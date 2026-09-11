from __future__ import annotations

import argparse
import json
import time
import urllib.error
from collections import defaultdict
from pathlib import Path

from .accounting import PROXY, parse_receipt_logs, parse_redstone_payload
from .lifecycle import INFLOW_EVENT, INFLOW_SELECTOR, OUTFLOW_EVENT, OUTFLOW_SELECTOR
from .rpc import RpcClient

RPC = "https://mainnet.megaeth.com/rpc"
BLOCKSCOUT_LOGS_RPC = "https://megaeth.blockscout.com/api/eth-rpc"
DEFAULT_FROM_BLOCK = 12_116_795
DEFAULT_WINDOW = 200_000
MIN_WINDOW = 10
MAX_LOGS_RESPONSE = 1_000
MAX_RATE_LIMIT_RETRIES = 7
SUCCESS_PAUSE_SECONDS = 0.05


def _hex_int(value: str | int) -> int:
    return int(value, 16) if isinstance(value, str) else int(value)


def _normalise_log(log: dict) -> dict:
    topics = [topic.lower() for topic in log.get("topics", [])]
    return {
        "transaction_hash": log["transactionHash"].lower(),
        "block_number": _hex_int(log["blockNumber"]),
        "log_index": _hex_int(log["logIndex"]),
        "topic0": topics[0] if topics else None,
        "correlation_key": topics[1] if len(topics) > 1 else None,
        "removed": bool(log.get("removed", False)),
    }


def _rate_limit_delay(exc: Exception, attempt: int) -> float | None:
    if not isinstance(exc, urllib.error.HTTPError) or exc.code != 429:
        return None
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return min(max(float(retry_after), 1.0), 60.0)
        except ValueError:
            pass
    return min(float(2**attempt), 60.0)


def _rpc_call_with_backoff(rpc: RpcClient, method: str, params: list, retries: int = 5):
    for attempt in range(retries + 1):
        try:
            return rpc.call(method, params)
        except Exception as exc:
            delay = _rate_limit_delay(exc, attempt)
            if delay is None or attempt >= retries:
                raise
            time.sleep(delay)
    raise RuntimeError("unreachable")


def _get_logs_window(rpc: RpcClient, topic0s: list[str], start: int, end: int) -> list[dict]:
    result = rpc.call(
        "eth_getLogs",
        [
            {
                "fromBlock": hex(start),
                "toBlock": hex(end),
                "address": PROXY,
                "topics": [topic0s],
            }
        ],
    )
    return [_normalise_log(item) for item in result if not item.get("removed", False)]


