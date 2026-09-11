from __future__ import annotations

import argparse
import json
from pathlib import Path

from eth_hash.auto import keccak
from eth_keys import keys

from .accounting import PROXY, RPC, _collect_recent_transactions
from .rpc import RpcClient


def _selector(signature: str) -> str:
    return "0x" + keccak(signature.encode("utf-8"))[:4].hex()


def _uint_call(rpc: RpcClient, signature: str, encoded_args: bytes = b"") -> int:
    data = _selector(signature) + encoded_args.hex()
    result = rpc.call("eth_call", [{"to": PROXY, "data": data}, "latest"])
    return int(result, 16)


def _string_call(rpc: RpcClient, signature: str) -> str:
    result = rpc.call("eth_call", [{"to": PROXY, "data": _selector(signature)}, "latest"])
    raw = bytes.fromhex(result.removeprefix("0x"))
    if len(raw) < 64:
        raise ValueError("short ABI string response")
    offset = int.from_bytes(raw[:32], "big")
    if offset + 32 > len(raw):
        raise ValueError("invalid ABI string offset")
    length = int.from_bytes(raw[offset : offset + 32], "big")
    start = offset + 32
    end = start + length
    if end > len(raw):
        raise ValueError("invalid ABI string length")
    return raw[start:end].decode("utf-8")


def recover_signer(signed_hash: str, signature: str) -> str:
    sig = bytes.fromhex(signature.removeprefix("0x"))
    if len(sig) != 65:
        raise ValueError("RedStone signature must be 65 bytes")
    recovery_id = sig[64]
    if recovery_id not in (27, 28):
        raise ValueError("RedStone signature recovery id must be 27 or 28")
    normalized = sig[:64] + bytes([recovery_id - 27])
    public_key = keys.Signature(signature_bytes=normalized).recover_public_key_from_msg_hash(
        bytes.fromhex(signed_hash.removeprefix("0x"))
    )
    return public_key.to_checksum_address().lower()


def _encode_address(address: str) -> bytes:
    raw = bytes.fromhex(address.removeprefix("0x"))
    if len(raw) != 20:
        raise ValueError("address must be 20 bytes")
    return (b"\x00" * 12) + raw


def collect(endpoint: str = RPC) -> dict:
    rpc = RpcClient(endpoint)
    rpc.verify_chain_id(4326)
    transactions = _collect_recent_transactions(rpc, max_blocks=2500, samples_per_selector=3)

    packages: list[dict] = []
    for tx in transactions:
        redstone = tx.get("redstone")
        if redstone is None:
            continue
        for package in redstone["packages"]:
            signer = recover_signer(package["signed_message_keccak256"], package["signature"])
            packages.append(
                {
                    "tx_hash": tx["tx_hash"],
                    "block_number": tx["block_number"],
                    "selector": tx["selector"],
                    "timestamp_ms": package["timestamp_ms"],
                    "feed_ids": [point["feed_id"] for point in package["data_points"]],
                    "signed_message_keccak256": package["signed_message_keccak256"],
                    "signer": signer,
                    "signature_recovered": True,
                }
            )

    unique_signers = sorted({package["signer"] for package in packages})
    policy_errors: list[str] = []

    try:
        threshold = _uint_call(rpc, "getUniqueSignersThreshold()")
    except (RuntimeError, ValueError) as exc:
        threshold = None
        policy_errors.append(f"getUniqueSignersThreshold:{type(exc).__name__}")

    data_service_id: str | None
    try:
        data_service_id = _string_call(rpc, "getDataServiceId()")
    except (RuntimeError, ValueError, UnicodeDecodeError) as exc:
        data_service_id = None
        policy_errors.append(f"getDataServiceId:{type(exc).__name__}")

    signer_policy: list[dict] = []
    for signer in unique_signers:
        try:
            signer_index = _uint_call(
                rpc,
                "getAuthorisedSignerIndex(address)",
                _encode_address(signer),
            )
            signer_policy.append(
                {
                    "signer": signer,
                    "authorised_by_contract": True,
                    "signer_index": signer_index,
                }
            )
        except (RuntimeError, ValueError) as exc:
            signer_policy.append(
                {
                    "signer": signer,
                    "authorised_by_contract": False,
                    "error": type(exc).__name__,
                }
            )

    all_observed_signers_authorised = bool(signer_policy) and all(
        item["authorised_by_contract"] for item in signer_policy
    )
    threshold_satisfied_by_observed_packages = (
        threshold is not None and threshold > 0 and len(unique_signers) >= threshold
    )
    policy_verified_for_sample = (
        bool(packages)
        and all(package["signature_recovered"] for package in packages)
        and all_observed_signers_authorised
        and threshold_satisfied_by_observed_packages
    )

    unresolved = []
    if not policy_verified_for_sample:
        unresolved.append("redstone_observed_signer_policy")
    unresolved.append("historical_redstone_signer_policy_completeness")
    if data_service_id is None:
        unresolved.append("euphoria_redstone_data_service_id")

    return {
        "gate": "P0-EUPHORIA-REDSTONE-SIGNERS-001",
        "status": "PARTIAL_EVIDENCE",
        "chain_id": 4326,
        "source_endpoint": endpoint,
        "proxy": PROXY,
        "function_selectors": {
            "getUniqueSignersThreshold()": _selector("getUniqueSignersThreshold()"),
            "getAuthorisedSignerIndex(address)": _selector("getAuthorisedSignerIndex(address)"),
            "getDataServiceId()": _selector("getDataServiceId()"),
        },
        "unique_signers_threshold": threshold,
        "data_service_id": data_service_id,
        "recovered_packages": packages,
        "unique_recovered_signers": unique_signers,
        "signer_policy": signer_policy,
        "all_observed_signers_authorised": all_observed_signers_authorised,
        "threshold_satisfied_by_observed_packages": threshold_satisfied_by_observed_packages,
        "sample_signer_policy_verified": policy_verified_for_sample,
        "policy_query_errors": policy_errors,
        "unresolved": unresolved,
        "unresolved_count": len(unresolved),
        "backfill_authorized": False,
        "paper_authorized": False,
        "testnet_authorized": False,
        "live_authorized": False,
        "max_autonomous_capital_usd": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", default=RPC)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence = collect(args.rpc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
