from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .historical_lifecycle import (
    BLOCKSCOUT_LOGS_RPC,
    DEFAULT_FROM_BLOCK,
    INFLOW_EVENT,
    OUTFLOW_EVENT,
    RPC,
    adaptive_get_logs,
    build_structural_index,
    enrich_pairs,
)
from .rpc import RpcClient


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def scan_shard(
    index: int,
    from_block: int,
    to_block: int,
    logs_endpoint: str = BLOCKSCOUT_LOGS_RPC,
) -> dict:
    if from_block > to_block:
        raise ValueError("from_block must be <= to_block")
    logs_rpc = RpcClient(logs_endpoint, user_agent="AGIA-TRADING-HISTORICAL-SHARD/1.0 read-only")
    logs_rpc.verify_chain_id(4326)
    logs, windows = adaptive_get_logs(
        logs_rpc,
        [INFLOW_EVENT, OUTFLOW_EVENT],
        from_block,
        to_block,
    )
    payload = {
        "shard_index": index,
        "from_block": from_block,
        "to_block": to_block,
        "historical_log_index": logs_endpoint,
        "logs": logs,
        "log_count": len(logs),
        "windows": windows,
    }
    payload["payload_sha256"] = _digest({key: value for key, value in payload.items() if key != "payload_sha256"})
    return payload


def _verify_shard(shard: dict) -> None:
    expected = shard.get("payload_sha256")
    actual = _digest({key: value for key, value in shard.items() if key != "payload_sha256"})
    if expected != actual:
        raise RuntimeError(
            f"shard digest mismatch index={shard.get('shard_index')} expected={expected} actual={actual}"
        )
    if shard.get("log_count") != len(shard.get("logs", [])):
        raise RuntimeError(f"shard row-count mismatch index={shard.get('shard_index')}")