def adaptive_get_logs(
    rpc: RpcClient,
    topic0s: list[str],
    start: int,
    end: int,
    initial_window: int = DEFAULT_WINDOW,
) -> tuple[list[dict], list[dict]]:
    cursor = start
    window = max(initial_window, MIN_WINDOW)
    logs: list[dict] = []
    windows: list[dict] = []
    rate_limit_events = 0
    truncation_splits = 0

    while cursor <= end:
        window_end = min(cursor + window - 1, end)
        rate_attempt = 0
        while True:
            try:
                batch = _get_logs_window(rpc, topic0s, cursor, window_end)
            except Exception as exc:  # providers expose different range/resource errors
                delay = _rate_limit_delay(exc, rate_attempt)
                if delay is not None:
                    if rate_attempt >= MAX_RATE_LIMIT_RETRIES:
                        raise RuntimeError(
                            f"eth_getLogs rate limit persisted at {cursor}-{window_end} "
                            f"after {MAX_RATE_LIMIT_RETRIES} retries"
                        ) from exc
                    rate_limit_events += 1
                    rate_attempt += 1
                    time.sleep(delay)
                    continue
                if window <= MIN_WINDOW:
                    raise RuntimeError(
                        f"eth_getLogs failed at minimum window {cursor}-{window_end}: {exc}"
                    ) from exc
                window = max(window // 2, MIN_WINDOW)
                window_end = min(cursor + window - 1, end)
                rate_attempt = 0
                continue

            # Blockscout documents a 1000-log response cap. Never accept an exact-cap
            # response as complete unless the block window can no longer be split.
            if len(batch) >= MAX_LOGS_RESPONSE:
                if window <= MIN_WINDOW:
                    raise RuntimeError(
                        f"possible log truncation at minimum window {cursor}-{window_end}: "
                        f"received {len(batch)} logs"
                    )
                truncation_splits += 1
                window = max(window // 2, MIN_WINDOW)
                window_end = min(cursor + window - 1, end)
                rate_attempt = 0
                continue
            break

        logs.extend(batch)
        windows.append(
            {
                "from_block": cursor,
                "to_block": window_end,
                "log_count": len(batch),
                "window_size": window_end - cursor + 1,
                "rate_limit_events_cumulative": rate_limit_events,
                "truncation_splits_cumulative": truncation_splits,
            }
        )
        cursor = window_end + 1
        if len(batch) < 250 and window < initial_window:
            window = min(window * 2, initial_window)
        time.sleep(SUCCESS_PAUSE_SECONDS)

    logs.sort(key=lambda item: (item["block_number"], item["log_index"]))
    return logs, windows


def build_structural_index(openings: list[dict], closings: list[dict]) -> dict:
    by_open: dict[str, list[dict]] = defaultdict(list)
    by_close: dict[str, list[dict]] = defaultdict(list)
    missing_key_count = 0

    for item in openings:
        key = item.get("correlation_key")
        if key:
            by_open[key].append(item)
        else:
            missing_key_count += 1
    for item in closings:
        key = item.get("correlation_key")
        if key:
            by_close[key].append(item)
        else:
            missing_key_count += 1

    keys = sorted(set(by_open) | set(by_close))
    duplicate_keys = [key for key in keys if len(by_open[key]) > 1 or len(by_close[key]) > 1]
    orphan_openings = [key for key in keys if by_open[key] and not by_close[key]]
    orphan_closings = [key for key in keys if by_close[key] and not by_open[key]]

    pairs: list[dict] = []
    ordering_errors: list[str] = []
    for key in keys:
        if len(by_open[key]) != 1 or len(by_close[key]) != 1:
            continue
        opening = by_open[key][0]
        closing = by_close[key][0]
        ordered = closing["block_number"] > opening["block_number"] or (
            closing["block_number"] == opening["block_number"]
            and closing["log_index"] > opening["log_index"]
        )
        if not ordered:
            ordering_errors.append(key)
        pairs.append(
            {
                "correlation_key": key,
                "opening": opening,
                "closing": closing,
                "ordered": ordered,
                "block_delta": closing["block_number"] - opening["block_number"],
            }
        )

    correlation_key_uniqueness = not duplicate_keys and missing_key_count == 0
    structural_complete = (
        correlation_key_uniqueness
        and not orphan_openings
        and not orphan_closings
        and not ordering_errors
        and len(pairs) == len(keys)
    )

    return {
        "opening_event_count": len(openings),
        "closing_event_count": len(closings),
        "unique_correlation_key_count": len(keys),
        "correlation_key_missing_count": missing_key_count,
        "duplicate_lifecycle_count": len(duplicate_keys),
        "duplicate_correlation_keys": duplicate_keys[:100],
        "orphan_opening_count": len(orphan_openings),
        "orphan_opening_keys": orphan_openings[:100],
        "orphan_closing_count": len(orphan_closings),
        "orphan_closing_keys": orphan_closings[:100],
        "ordering_error_count": len(ordering_errors),
        "ordering_error_keys": ordering_errors[:100],
        "matched_pair_count": len(pairs),
        "correlation_key_uniqueness": "PASS" if correlation_key_uniqueness else "FAIL",
        "structural_lifecycle_linkage": "PASS" if structural_complete else "FAIL",
        "pairs": pairs,
    }


def _select_coverage_pairs(pairs: list[dict], max_pairs: int) -> list[dict]:
    if max_pairs <= 0 or not pairs:
        return []
    if len(pairs) <= max_pairs:
        return pairs
    if max_pairs == 1:
        return [pairs[len(pairs) // 2]]
    indexes = {round(index * (len(pairs) - 1) / (max_pairs - 1)) for index in range(max_pairs)}
    return [pairs[index] for index in sorted(indexes)]


def enrich_pairs(rpc: RpcClient, pairs: list[dict], max_pairs: int) -> dict:
    selected = _select_coverage_pairs(pairs, max_pairs)
    receipt_missing = 0
    oracle_payload_missing = 0
    amount_errors = 0
    selector_errors = 0
    enriched: list[dict] = []

    for pair in selected:
        opening_hash = pair["opening"]["transaction_hash"]
        closing_hash = pair["closing"]["transaction_hash"]
        opening_tx = _rpc_call_with_backoff(rpc, "eth_getTransactionByHash", [opening_hash])
        closing_tx = _rpc_call_with_backoff(rpc, "eth_getTransactionByHash", [closing_hash])
        opening_receipt = _rpc_call_with_backoff(rpc, "eth_getTransactionReceipt", [opening_hash])
        closing_receipt = _rpc_call_with_backoff(rpc, "eth_getTransactionReceipt", [closing_hash])

        if not opening_tx or not closing_tx or not opening_receipt or not closing_receipt:
            receipt_missing += 1
            enriched.append({**pair, "evidence_complete": False, "reason": "missing_tx_or_receipt"})
            continue

        opening_input = (opening_tx.get("input") or "0x").lower()
        closing_input = (closing_tx.get("input") or "0x").lower()
        selectors_valid = opening_input[:10] == INFLOW_SELECTOR and closing_input[:10] == OUTFLOW_SELECTOR
        if not selectors_valid:
            selector_errors += 1

        opening_logs = parse_receipt_logs(opening_receipt.get("logs", []))
        closing_logs = parse_receipt_logs(closing_receipt.get("logs", []))
        redstone = parse_redstone_payload(closing_input)
        if redstone is None:
            oracle_payload_missing += 1

        amount_equal = (
            opening_logs["usdm_in_raw"] > 0
            and opening_logs["usdm_in_raw"] == closing_logs["usdm_out_raw"]
        )
        if not amount_equal:
            amount_errors += 1

        enriched.append(
            {
                **pair,
                "evidence_complete": True,
                "selectors_valid": selectors_valid,
                "opening_usdm_in_raw": opening_logs["usdm_in_raw"],
                "closing_usdm_out_raw": closing_logs["usdm_out_raw"],
                "amounts_equal": amount_equal,
                "closing_redstone_payload_present": redstone is not None,
                "closing_redstone_feed_ids": sorted(
                    {
                        point["feed_id"]
                        for package in (redstone or {}).get("packages", [])
                        for point in package.get("data_points", [])
                    }
                ),
                "economic_role_verified": False,
            }
        )
        time.sleep(SUCCESS_PAUSE_SECONDS)

    return {
        "coverage_pair_count": len(selected),
        "total_pair_count": len(pairs),
        "coverage_is_full": bool(pairs) and len(selected) == len(pairs),
        "receipt_missing_count_observed": receipt_missing,
        "oracle_payload_missing_count_observed": oracle_payload_missing,
        "amount_reconciliation_errors_observed": amount_errors,
        "selector_mismatch_count_observed": selector_errors,
        "pairs": enriched,
    }


def collect(
    endpoint: str = RPC,
    logs_endpoint: str = BLOCKSCOUT_LOGS_RPC,
    from_block: int = DEFAULT_FROM_BLOCK,
    to_block: int | None = None,
    max_enriched_pairs: int = 16,
) -> dict:
    rpc = RpcClient(endpoint)
    logs_rpc = RpcClient(logs_endpoint, user_agent="AGIA-TRADING-HISTORICAL-INDEX/1.0 read-only")
    rpc.verify_chain_id(4326)
    logs_rpc.verify_chain_id(4326)
    latest = _hex_int(_rpc_call_with_backoff(rpc, "eth_blockNumber", []))
    end = latest if to_block is None else min(to_block, latest)
    if from_block > end:
        raise ValueError("from_block is after to_block/latest")

    all_logs, collection_windows = adaptive_get_logs(
        logs_rpc,
        [INFLOW_EVENT, OUTFLOW_EVENT],
        from_block,
        end,
    )
    openings = [item for item in all_logs if item["topic0"] == INFLOW_EVENT]
    closings = [item for item in all_logs if item["topic0"] == OUTFLOW_EVENT]
    structural = build_structural_index(openings, closings)
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

    return {
        "gate": "P0-EUPHORIA-HISTORICAL-LIFECYCLE-001",
        "status": "PARTIAL_EVIDENCE" if unresolved else "PASS",
        "chain_id": 4326,
        "sources": {
            "authoritative_rpc": endpoint,
            "historical_log_index": logs_endpoint,
            "historical_log_index_role": "discovery/index only; tx/receipt verification uses authoritative RPC",
        },
        "range": {"from_block": from_block, "to_block": end, "latest_block": latest},
        "collection": {
            "method": "blockscout_eth_getLogs_adaptive_windows_with_cap_detection",
            "windows": collection_windows,
            "window_count": len(collection_windows),
            "rate_limit_events": (
                collection_windows[-1]["rate_limit_events_cumulative"] if collection_windows else 0
            ),
            "truncation_splits": (
                collection_windows[-1]["truncation_splits_cumulative"] if collection_windows else 0
            ),
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
    parser.add_argument("--rpc", default=RPC)
    parser.add_argument("--logs-rpc", default=BLOCKSCOUT_LOGS_RPC)
    parser.add_argument("--from-block", type=int, default=DEFAULT_FROM_BLOCK)
    parser.add_argument("--to-block", type=int)
    parser.add_argument("--max-enriched-pairs", type=int, default=16)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence = collect(
        args.rpc,
        args.logs_rpc,
        args.from_block,
        args.to_block,
        args.max_enriched_pairs,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
