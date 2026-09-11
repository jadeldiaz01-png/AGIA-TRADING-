from __future__ import annotations

import argparse
import json
from pathlib import Path

from eth_hash.auto import keccak

from .rpc import RpcClient

RPC = "https://mainnet.megaeth.com/rpc"
PROXY = "0x12759afca690637b425ffba3265f0dc2f6242a8d"
USDM = "0xfafddbb3fc7688494971a79cc65dca3ef82079e7"
FUNCTION_SELECTORS = {"0x21d5c9bb", "0x93ca625d"}
BALANCE_UPDATE_TOPIC = "0x163aa6d2d8e5248ce93dbd22509af93e5f27f8e0edc15c9e26f2867c593e0870"
UNKNOWN_EVENT_TOPICS = {
    "0x90cacdb710d0777a8e5fb6387c414be82e2ffe362a703077117a2a995fce12c1",
    "0x9f039a0ca58d6157d7b6914e2d60cedacf65fea21a365e93d708a5e5c25454f3",
}
TRANSFER_TOPIC = "0x" + keccak(b"Transfer(address,address,uint256)").hex()
REDSTONE_MARKER = bytes.fromhex("000002ed57011e0000")


def _decode_feed_id(raw: bytes) -> str:
    stripped = raw.rstrip(b"\x00")
    if stripped and all(32 <= byte < 127 for byte in stripped):
        return stripped.decode("ascii")
    return "0x" + raw.hex()


def _topic_address(topic: str) -> str:
    raw = topic.removeprefix("0x")
    if len(raw) != 64:
        raise ValueError("indexed address topic must be 32 bytes")
    return "0x" + raw[-40:].lower()


def _data_words(data: str) -> list[int]:
    raw = bytes.fromhex(data.removeprefix("0x"))
    if len(raw) % 32:
        return []
    return [int.from_bytes(raw[index : index + 32], "big") for index in range(0, len(raw), 32)]


def parse_redstone_payload(calldata: str) -> dict | None:
    raw = bytes.fromhex(calldata.removeprefix("0x"))
    if len(raw) < len(REDSTONE_MARKER) + 3 + 2 or not raw.endswith(REDSTONE_MARKER):
        return None

    marker_start = len(raw) - len(REDSTONE_MARKER)
    metadata_size_start = marker_start - 3
    unsigned_metadata_size = int.from_bytes(raw[metadata_size_start:marker_start], "big")
    metadata_start = metadata_size_start - unsigned_metadata_size
    count_start = metadata_start - 2
    if count_start < 0:
        raise ValueError("invalid RedStone unsigned metadata size")

    package_count = int.from_bytes(raw[count_start:metadata_start], "big")
    if package_count < 1:
        raise ValueError("RedStone payload must contain at least one package")

    cursor = count_start
    reverse_packages: list[dict] = []
    for _ in range(package_count):
        signature_end = cursor
        signature_start = signature_end - 65
        count_field_start = signature_start - 3
        value_size_start = count_field_start - 4
        timestamp_start = value_size_start - 6
        if timestamp_start < 0:
            raise ValueError("truncated RedStone package header")

        data_points_count = int.from_bytes(raw[count_field_start:signature_start], "big")
        value_size = int.from_bytes(raw[value_size_start:count_field_start], "big")
        if data_points_count < 1 or value_size < 1 or value_size > 32:
            raise ValueError("invalid RedStone data point dimensions")

        points_size = data_points_count * (32 + value_size)
        package_start = timestamp_start - points_size
        if package_start < 0:
            raise ValueError("truncated RedStone data points")

        points: list[dict] = []
        point_cursor = package_start
        for _ in range(data_points_count):
            feed_raw = raw[point_cursor : point_cursor + 32]
            point_cursor += 32
            value_raw = raw[point_cursor : point_cursor + value_size]
            point_cursor += value_size
            points.append(
                {
                    "feed_id": _decode_feed_id(feed_raw),
                    "feed_id_hex": "0x" + feed_raw.hex(),
                    "value_raw": int.from_bytes(value_raw, "big"),
                    "value_byte_size": value_size,
                }
            )

        timestamp_ms = int.from_bytes(raw[timestamp_start:value_size_start], "big")
        signed_message = raw[package_start:signature_start]
        signature = raw[signature_start:signature_end]
        reverse_packages.append(
            {
                "timestamp_ms": timestamp_ms,
                "data_points": points,
                "data_points_count": data_points_count,
                "value_byte_size": value_size,
                "signature": "0x" + signature.hex(),
                "signature_bytes": len(signature),
                "signed_message_keccak256": "0x" + keccak(signed_message).hex(),
                "signature_crypto_verified": False,
            }
        )
        cursor = package_start

    packages = list(reversed(reverse_packages))
    timestamps = {package["timestamp_ms"] for package in packages}
    return {
        "marker_verified": True,
        "marker_hex": "0x" + REDSTONE_MARKER.hex(),
        "payload_start_byte": cursor,
        "business_calldata": "0x" + raw[:cursor].hex(),
        "business_calldata_bytes": cursor,
        "unsigned_metadata": "0x" + raw[metadata_start:metadata_size_start].hex(),
        "unsigned_metadata_size": unsigned_metadata_size,
        "package_count": package_count,
        "timestamps_equal": len(timestamps) == 1,
        "packages": packages,
    }


