from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contract_graph import (
    ERC1967_IMPLEMENTATION_SLOT,
    implementation_from_storage_word,
    runtime_bytecode_sha256,
    verify_expected_implementation,
)
from .rpc import RpcClient

PROXY = "0x12759afca690637b425ffba3265f0dc2f6242a8d"
EXPECTED_IMPLEMENTATION = "0x95d2a2cb2e9f1efb89f435752bbc8ccf61c3485a"
DEFAULT_RPCS = [
    "https://mainnet.megaeth.com/rpc",
    "https://megaeth.drpc.org",
    "https://public.1rpc.io/megaeth",
]
UPGRADED_TOPIC0 = "0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b"
EXPECTED_RPC_ERRORS = (OSError, ValueError, RuntimeError)


def push4_candidates(bytecode: str) -> list[str]:
    raw = bytes.fromhex(bytecode.removeprefix("0x"))
    out: set[str] = set()
    i = 0
    while i < len(raw):
        opcode = raw[i]
        if opcode == 0x63 and i + 4 < len(raw):
            out.add("0x" + raw[i + 1 : i + 5].hex())
            i += 5
            continue
        if 0x60 <= opcode <= 0x7F:
            i += 1 + (opcode - 0x5F)
            continue
        i += 1
    return sorted(out)


def _upgrade_log_query(rpc: RpcClient, start: int, end: int) -> list[dict]:
    return rpc.call(
        "eth_getLogs",
        [
            {
                "address": PROXY,
                "fromBlock": hex(start),
                "toBlock": hex(end),
                "topics": [UPGRADED_TOPIC0],
            }
        ],
    )


def scan_upgrade_history(
    rpc: RpcClient,
    latest_block: int,
    initial_chunk: int = 1_000_000,
    min_chunk: int = 10_000,
) -> list[dict]:
    logs: list[dict] = []

    def scan(start: int, end: int) -> None:
        try:
            logs.extend(_upgrade_log_query(rpc, start, end))
        except EXPECTED_RPC_ERRORS:
            size = end - start + 1
            if size <= min_chunk:
                raise
            midpoint = start + size // 2 - 1
            scan(start, midpoint)
            scan(midpoint + 1, end)

    for start in range(0, latest_block + 1, initial_chunk):
        scan(start, min(start + initial_chunk - 1, latest_block))

    history = []
    for log in sorted(logs, key=lambda item: (int(item["blockNumber"], 16), int(item["logIndex"], 16))):
        topics = log.get("topics", [])
        if len(topics) < 2:
            raise RuntimeError("Upgraded log missing indexed implementation topic")
        implementation = "0x" + topics[1][-40:].lower()
        history.append(
            {
                "block_number": int(log["blockNumber"], 16),
                "transaction_hash": log["transactionHash"].lower(),
                "log_index": int(log["logIndex"], 16),
                "implementation": implementation,
            }
        )
    return history


def collect_from_endpoint(endpoint: str) -> dict:
    rpc = RpcClient(endpoint)
    rpc.verify_chain_id(4326)

    storage_word = rpc.call("eth_getStorageAt", [PROXY, ERC1967_IMPLEMENTATION_SLOT, "latest"])
    verify_expected_implementation(storage_word, EXPECTED_IMPLEMENTATION)
    observed_implementation = implementation_from_storage_word(storage_word)

    proxy_code = rpc.call("eth_getCode", [PROXY, "latest"])
    implementation_code = rpc.call("eth_getCode", [observed_implementation, "latest"])
    if proxy_code == "0x" or implementation_code == "0x":
        raise RuntimeError("proxy or implementation runtime bytecode is empty")

    latest_block = int(rpc.call("eth_blockNumber", []), 16)
    upgrade_history = scan_upgrade_history(rpc, latest_block)
    if upgrade_history and upgrade_history[-1]["implementation"] != observed_implementation:
        raise RuntimeError("latest Upgraded event disagrees with current ERC1967 implementation slot")

    return {
        "gate": "P0-EUPHORIA-CONTRACTS-001",
        "source_endpoint": endpoint,
        "chain_id": 4326,
        "scan_start_block": 0,
        "latest_block": latest_block,
        "proxy": PROXY,
        "proxy_standard": "ERC1967",
        "implementation_slot": ERC1967_IMPLEMENTATION_SLOT,
        "implementation_storage_word": storage_word,
        "implementation": observed_implementation,
        "expected_implementation": EXPECTED_IMPLEMENTATION,
        "implementation_matches_expected": True,
        "proxy_runtime_bytecode_sha256": runtime_bytecode_sha256(proxy_code),
        "implementation_runtime_bytecode_sha256": runtime_bytecode_sha256(implementation_code),
        "implementation_push4_candidates": push4_candidates(implementation_code),
        "known_erc1967_upgraded_topic0": UPGRADED_TOPIC0,
        "upgrade_history": upgrade_history,
        "upgrade_history_complete": True,
        "abi_verified": False,
        "event_semantics_verified": False,
        "oracle_dependency_verified": False,
        "settlement_semantics_verified": False,
        "fee_payout_semantics_verified": False,
        "p0_euphoria_contracts_001": "PARTIAL_EVIDENCE",
        "live_authorized": False,
        "max_autonomous_capital_usd": 0,
    }


def collect(endpoints: list[str]) -> dict:
    failures: list[dict[str, str]] = []
    for endpoint in endpoints:
        try:
            evidence = collect_from_endpoint(endpoint)
            evidence["rpc_failures_before_success"] = failures
            return evidence
        except EXPECTED_RPC_ERRORS as exc:
            failures.append({"endpoint": endpoint, "error": f"{type(exc).__name__}: {exc}"})
    raise RuntimeError(f"all MegaETH RPC endpoints failed: {failures}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", action="append", dest="rpcs")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    evidence = collect(args.rpcs or DEFAULT_RPCS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
