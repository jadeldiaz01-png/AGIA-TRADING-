from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
from pathlib import Path
from typing import Any

from .accounting import PROXY
from .historical_lifecycle import BLOCKSCOUT_LOGS_RPC, INFLOW_EVENT, OUTFLOW_EVENT
from .rpc import RpcClient

MAX_RESULTS_PER_LOG_QUERY = 1_000
MAX_RETRIES = 8
BASE_PAUSE_SECONDS = 0.25
DEFAULT_SEARCH_HORIZON_BLOCKS = 10_000


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _hex_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    return int(value, 16) if isinstance(value, str) else int(value)


def _address_topic(address: str) -> str:
    raw = address.lower().removeprefix("0x")
    if len(raw) != 40:
        raise ValueError(f"invalid EVM address: {address}")
    return "0x" + ("0" * 24) + raw


def _topic_address(topic: str | None) -> str | None:
    if not topic:
        return None
    raw = topic.lower().removeprefix("0x")
    if len(raw) != 64:
        return None
    return "0x" + raw[-40:]


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
            result = rpc.call(method, params)
            time.sleep(BASE_PAUSE_SECONDS)
            return result
        except Exception as exc:
            delay = _retry_delay(exc, attempt)
            if delay is None or attempt >= MAX_RETRIES:
                raise
            time.sleep(delay)
    raise RuntimeError("unreachable")


def _normalise_log(log: dict[str, Any]) -> dict[str, Any]:
    topics = [str(value).lower() for value in log.get("topics", [])]
    return {
        "transaction_hash": str(log.get("transactionHash", "")).lower(),
        "block_number": _hex_int(log.get("blockNumber")),
        "log_index": _hex_int(log.get("logIndex")),
        "topic0": topics[0] if topics else None,
        "correlation_key": topics[1] if len(topics) > 1 else None,
        "topics": topics,
        "address": str(log.get("address", "")).lower(),
        "data": str(log.get("data", "0x")).lower(),
    }


def _cache_path(cache_dir: Path, key: object) -> Path:
    return cache_dir / f"{_digest(key)}.json"


def _query_logs_window(
    rpc: RpcClient,
    topics: list[Any],
    from_block: int,
    to_block: int,
) -> list[dict[str, Any]]:
    result = _call(
        rpc,
        "eth_getLogs",
        [
            {
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "address": PROXY,
                "topics": topics,
            }
        ],
    )
    return [_normalise_log(item) for item in result if not item.get("removed", False)]


def _adaptive_topic_logs(
    rpc: RpcClient,
    topics: list[Any],
    from_block: int,
    to_block: int,
    cache_dir: Path,
) -> list[dict[str, Any]]:
    if from_block > to_block:
        return []
    key = {
        "endpoint": rpc.endpoint,
        "topics": topics,
        "from_block": from_block,
        "to_block": to_block,
    }
    path = _cache_path(cache_dir, key)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    logs = _query_logs_window(rpc, topics, from_block, to_block)
    if len(logs) >= MAX_RESULTS_PER_LOG_QUERY and from_block < to_block:
        midpoint = (from_block + to_block) // 2
        logs = _adaptive_topic_logs(rpc, topics, from_block, midpoint, cache_dir)
        logs += _adaptive_topic_logs(rpc, topics, midpoint + 1, to_block, cache_dir)

    identities = {
        (item["transaction_hash"], item["log_index"], item["topic0"]): item for item in logs
    }
    ordered = sorted(
        identities.values(),
        key=lambda item: (item["block_number"] or 0, item["log_index"] or 0),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ordered, sort_keys=True) + "\n", encoding="utf-8")
    return ordered


def _find_lifecycle_log(bundle: dict[str, Any], topic0: str) -> dict[str, Any] | None:
    for log in bundle.get("euphoria_logs", []):
        if log.get("topic0") == topic0:
            return log
    return None


