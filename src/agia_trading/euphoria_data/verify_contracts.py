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
DEFAULT_RPC = "https://mainnet.megaeth.com/rpc"
UPGRADED_TOPIC0 = "0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b"


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


def collect(endpoint: str) -> dict:
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
    return {
        "gate": "P0-EUPHORIA-CONTRACTS-001",
        "chain_id": 4326,
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
        "upgrade_history_complete": False,
        "abi_verified": False,
        "event_semantics_verified": False,
        "oracle_dependency_verified": False,
        "settlement_semantics_verified": False,
        "fee_payout_semantics_verified": False,
        "p0_euphoria_contracts_001": "PARTIAL_EVIDENCE",
        "live_authorized": False,
        "max_autonomous_capital_usd": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", default=DEFAULT_RPC)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    evidence = collect(args.rpc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
