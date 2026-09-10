from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from eth_hash.auto import keccak

from .rpc import RpcClient

RPC = "https://mainnet.megaeth.com/rpc"
PROXY = "0x12759afca690637b425ffba3265f0dc2f6242a8d"
FUNCTION_SELECTORS = ("0x21d5c9bb", "0x93ca625d")
EVENT_TOPICS = (
    "0x163aa6d2d8e5248ce93dbd22509af93e5f27f8e0edc15c9e26f2867c593e0870",
    "0x90cacdb710d0777a8e5fb6387c414be82e2ffe362a703077117a2a995fce12c1",
    "0x9f039a0ca58d6157d7b6914e2d60cedacf65fea21a365e93d708a5e5c25454f3",
)
USER_AGENT = "AGIA-TRADING-semantic-adjudicator/1.1"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read())


def signature_hash(signature: str) -> str:
    return "0x" + keccak(signature.encode("utf-8")).hex()


def function_selector(signature: str) -> str:
    return signature_hash(signature)[:10]


def _split_types(signature: str) -> list[str]:
    start = signature.find("(")
    end = signature.rfind(")")
    if start < 0 or end < start:
        return []
    body = signature[start + 1 : end]
    if not body:
        return []
    out: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "," and depth == 0:
            out.append("".join(current))
            current = []
            continue
        current.append(char)
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
    out.append("".join(current))
    return out


def _is_dynamic(abi_type: str) -> bool:
    if abi_type in {"bytes", "string"}:
        return True
    return abi_type.endswith("[]")


def signature_shape_compatible(signature: str, calldata_hex: str) -> bool:
    raw = calldata_hex.removeprefix("0x")
    if len(raw) < 8 or (len(raw) - 8) % 64:
        return False
    words = (len(raw) - 8) // 64
    types = _split_types(signature)
    if words < len(types):
        return False
    for index, abi_type in enumerate(types):
        if not _is_dynamic(abi_type):
            continue
        word = raw[8 + index * 64 : 8 + (index + 1) * 64]
        offset = int(word, 16)
        if offset % 32 or offset // 32 >= words:
            return False
    return True


def event_shape_compatible(signature: str, log: dict) -> bool:
    types = _split_types(signature)
    data = log.get("data", "0x").removeprefix("0x")
    if len(data) % 64:
        return False
    indexed_count = max(len(log.get("topics", [])) - 1, 0)
    data_words = len(data) // 64
    if any(_is_dynamic(abi_type) for abi_type in types):
        return len(types) >= indexed_count
    return len(types) == indexed_count + data_words


def openchain_lookup(identifier: str, kind: str) -> list[str]:
    query = urllib.parse.urlencode({kind: identifier, "filter": "true"})
    payload = _get_json(f"https://api.openchain.xyz/signature-database/v1/lookup?{query}")
    entries = payload.get("result", {}).get(kind, {}).get(identifier, []) or []
    return sorted({entry["name"] for entry in entries if entry.get("name")})


