from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
from pathlib import Path
from typing import Any

from .accounting import PROXY, parse_receipt_logs, parse_redstone_payload
from .historical_lifecycle import BLOCKSCOUT_LOGS_RPC, INFLOW_EVENT, OUTFLOW_EVENT, RPC
from .rpc import RpcClient

MAX_RETRIES = 8
BASE_PAUSE_SECONDS = 0.35


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _hex_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    return int(value, 16) if isinstance(value, str) else int(value)


def _retry_delay(exc: Exception, attempt: int) -> float | None:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after:
            try:
                return min(max(float(retry_after), 1.0), 90.0)
            except ValueError:
                pass
        return min(float(2**attempt), 90.0)
    if isinstance(exc, (TimeoutError, urllib.error.URLError)):
        return min(float(2**attempt), 60.0)
    return None


def _call(rpc: RpcClient, method: str, params: list[Any]) -> Any:
    for attempt in range(MAX_RETRIES + 1):
        try:
            value = rpc.call(method, params)
            time.sleep(BASE_PAUSE_SECONDS)
            return value
        except Exception as exc:
            delay = _retry_delay(exc, attempt)
            if delay is None or attempt >= MAX_RETRIES:
                raise
            time.sleep(delay)
    raise RuntimeError("unreachable")


def _cache_read(cache_dir: Path, namespace: str, key: str) -> Any | None:
    path = cache_dir / namespace / f"{key}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_write(cache_dir: Path, namespace: str, key: str, value: Any) -> None:
    path = cache_dir / namespace / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _cached_call(
    rpc: RpcClient,
    cache_dir: Path,
    namespace: str,
    key: str,
    method: str,
    params: list[Any],
) -> Any:
    cached = _cache_read(cache_dir, namespace, key)
    if cached is not None:
        return cached
    value = _call(rpc, method, params)
    _cache_write(cache_dir, namespace, key, value)
    return value


def _normalise_log(log: dict[str, Any]) -> dict[str, Any]:
    topics = [str(topic).lower() for topic in log.get("topics", [])]
    return {
        "transaction_hash": str(log.get("transactionHash", "")).lower(),
        "block_number": _hex_int(log.get("blockNumber")),
        "log_index": _hex_int(log.get("logIndex")),
        "topic0": topics[0] if topics else None,
        "correlation_key": topics[1] if len(topics) > 1 else None,
        "topics": topics,
        "data": str(log.get("data", "0x")).lower(),
        "address": str(log.get("address", "")).lower(),
    }


def targeted_lifecycle_lookup(
    logs_rpc: RpcClient,
    correlation_key: str,
    from_block: int,
    to_block: int,
    cache_dir: Path,
) -> list[dict[str, Any]]:
    cache_key = correlation_key.lower().removeprefix("0x")
    cached = _cache_read(cache_dir, "targeted_logs", cache_key)
    if cached is not None:
        return cached
    result = _call(
        logs_rpc,
        "eth_getLogs",
        [
            {
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "address": PROXY,
                "topics": [[INFLOW_EVENT, OUTFLOW_EVENT], correlation_key],
            }
        ],
    )
    logs = [_normalise_log(item) for item in result if not item.get("removed", False)]
    logs.sort(key=lambda item: (item["block_number"] or 0, item["log_index"] or 0))
    _cache_write(cache_dir, "targeted_logs", cache_key, logs)
    return logs


def classify_exception(
    item: dict[str, Any],
    targeted_logs: list[dict[str, Any]],
    range_from: int,
    range_to: int,
    censoring_horizon_blocks: int,
) -> dict[str, Any]:
    openings = [log for log in targeted_logs if log.get("topic0") == INFLOW_EVENT]
    closings = [log for log in targeted_logs if log.get("topic0") == OUTFLOW_EVENT]
    original_openings = item.get("openings", [])
    original_closings = item.get("closings", [])

    if openings and closings and (not original_openings or not original_closings):
        return {
            "label": "INDEXING_DEFECT",
            "classification_verified": True,
            "structural_completeness_resolved": True,
            "reason": "targeted historical lookup recovered both lifecycle event families",
        }

    if item.get("kind") == "OPENING_ONLY" and original_openings:
        block = int(original_openings[0]["block_number"])
        if range_to - block <= censoring_horizon_blocks:
            return {
                "label": "RIGHT_CENSORED",
                "classification_verified": True,
                "structural_completeness_resolved": True,
                "reason": "opening lies within the empirical right-censoring horizon",
            }

    if item.get("kind") == "CLOSING_ONLY" and original_closings:
        block = int(original_closings[0]["block_number"])
        if block - range_from <= censoring_horizon_blocks:
            return {
                "label": "LEFT_CENSORED",
                "classification_verified": True,
                "structural_completeness_resolved": True,
                "reason": "closing lies within the empirical left-censoring horizon",
            }

    expected_singleton = (
        item.get("kind") == "OPENING_ONLY" and len(openings) == 1 and not closings
    ) or (
        item.get("kind") == "CLOSING_ONLY" and len(closings) == 1 and not openings
    )
    if expected_singleton:
        return {
            "label": "MISSING_COUNTERPART",
            "classification_verified": True,
            "structural_completeness_resolved": False,
            "reason": "targeted full-range lookup confirms one lifecycle family outside censoring horizon",
        }

    return {
        "label": "UNKNOWN",
        "classification_verified": False,
        "structural_completeness_resolved": False,
        "reason": "deterministic evidence does not satisfy an adjudication rule",
    }