def _lifecycle_event(result: dict[str, Any], topic0: str) -> dict[str, Any] | None:
    for bundle in result.get("transaction_bundles", []):
        if bundle.get("complete"):
            event = _find_lifecycle_log(bundle, topic0)
            if event:
                return event
    for log in result.get("targeted_lifecycle_logs", []):
        if log.get("topic0") == topic0:
            return log
    return None


def _participant_accounts(result: dict[str, Any]) -> list[str]:
    kind = result.get("original_kind")
    if kind == "OPENING_ONLY":
        event = _lifecycle_event(result, INFLOW_EVENT)
        if not event:
            return []
        accounts = [_topic_address(topic) for topic in event.get("topics", [])[2:4]]
        return sorted({account for account in accounts if account})
    if kind == "CLOSING_ONLY":
        event = _lifecycle_event(result, OUTFLOW_EVENT)
        if not event:
            return []
        topics = event.get("topics", [])
        account = _topic_address(topics[2]) if len(topics) > 2 else None
        return [account] if account else []
    return []


def _origin_block(result: dict[str, Any]) -> int | None:
    for bundle in result.get("transaction_bundles", []):
        if bundle.get("complete"):
            block = _hex_int(bundle.get("block_number"))
            if block is not None:
                return block
    kind = result.get("original_kind")
    topic0 = INFLOW_EVENT if kind == "OPENING_ONLY" else OUTFLOW_EVENT
    event = _lifecycle_event(result, topic0)
    return _hex_int(event.get("block_number")) if event else None


def _candidate_record(
    log: dict[str, Any],
    origin_block: int,
    participant: str,
    account_topic_position: int,
) -> dict[str, Any]:
    block = int(log["block_number"])
    return {
        "transaction_hash": log["transaction_hash"],
        "block_number": block,
        "block_delta": block - origin_block,
        "log_index": log["log_index"],
        "topic0": log["topic0"],
        "correlation_key": log["correlation_key"],
        "participant": participant,
        "participant_topic_position": account_topic_position,
    }


def _counterpart_candidates(
    rpc: RpcClient,
    result: dict[str, Any],
    range_from: int,
    range_to: int,
    horizon_blocks: int,
    cache_dir: Path,
) -> list[dict[str, Any]]:
    origin = _origin_block(result)
    participants = _participant_accounts(result)
    if origin is None or not participants:
        return []

    candidates: list[dict[str, Any]] = []
    if result.get("original_kind") == "OPENING_ONLY":
        start = origin + 1
        end = min(range_to, origin + horizon_blocks)
        for participant in participants:
            logs = _adaptive_topic_logs(
                rpc,
                [OUTFLOW_EVENT, None, _address_topic(participant)],
                start,
                end,
                cache_dir,
            )
            candidates.extend(_candidate_record(log, origin, participant, 2) for log in logs)
    elif result.get("original_kind") == "CLOSING_ONLY":
        start = max(range_from, origin - horizon_blocks)
        end = origin - 1
        for participant in participants:
            participant_topic = _address_topic(participant)
            for position in (2, 3):
                topics: list[Any] = [INFLOW_EVENT, None, None, None]
                topics[position] = participant_topic
                logs = _adaptive_topic_logs(rpc, topics, start, end, cache_dir)
                candidates.extend(
                    _candidate_record(log, origin, participant, position) for log in logs
                )

    original_key = result.get("correlation_key")
    deduped = {
        (item["transaction_hash"], item["log_index"], item["topic0"]): item
        for item in candidates
        if item.get("correlation_key") != original_key
    }
    return sorted(
        deduped.values(),
        key=lambda item: (abs(item["block_delta"]), item["block_number"], item["log_index"]),
    )


