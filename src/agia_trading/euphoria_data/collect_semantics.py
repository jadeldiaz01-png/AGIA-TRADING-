from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contract_graph import runtime_bytecode_sha256
from .rpc import RpcClient

RPC = "https://mainnet.megaeth.com/rpc"
PROXY = "0x12759afca690637b425ffba3265f0dc2f6242a8d"
HISTORICAL_IMPLEMENTATION = "0xa9eefd86a4ef1cc1b72eddc6da3b2c0b40016cbb"
CURRENT_IMPLEMENTATION = "0x95d2a2cb2e9f1efb89f435752bbc8ccf61c3485a"
TARGET_SELECTORS = {"0x21d5c9bb", "0x93ca625d"}
MAX_SCAN_BLOCKS = 2000


def push4_candidates(bytecode: str) -> list[str]:
    raw = bytes.fromhex(bytecode.removeprefix("0x"))
    found: set[str] = set()
    i = 0
    while i < len(raw):
        opcode = raw[i]
        if opcode == 0x63 and i + 4 < len(raw):
            found.add("0x" + raw[i + 1 : i + 5].hex())
            i += 5
        elif 0x60 <= opcode <= 0x7F:
            i += 1 + opcode - 0x5F
        else:
            i += 1
    return sorted(found)


def _log_projection(log: dict) -> dict:
    return {
        "address": log["address"].lower(),
        "topics": [topic.lower() for topic in log.get("topics", [])],
        "data": log.get("data", "0x").lower(),
        "log_index": int(log["logIndex"], 16),
    }


def _discover_samples(rpc: RpcClient, latest_block: int) -> tuple[dict[str, dict], int]:
    found: dict[str, dict] = {}
    scanned = 0
    for block_number in range(latest_block, max(-1, latest_block - MAX_SCAN_BLOCKS), -1):
        scanned += 1
        block = rpc.call("eth_getBlockByNumber", [hex(block_number), True])
        if block is None:
            continue
        for tx in block.get("transactions", []):
            to_address = tx.get("to")
            tx_input = tx.get("input", "0x")
            if not to_address or to_address.lower() != PROXY or len(tx_input) < 10:
                continue
            selector = tx_input[:10].lower()
            if selector in TARGET_SELECTORS and selector not in found:
                found[selector] = tx
        if set(found) == TARGET_SELECTORS:
            break
    missing = TARGET_SELECTORS - set(found)
    if missing:
        raise RuntimeError(
            f"could not discover live proxy transactions for selectors {sorted(missing)} "
            f"within {scanned} blocks ending at {latest_block}"
        )
    return found, scanned


def collect(endpoint: str = RPC) -> dict:
    rpc = RpcClient(endpoint)
    rpc.verify_chain_id(4326)

    historical_code = rpc.call("eth_getCode", [HISTORICAL_IMPLEMENTATION, "latest"])
    current_code = rpc.call("eth_getCode", [CURRENT_IMPLEMENTATION, "latest"])
    if historical_code == "0x" or current_code == "0x":
        raise RuntimeError("historical or current implementation code is unavailable")

    historical_candidates = push4_candidates(historical_code)
    current_candidates = push4_candidates(current_code)
    for selector in TARGET_SELECTORS:
        if selector not in current_candidates:
            raise RuntimeError(f"target selector is absent from current implementation bytecode: {selector}")

    latest_block = int(rpc.call("eth_blockNumber", []), 16)
    discovered, scanned_blocks = _discover_samples(rpc, latest_block)

    samples = []
    all_log_addresses: set[str] = set()
    all_topic0: set[str] = set()
    for expected_selector in sorted(TARGET_SELECTORS):
        tx = discovered[expected_selector]
        tx_hash = tx["hash"].lower()
        receipt = rpc.call("eth_getTransactionReceipt", [tx_hash])
        if receipt is None:
            raise RuntimeError(f"missing receipt for discovered transaction: {tx_hash}")
        selector = tx["input"][:10].lower()
        if selector != expected_selector:
            raise RuntimeError(
                f"discovered selector mismatch for {tx_hash}: expected {expected_selector}, got {selector}"
            )
        logs = [_log_projection(log) for log in receipt.get("logs", [])]
        for log in logs:
            all_log_addresses.add(log["address"])
            if log["topics"]:
                all_topic0.add(log["topics"][0])
        samples.append(
            {
                "tx_hash": tx_hash,
                "selector": selector,
                "block_number": int(tx["blockNumber"], 16),
                "from": tx["from"].lower(),
                "to": tx["to"].lower(),
                "value_wei": int(tx["value"], 16),
                "calldata_bytes": (len(tx["input"]) - 2) // 2,
                "calldata": tx["input"].lower(),
                "receipt_status": int(receipt["status"], 16),
                "gas_used": int(receipt["gasUsed"], 16),
                "log_count": len(logs),
                "logs": logs,
            }
        )

    return {
        "gate": "P0-EUPHORIA-SEMANTICS-001",
        "status": "PARTIAL_EVIDENCE",
        "source_endpoint": endpoint,
        "chain_id": 4326,
        "proxy": PROXY,
        "latest_block": latest_block,
        "sample_discovery": {
            "method": "eth_getBlockByNumber(full_transactions=true)",
            "scanned_blocks": scanned_blocks,
            "max_scan_blocks": MAX_SCAN_BLOCKS,
            "target_selectors": sorted(TARGET_SELECTORS),
            "preselected_transaction_hashes": False,
        },
        "historical_implementation": HISTORICAL_IMPLEMENTATION,
        "historical_runtime_bytecode_sha256": runtime_bytecode_sha256(historical_code),
        "historical_push4_candidates": historical_candidates,
        "current_implementation": CURRENT_IMPLEMENTATION,
        "current_runtime_bytecode_sha256": runtime_bytecode_sha256(current_code),
        "current_push4_candidates": current_candidates,
        "samples": samples,
        "observed_log_addresses": sorted(all_log_addresses),
        "observed_topic0": sorted(all_topic0),
        "selector_semantics_verified": False,
        "event_semantics_verified": False,
        "oracle_dependency_verified": False,
        "settlement_semantics_verified": False,
        "fee_payout_semantics_verified": False,
        "live_authorized": False,
        "max_autonomous_capital_usd": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", default=RPC)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = collect(args.rpc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