def fourbyte_lookup(identifier: str, kind: str) -> list[str]:
    resource = "signatures" if kind == "function" else "event-signatures"
    query = urllib.parse.urlencode({"hex_signature": identifier})
    try:
        payload = _get_json(f"https://www.4byte.directory/api/v1/{resource}/?{query}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return []
    return sorted(
        {
            entry["text_signature"]
            for entry in payload.get("results", [])
            if entry.get("text_signature")
        }
    )


def discover_samples(rpc: RpcClient, max_blocks: int = 1000) -> dict[str, list[dict]]:
    latest = int(rpc.call("eth_blockNumber", []), 16)
    found: dict[str, list[dict]] = {selector: [] for selector in FUNCTION_SELECTORS}
    for block_number in range(latest, max(latest - max_blocks, 0), -1):
        block = rpc.call("eth_getBlockByNumber", [hex(block_number), True])
        for tx in block.get("transactions", []):
            to = (tx.get("to") or "").lower()
            calldata = tx.get("input", "0x").lower()
            selector = calldata[:10]
            if to != PROXY or selector not in found or len(found[selector]) >= 2:
                continue
            receipt = rpc.call("eth_getTransactionReceipt", [tx["hash"]])
            if receipt is None:
                continue
            logs = [
                {
                    "address": log["address"].lower(),
                    "topics": [topic.lower() for topic in log.get("topics", [])],
                    "data": log.get("data", "0x").lower(),
                }
                for log in receipt.get("logs", [])
            ]
            found[selector].append(
                {
                    "tx_hash": tx["hash"].lower(),
                    "block_number": block_number,
                    "calldata": calldata,
                    "calldata_bytes": (len(calldata) - 2) // 2,
                    "receipt_status": int(receipt["status"], 16),
                    "logs": logs,
                }
            )
        if all(len(samples) >= 2 for samples in found.values()):
            break
    if any(not samples for samples in found.values()):
        raise RuntimeError(f"could not discover samples for all selectors: {found}")
    return found


def _event_occurrences(samples: dict[str, list[dict]], topic: str) -> list[dict]:
    return [
        log
        for selector_samples in samples.values()
        for sample in selector_samples
        for log in sample["logs"]
        if log.get("topics") and log["topics"][0] == topic
    ]


def adjudicate(endpoint: str = RPC) -> dict:
    rpc = RpcClient(endpoint)
    rpc.verify_chain_id(4326)
    samples = discover_samples(rpc)

    functions: dict[str, dict] = {}
    for selector in FUNCTION_SELECTORS:
        openchain = openchain_lookup(selector, "function")
        fourbyte = fourbyte_lookup(selector, "function")
        hash_verified = sorted(
            signature
            for signature in set(openchain) | set(fourbyte)
            if function_selector(signature) == selector
        )
        intersection = sorted(set(openchain) & set(fourbyte))
        compatible = [
            signature
            for signature in intersection
            if signature in hash_verified
            and all(
                signature_shape_compatible(signature, sample["calldata"])
                for sample in samples[selector]
            )
        ]
        if len(compatible) == 1:
            status = "CORROBORATED_SIGNATURE_CANDIDATE"
        elif len(compatible) > 1:
            status = "COLLISION"
        else:
            status = "UNKNOWN"
        functions[selector] = {
            "status": status,
            "openchain_candidates": openchain,
            "fourbyte_candidates": fourbyte,
            "hash_verified_candidates": hash_verified,
            "cross_database_shape_compatible": compatible,
            "semantic_verified": False,
            "samples": samples[selector],
        }

    events: dict[str, dict] = {}
    for topic in EVENT_TOPICS:
        openchain = openchain_lookup(topic, "event")
        fourbyte = fourbyte_lookup(topic, "event")
        candidates = sorted(set(openchain) | set(fourbyte))
        hash_verified = [signature for signature in candidates if signature_hash(signature) == topic]
        occurrences = _event_occurrences(samples, topic)
        shape_verified = [
            signature
            for signature in hash_verified
            if occurrences and all(event_shape_compatible(signature, log) for log in occurrences)
        ]
        cross_database = sorted(set(openchain) & set(fourbyte))
        signature_verified = len(shape_verified) == 1
        if signature_verified:
            status = "SIGNATURE_AND_SHAPE_VERIFIED"
        elif len(shape_verified) > 1:
            status = "COLLISION"
        else:
            status = "UNKNOWN"
        events[topic] = {
            "status": status,
            "openchain_candidates": openchain,
            "fourbyte_candidates": fourbyte,
            "cross_database_candidates": cross_database,
            "hash_verified_candidates": hash_verified,
            "shape_verified_candidates": shape_verified,
            "observed_occurrences": len(occurrences),
            "signature_verified": signature_verified,
            "economic_role_verified": False,
        }

    return {
        "gate": "P0-EUPHORIA-SEMANTIC-ADJUDICATION-001",
        "chain_id": 4326,
        "source_endpoint": endpoint,
        "functions": functions,
        "events": events,
        "rule": (
            "A cryptographically verified event signature is not sufficient to prove its "
            "economic role. Function semantics, oracle, settlement, payout and fees remain "
            "fail-closed until deterministic behavioral/accounting proof exists."
        ),
        "oracle_dependency_verified": False,
        "settlement_semantics_verified": False,
        "fee_payout_semantics_verified": False,
        "backfill_authorized": False,
        "live_authorized": False,
        "max_autonomous_capital_usd": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", default=RPC)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = adjudicate(args.rpc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