def aggregate_shards(
    shard_files: list[Path],
    expected_from: int,
    expected_to: int,
    endpoint: str = RPC,
    max_enriched_pairs: int = 16,
) -> dict:
    if not shard_files:
        raise RuntimeError("no shard files supplied")
    shards = [json.loads(path.read_text(encoding="utf-8")) for path in shard_files]
    for shard in shards:
        _verify_shard(shard)
    shards.sort(key=lambda item: (item["from_block"], item["shard_index"]))

    duplicate_indexes = len({item["shard_index"] for item in shards}) != len(shards)
    coverage_errors: list[dict] = []
    cursor = expected_from
    for shard in shards:
        if shard["from_block"] != cursor:
            coverage_errors.append(
                {
                    "expected_from": cursor,
                    "observed_from": shard["from_block"],
                    "shard_index": shard["shard_index"],
                }
            )
        cursor = shard["to_block"] + 1
    if cursor - 1 != expected_to:
        coverage_errors.append(
            {"expected_final_to": expected_to, "observed_final_to": cursor - 1}
        )
    if duplicate_indexes:
        coverage_errors.append({"duplicate_shard_indexes": True})
    if coverage_errors:
        raise RuntimeError(f"historical shard coverage invalid: {coverage_errors}")

    sources = {item["historical_log_index"] for item in shards}
    if len(sources) != 1:
        raise RuntimeError(f"mixed historical index sources are not allowed: {sorted(sources)}")

    logs = [log for shard in shards for log in shard["logs"]]
    identities = [
        (log["transaction_hash"], log["log_index"], log["topic0"])
        for log in logs
    ]
    duplicate_log_count = len(identities) - len(set(identities))
    if duplicate_log_count:
        raise RuntimeError(f"duplicate logs across shards: {duplicate_log_count}")

    logs.sort(key=lambda item: (item["block_number"], item["log_index"]))
    openings = [item for item in logs if item["topic0"] == INFLOW_EVENT]
    closings = [item for item in logs if item["topic0"] == OUTFLOW_EVENT]
    unexpected_topics = sorted(
        {item["topic0"] for item in logs if item["topic0"] not in {INFLOW_EVENT, OUTFLOW_EVENT}}
    )
    if unexpected_topics:
        raise RuntimeError(f"unexpected lifecycle topics: {unexpected_topics}")

    structural = build_structural_index(openings, closings)
    rpc = RpcClient(endpoint)
    rpc.verify_chain_id(4326)
    coverage = enrich_pairs(rpc, structural["pairs"], max_enriched_pairs)
    full_economic_coverage = coverage["coverage_is_full"]
    all_observed_clean = (
        coverage["receipt_missing_count_observed"] == 0
        and coverage["oracle_payload_missing_count_observed"] == 0
        and coverage["amount_reconciliation_errors_observed"] == 0
        and coverage["selector_mismatch_count_observed"] == 0
    )

    unresolved: list[str] = []
    if structural["correlation_key_uniqueness"] != "PASS":
        unresolved.append("correlation_key_uniqueness")
    if structural["orphan_opening_count"]:
        unresolved.append("orphan_openings")
    if structural["orphan_closing_count"]:
        unresolved.append("orphan_closings")
    if structural["duplicate_lifecycle_count"]:
        unresolved.append("duplicate_lifecycles")
    if structural["ordering_error_count"]:
        unresolved.append("lifecycle_ordering")
    if not full_economic_coverage:
        unresolved.extend(
            [
                "historical_receipt_coverage",
                "historical_oracle_payload_coverage",
                "historical_amount_reconciliation_coverage",
            ]
        )
    elif not all_observed_clean:
        unresolved.append("historical_economic_evidence_errors")
    unresolved.extend(
        [
            "opening_event_economic_role",
            "closing_event_economic_role",
            "balance_update_economic_role",
            "gross_payout_formula",
            "protocol_fee_formula",
            "net_payout_formula",
            "deterministic_pnl_identity",
        ]
    )

    shard_manifest = [
        {
            "shard_index": item["shard_index"],
            "from_block": item["from_block"],
            "to_block": item["to_block"],
            "log_count": item["log_count"],
            "payload_sha256": item["payload_sha256"],
            "window_count": len(item.get("windows", [])),
        }
        for item in shards
    ]

    return {
        "gate": "P0-EUPHORIA-HISTORICAL-LIFECYCLE-001",
        "status": "PARTIAL_EVIDENCE" if unresolved else "PASS",
        "chain_id": 4326,
        "range": {"from_block": expected_from, "to_block": expected_to},
        "sources": {
            "authoritative_rpc": endpoint,
            "historical_log_index": next(iter(sources)),
            "historical_log_index_role": "discovery/index only; tx/receipt verification uses authoritative RPC",
        },
        "sharding": {
            "strategy": "contiguous_non_overlapping_block_ranges",
            "shard_count": len(shards),
            "coverage_gap_or_overlap_count": 0,
            "duplicate_log_count": 0,
            "manifest_sha256": _digest(shard_manifest),
            "manifest": shard_manifest,
        },
        "structural": {key: value for key, value in structural.items() if key != "pairs"},
        "economic_coverage": coverage,
        "scientific_targets": {
            "correlation_key_uniqueness": structural["correlation_key_uniqueness"],
            "orphan_opening_count": structural["orphan_opening_count"],
            "orphan_closing_count": structural["orphan_closing_count"],
            "duplicate_lifecycle_count": structural["duplicate_lifecycle_count"],
            "receipt_missing_count": (
                coverage["receipt_missing_count_observed"] if full_economic_coverage else None
            ),
            "oracle_payload_missing_count": (
                coverage["oracle_payload_missing_count_observed"] if full_economic_coverage else None
            ),
            "amount_reconciliation_errors": (
                coverage["amount_reconciliation_errors_observed"] if full_economic_coverage else None
            ),
            "unknown_semantic_count": 7,
        },
        "historical_structural_linkage_verified": structural["structural_lifecycle_linkage"] == "PASS",
        "historical_economic_coverage_verified": full_economic_coverage and all_observed_clean,
        "gross_payout_formula_verified": False,
        "protocol_fee_formula_verified": False,
        "deterministic_pnl_verified": False,
        "unresolved": sorted(set(unresolved)),
        "unresolved_count": len(set(unresolved)),
        "backfill_authorized": False,
        "paper_authorized": False,
        "testnet_authorized": False,
        "live_authorized": False,
        "max_autonomous_capital_usd": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan")
    scan.add_argument("--index", type=int, required=True)
    scan.add_argument("--from-block", type=int, required=True)
    scan.add_argument("--to-block", type=int, required=True)
    scan.add_argument("--logs-rpc", default=BLOCKSCOUT_LOGS_RPC)
    scan.add_argument("--out", type=Path, required=True)

    aggregate = sub.add_parser("aggregate")
    aggregate.add_argument("--shards", type=Path, required=True)
    aggregate.add_argument("--expected-from", type=int, default=DEFAULT_FROM_BLOCK)
    aggregate.add_argument("--expected-to", type=int, required=True)
    aggregate.add_argument("--rpc", default=RPC)
    aggregate.add_argument("--max-enriched-pairs", type=int, default=16)
    aggregate.add_argument("--out", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "scan":
        evidence = scan_shard(args.index, args.from_block, args.to_block, args.logs_rpc)
    else:
        shard_files = sorted(args.shards.glob("shard-*.json"))
        evidence = aggregate_shards(
            shard_files,
            args.expected_from,
            args.expected_to,
            args.rpc,
            args.max_enriched_pairs,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
