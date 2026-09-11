import hashlib
import json
from pathlib import Path

from agia_trading.euphoria_data.historical_lifecycle import INFLOW_EVENT, OUTFLOW_EVENT
from agia_trading.euphoria_data.historical_shards import aggregate_shards


def _log(topic0: str, key: str, tx: str, block: int, index: int) -> dict:
    return {
        "transaction_hash": tx,
        "block_number": block,
        "log_index": index,
        "topic0": topic0,
        "correlation_key": key,
        "removed": False,
    }


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _write_shard(path: Path, index: int, start: int, end: int, logs: list[dict]) -> None:
    payload = {
        "shard_index": index,
        "from_block": start,
        "to_block": end,
        "historical_log_index": "https://megaeth.blockscout.com/api/eth-rpc",
        "logs": logs,
        "log_count": len(logs),
        "windows": [],
    }
    payload["payload_sha256"] = _digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_aggregate_is_structural_only_and_emits_exception_inventory(tmp_path: Path) -> None:
    _write_shard(
        tmp_path / "shard-0.json",
        0,
        10,
        19,
        [
            _log(INFLOW_EVENT, "0x01", "0xaaa", 11, 1),
            _log(OUTFLOW_EVENT, "0x01", "0xaab", 12, 1),
            _log(INFLOW_EVENT, "0x02", "0xaac", 13, 1),
        ],
    )
    _write_shard(
        tmp_path / "shard-1.json",
        1,
        20,
        29,
        [_log(OUTFLOW_EVENT, "0x03", "0xaad", 21, 1)],
    )

    result = aggregate_shards(sorted(tmp_path.glob("shard-*.json")), 10, 29)

    assert result["structural"]["correlation_key_uniqueness"] == "PASS"
    assert result["structural"]["duplicate_lifecycle_count"] == 0
    assert result["structural"]["ordering_error_count"] == 0
    assert result["structural"]["orphan_opening_count"] == 1
    assert result["structural"]["orphan_closing_count"] == 1
    assert result["exceptions"]["count"] == 2
    assert result["exceptions"]["counts_by_kind"] == {
        "CLOSING_ONLY": 1,
        "OPENING_ONLY": 1,
    }
    assert {item["adjudication_status"] for item in result["exceptions"]["inventory"]} == {
        "UNRESOLVED"
    }
    assert result["economic_coverage"]["status"] == "NOT_EVALUATED"
    assert result["economic_coverage"]["coverage_pair_count"] == 0
    assert result["historical_economic_coverage_verified"] is False
    assert result["backfill_authorized"] is False
    assert result["live_authorized"] is False
    assert result["max_autonomous_capital_usd"] == 0


def test_aggregate_rejects_gap_before_any_economic_work(tmp_path: Path) -> None:
    _write_shard(tmp_path / "shard-0.json", 0, 10, 19, [])
    _write_shard(tmp_path / "shard-1.json", 1, 21, 29, [])

    try:
        aggregate_shards(sorted(tmp_path.glob("shard-*.json")), 10, 29)
    except RuntimeError as exc:
        assert "coverage invalid" in str(exc)
    else:
        raise AssertionError("coverage gap must fail closed")