def profile_orphans(
    exception_evidence: dict[str, Any],
    logs_endpoint: str = BLOCKSCOUT_LOGS_RPC,
    search_horizon_blocks: int = DEFAULT_SEARCH_HORIZON_BLOCKS,
    cache_dir: Path = Path("evidence/cache/p0-euphoria-orphan-semantics"),
) -> dict[str, Any]:
    if search_horizon_blocks <= 0:
        raise ValueError("search_horizon_blocks must be positive")
    range_from = int(exception_evidence["range"]["from_block"])
    range_to = int(exception_evidence["range"]["to_block"])
    normal_horizon = int(exception_evidence.get("censoring_horizon_blocks", 0))
    rpc = RpcClient(
        logs_endpoint,
        user_agent="AGIA-TRADING-ORPHAN-SEMANTICS/1.0 read-only",
    )
    rpc.verify_chain_id(4326)

    rows: list[dict[str, Any]] = []
    for result in exception_evidence.get("results", []):
        if result.get("classification", {}).get("label") != "MISSING_COUNTERPART":
            continue
        participants = _participant_accounts(result)
        origin_block = _origin_block(result)
        candidates = _counterpart_candidates(
            rpc,
            result,
            range_from,
            range_to,
            search_horizon_blocks,
            cache_dir,
        )
        within_normal = [
            item for item in candidates if normal_horizon and abs(item["block_delta"]) <= normal_horizon
        ]
        rows.append(
            {
                "correlation_key": result["correlation_key"],
                "original_kind": result["original_kind"],
                "origin_block": origin_block,
                "participants": participants,
                "source_context_complete": origin_block is not None and bool(participants),
                "cross_key_candidate_count": len(candidates),
                "cross_key_candidate_within_normal_horizon_count": len(within_normal),
                "nearest_cross_key_candidates": candidates[:20],
                "semantic_classification": "UNRESOLVED",
                "semantic_rule_id": None,
                "economic_role_verified": False,
            }
        )

    opening_rows = [item for item in rows if item["original_kind"] == "OPENING_ONLY"]
    closing_rows = [item for item in rows if item["original_kind"] == "CLOSING_ONLY"]
    rows_with_candidates = [item for item in rows if item["cross_key_candidate_count"] > 0]
    rows_with_near_candidates = [
        item for item in rows if item["cross_key_candidate_within_normal_horizon_count"] > 0
    ]
    incomplete_context = [item for item in rows if not item["source_context_complete"]]
    ledger_sha = _digest(rows)
    return {
        "gate": "P0-EUPHORIA-ORPHAN-SEMANTICS-001",
        "status": "PARTIAL_EVIDENCE",
        "chain_id": 4326,
        "source_adjudication_ledger_sha256": exception_evidence["adjudication_ledger_sha256"],
        "range": {"from_block": range_from, "to_block": range_to},
        "normal_lifecycle_horizon_blocks": normal_horizon,
        "cross_key_search_horizon_blocks": search_horizon_blocks,
        "true_orphan_count": len(rows),
        "true_orphan_opening_count": len(opening_rows),
        "true_orphan_closing_count": len(closing_rows),
        "source_context_incomplete_count": len(incomplete_context),
        "orphans_with_cross_key_candidates": len(rows_with_candidates),
        "orphans_with_cross_key_candidates_within_normal_horizon": len(rows_with_near_candidates),
        "semantic_unknown_count": len(rows),
        "semantic_ledger_sha256": ledger_sha,
        "results": rows,
        "economic_semantics_verified": False,
        "gross_payout_formula_verified": False,
        "protocol_fee_formula_verified": False,
        "deterministic_pnl_verified": False,
        "backfill_authorized": False,
        "strategy_research_authorized": False,
        "paper_authorized": False,
        "testnet_authorized": False,
        "limited_pilot_authorized": False,
        "live_authorized": False,
        "max_autonomous_capital_usd": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exception-evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--logs-rpc", default=BLOCKSCOUT_LOGS_RPC)
    parser.add_argument("--search-horizon-blocks", type=int, default=DEFAULT_SEARCH_HORIZON_BLOCKS)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("evidence/cache/p0-euphoria-orphan-semantics"),
    )
    args = parser.parse_args()
    evidence = json.loads(args.exception_evidence.read_text(encoding="utf-8"))
    output = profile_orphans(
        evidence,
        logs_endpoint=args.logs_rpc,
        search_horizon_blocks=args.search_horizon_blocks,
        cache_dir=args.cache_dir,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "results"}, sort_keys=True))


if __name__ == "__main__":
    main()