def _implementation_era(block_number: int) -> dict[str, Any]:
    if block_number < 12_116_795:
        return {"era": "PRE_OBSERVED_PROXY_HISTORY", "implementation": None}
    if block_number < 12_195_603:
        return {
            "era": "HISTORICAL_IMPLEMENTATION",
            "implementation": "0xa9eefd86a4ef1cc1b72eddc6da3b2c0b40016cbb",
        }
    return {
        "era": "CURRENT_IMPLEMENTATION",
        "implementation": "0x95d2a2cb2e9f1efb89f435752bbc8ccf61c3485a",
    }


def _transaction_bundle(
    rpc: RpcClient,
    cache_dir: Path,
    tx_hash: str,
) -> dict[str, Any]:
    key = tx_hash.lower().removeprefix("0x")
    tx = _cached_call(
        rpc,
        cache_dir,
        "transactions",
        key,
        "eth_getTransactionByHash",
        [tx_hash],
    )
    receipt = _cached_call(
        rpc,
        cache_dir,
        "receipts",
        key,
        "eth_getTransactionReceipt",
        [tx_hash],
    )
    if tx is None or receipt is None:
        return {"tx_hash": tx_hash, "complete": False, "reason": "missing_transaction_or_receipt"}

    block_number = _hex_int(tx.get("blockNumber"))
    if block_number is None:
        return {"tx_hash": tx_hash, "complete": False, "reason": "missing_block_number"}
    block = _cached_call(
        rpc,
        cache_dir,
        "blocks",
        str(block_number),
        "eth_getBlockByNumber",
        [hex(block_number), False],
    )
    input_data = str(tx.get("input") or "0x").lower()
    parsed = parse_receipt_logs(receipt.get("logs", []))
    euphoria_logs = [
        _normalise_log(log)
        for log in receipt.get("logs", [])
        if str(log.get("address", "")).lower() == PROXY.lower()
    ]
    return {
        "tx_hash": tx_hash.lower(),
        "complete": True,
        "status": _hex_int(receipt.get("status")),
        "block_number": block_number,
        "block_timestamp": _hex_int((block or {}).get("timestamp")),
        "from": str(tx.get("from", "")).lower(),
        "to": str(tx.get("to", "")).lower(),
        "selector": input_data[:10] if len(input_data) >= 10 else input_data,
        "calldata_bytes": max((len(input_data) - 2) // 2, 0),
        "implementation_era": _implementation_era(block_number),
        "usdm_in_raw": parsed["usdm_in_raw"],
        "usdm_out_raw": parsed["usdm_out_raw"],
        "usdm_transfers": parsed["usdm_transfers"],
        "balance_updates": parsed["balance_updates"],
        "euphoria_logs": euphoria_logs,
        "redstone": parse_redstone_payload(input_data),
    }


def adjudicate(
    historical_evidence: dict[str, Any],
    endpoint: str = RPC,
    logs_endpoint: str = BLOCKSCOUT_LOGS_RPC,
    cache_dir: Path = Path("evidence/cache/p0-euphoria-exceptions"),
    censoring_horizon_blocks: int = 0,
) -> dict[str, Any]:
    inventory = historical_evidence["exceptions"]["inventory"]
    range_from = int(historical_evidence["range"]["from_block"])
    range_to = int(historical_evidence["range"]["to_block"])
    expected_inventory_sha = historical_evidence["exceptions"]["inventory_sha256"]
    if _digest(inventory) != expected_inventory_sha:
        raise RuntimeError("historical exception inventory SHA mismatch")

    observed_max_delta = historical_evidence["structural"].get(
        "matched_lifecycle_block_delta_max"
    )
    if censoring_horizon_blocks <= 0:
        if observed_max_delta is None:
            raise RuntimeError("cannot derive censoring horizon without matched lifecycle durations")
        censoring_horizon_blocks = int(observed_max_delta)
    if censoring_horizon_blocks < 0:
        raise ValueError("censoring horizon must be non-negative")

    rpc = RpcClient(endpoint, user_agent="AGIA-TRADING-EXCEPTION-ADJUDICATION/1.0 read-only")
    logs_rpc = RpcClient(
        logs_endpoint,
        user_agent="AGIA-TRADING-EXCEPTION-ADJUDICATION-INDEX/1.0 read-only",
    )
    rpc.verify_chain_id(4326)
    logs_rpc.verify_chain_id(4326)

    results: list[dict[str, Any]] = []
    label_counts: dict[str, int] = {}
    true_orphan_openings = 0
    true_orphan_closings = 0
    unclassified = 0

    for item in inventory:
        targeted = targeted_lifecycle_lookup(
            logs_rpc,
            item["correlation_key"],
            range_from,
            range_to,
            cache_dir,
        )
        classification = classify_exception(
            item,
            targeted,
            range_from,
            range_to,
            censoring_horizon_blocks,
        )
        label = classification["label"]
        label_counts[label] = label_counts.get(label, 0) + 1
        if label == "UNKNOWN":
            unclassified += 1
        if label == "MISSING_COUNTERPART":
            if item.get("kind") == "OPENING_ONLY":
                true_orphan_openings += 1
            elif item.get("kind") == "CLOSING_ONLY":
                true_orphan_closings += 1

        tx_hashes = sorted(
            {
                event["transaction_hash"]
                for event in item.get("openings", []) + item.get("closings", [])
                if event.get("transaction_hash")
            }
        )
        results.append(
            {
                "correlation_key": item["correlation_key"],
                "original_kind": item.get("kind"),
                "targeted_lifecycle_logs": targeted,
                "classification": classification,
                "transaction_bundles": [
                    _transaction_bundle(rpc, cache_dir, tx_hash) for tx_hash in tx_hashes
                ],
                "economic_role_verified": False,
            }
        )

    all_resolved = all(
        item["classification"]["structural_completeness_resolved"] for item in results
    )
    pass_gate = unclassified == 0 and true_orphan_openings == 0 and true_orphan_closings == 0
    return {
        "gate": "P0-EUPHORIA-EXCEPTION-ADJUDICATION-001",
        "status": "PASS" if pass_gate and all_resolved else "PARTIAL_EVIDENCE",
        "chain_id": 4326,
        "source_historical_gate": historical_evidence["gate"],
        "source_exception_inventory_sha256": expected_inventory_sha,
        "adjudication_ledger_sha256": _digest(results),
        "range": {"from_block": range_from, "to_block": range_to},
        "censoring_horizon_blocks": censoring_horizon_blocks,
        "censoring_horizon_basis": "max_observed_matched_lifecycle_block_delta",
        "exception_count": len(inventory),
        "label_counts": label_counts,
        "true_orphan_opening_count": true_orphan_openings,
        "true_orphan_closing_count": true_orphan_closings,
        "unclassified_exception_count": unclassified,
        "all_exceptions_classified": unclassified == 0,
        "all_structural_exceptions_resolved": all_resolved,
        "results": results,
        "economic_semantics_verified": False,
        "gross_payout_formula_verified": False,
        "protocol_fee_formula_verified": False,
        "deterministic_pnl_verified": False,
        "backfill_authorized": False,
        "paper_authorized": False,
        "testnet_authorized": False,
        "live_authorized": False,
        "max_autonomous_capital_usd": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rpc", default=RPC)
    parser.add_argument("--logs-rpc", default=BLOCKSCOUT_LOGS_RPC)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("evidence/cache/p0-euphoria-exceptions"),
    )
    parser.add_argument("--censoring-horizon-blocks", type=int, default=0)
    args = parser.parse_args()
    historical = json.loads(args.historical_evidence.read_text(encoding="utf-8"))
    evidence = adjudicate(
        historical,
        args.rpc,
        args.logs_rpc,
        args.cache_dir,
        args.censoring_horizon_blocks,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in evidence.items() if key != "results"},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