def parse_receipt_logs(logs: list[dict]) -> dict:
    transfers: list[dict] = []
    balance_updates: list[dict] = []
    unknown_events: list[dict] = []

    for log in logs:
        address = (log.get("address") or "").lower()
        topics = [topic.lower() for topic in log.get("topics", [])]
        data = (log.get("data") or "0x").lower()
        if not topics:
            continue

        if address == USDM and topics[0] == TRANSFER_TOPIC and len(topics) >= 3:
            words = _data_words(data)
            if len(words) == 1:
                transfers.append(
                    {
                        "from": _topic_address(topics[1]),
                        "to": _topic_address(topics[2]),
                        "amount_raw": words[0],
                    }
                )
            continue

        if address == PROXY and topics[0] == BALANCE_UPDATE_TOPIC and len(topics) >= 2:
            words = _data_words(data)
            if len(words) == 1:
                balance_updates.append(
                    {
                        "account": _topic_address(topics[1]),
                        "value_raw": words[0],
                        "economic_role_verified": False,
                    }
                )
            continue

        if address == PROXY and topics[0] in UNKNOWN_EVENT_TOPICS:
            unknown_events.append(
                {
                    "topic0": topics[0],
                    "indexed_topics": topics[1:],
                    "indexed_addresses": [
                        _topic_address(topic) for topic in topics[1:] if len(topic) == 66
                    ],
                    "data": data,
                    "data_words": _data_words(data),
                    "economic_role_verified": False,
                }
            )

    usdm_in_raw = sum(item["amount_raw"] for item in transfers if item["to"] == PROXY)
    usdm_out_raw = sum(item["amount_raw"] for item in transfers if item["from"] == PROXY)
    return {
        "usdm_transfers": transfers,
        "balance_updates": balance_updates,
        "unknown_events": unknown_events,
        "usdm_in_raw": usdm_in_raw,
        "usdm_out_raw": usdm_out_raw,
        "proxy_net_inflow_raw": usdm_in_raw - usdm_out_raw,
    }


def _collect_recent_transactions(
    rpc: RpcClient, max_blocks: int = 2500, samples_per_selector: int = 3
) -> list[dict]:
    latest = int(rpc.call("eth_blockNumber", []), 16)
    found: dict[str, list[dict]] = {selector: [] for selector in FUNCTION_SELECTORS}

    for block_number in range(latest, max(latest - max_blocks, 0), -1):
        block = rpc.call("eth_getBlockByNumber", [hex(block_number), True])
        for tx in block.get("transactions", []):
            to = (tx.get("to") or "").lower()
            calldata = (tx.get("input") or "0x").lower()
            selector = calldata[:10]
            if to != PROXY or selector not in found or len(found[selector]) >= samples_per_selector:
                continue
            receipt = rpc.call("eth_getTransactionReceipt", [tx["hash"]])
            if not receipt or int(receipt.get("status", "0x0"), 16) != 1:
                continue
            parsed_logs = parse_receipt_logs(receipt.get("logs", []))
            redstone = parse_redstone_payload(calldata)
            found[selector].append(
                {
                    "tx_hash": tx["hash"].lower(),
                    "block_number": block_number,
                    "from": (tx.get("from") or "").lower(),
                    "selector": selector,
                    "calldata_bytes": (len(calldata) - 2) // 2,
                    "redstone": redstone,
                    **parsed_logs,
                }
            )
        if all(len(items) >= samples_per_selector for items in found.values()):
            break

    if any(not items for items in found.values()):
        raise RuntimeError(f"could not discover both selector families: {found}")
    return [item for selector in sorted(found) for item in found[selector]]


def collect(endpoint: str = RPC) -> dict:
    rpc = RpcClient(endpoint)
    rpc.verify_chain_id(4326)
    transactions = _collect_recent_transactions(rpc)

    feed_ids = sorted(
        {
            point["feed_id"]
            for tx in transactions
            if tx["redstone"]
            for package in tx["redstone"]["packages"]
            for point in package["data_points"]
        }
    )
    unknown_topic_counts = {
        topic: sum(
            1
            for tx in transactions
            for event in tx["unknown_events"]
            if event["topic0"] == topic
        )
        for topic in sorted(UNKNOWN_EVENT_TOPICS)
    }
    unresolved = [
        "selector_0x21d5c9bb_economic_role",
        "selector_0x93ca625d_economic_role",
        "balance_update_economic_role",
        "event_0x90cacdb_economic_role",
        "event_0x9f039a0_economic_role",
        "redstone_authorised_signer_policy",
        "settlement_lifecycle_linkage",
        "gross_payout_formula",
        "protocol_fee_formula",
        "net_payout_and_pnl_identity",
    ]

    return {
        "gate": "P0-EUPHORIA-ACCOUNTING-001",
        "status": "PARTIAL_EVIDENCE",
        "chain_id": 4326,
        "source_endpoint": endpoint,
        "protocol": {
            "proxy": PROXY,
            "asset": {"symbol": "USDm", "address": USDM},
            "redstone_marker": "0x" + REDSTONE_MARKER.hex(),
            "redstone_transport_layout_verified_against_official_source": True,
        },
        "observed_feed_ids": feed_ids,
        "expected_euphoria_feed_ids_documented_by_provider": ["ETH", "ETH_HIGH", "ETH_LOW"],
        "unknown_event_occurrences": unknown_topic_counts,
        "transactions": transactions,
        "accounting_invariants": {
            "gross_payout_equals_principal_plus_gross_profit": "UNRESOLVED",
            "net_payout_equals_gross_payout_minus_fees": "UNRESOLVED",
            "net_pnl_equals_net_payout_minus_stake": "UNRESOLVED",
            "wallet_equity_reconciliation": "UNRESOLVED",
            "cashflow_observation": "VERIFIED_AT_TRANSACTION_LEVEL",
        },
        "unresolved": unresolved,
        "unresolved_count": len(unresolved),
        "oracle_payload_structurally_parsed": any(tx["redstone"] for tx in transactions),
        "oracle_signatures_cryptographically_verified": False,
        "settlement_semantics_verified": False,
        "fee_payout_semantics_verified": False,
        "deterministic_pnl_verified": False,
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
