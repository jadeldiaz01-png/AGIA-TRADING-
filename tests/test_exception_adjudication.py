from agia_trading.euphoria_data.exception_adjudication import classify_exception
from agia_trading.euphoria_data.historical_lifecycle import INFLOW_EVENT, OUTFLOW_EVENT


def _event(topic0: str, key: str, block: int) -> dict:
    return {
        "transaction_hash": f"0x{block:064x}",
        "block_number": block,
        "log_index": 1,
        "topic0": topic0,
        "correlation_key": key,
    }


def test_classifies_targeted_counterpart_as_indexing_defect() -> None:
    key = "0x01"
    item = {
        "correlation_key": key,
        "kind": "OPENING_ONLY",
        "openings": [_event(INFLOW_EVENT, key, 100)],
        "closings": [],
    }
    result = classify_exception(
        item,
        [_event(INFLOW_EVENT, key, 100), _event(OUTFLOW_EVENT, key, 120)],
        1,
        1_000,
        100,
    )
    assert result["label"] == "INDEXING_DEFECT"
    assert result["classification_verified"] is True
    assert result["structural_completeness_resolved"] is True


def test_classifies_opening_near_tip_as_right_censored() -> None:
    key = "0x02"
    item = {
        "correlation_key": key,
        "kind": "OPENING_ONLY",
        "openings": [_event(INFLOW_EVENT, key, 950)],
        "closings": [],
    }
    result = classify_exception(item, [_event(INFLOW_EVENT, key, 950)], 1, 1_000, 100)
    assert result["label"] == "RIGHT_CENSORED"
    assert result["structural_completeness_resolved"] is True


def test_classifies_old_singleton_as_missing_counterpart() -> None:
    key = "0x03"
    item = {
        "correlation_key": key,
        "kind": "OPENING_ONLY",
        "openings": [_event(INFLOW_EVENT, key, 200)],
        "closings": [],
    }
    result = classify_exception(item, [_event(INFLOW_EVENT, key, 200)], 1, 1_000, 100)
    assert result["label"] == "MISSING_COUNTERPART"
    assert result["classification_verified"] is True
    assert result["structural_completeness_resolved"] is False


def test_unknown_when_targeted_lookup_is_inconsistent() -> None:
    key = "0x04"
    item = {
        "correlation_key": key,
        "kind": "OPENING_ONLY",
        "openings": [_event(INFLOW_EVENT, key, 200)],
        "closings": [],
    }
    result = classify_exception(item, [], 1, 1_000, 100)
    assert result["label"] == "UNKNOWN"
    assert result["classification_verified"] is False
    assert result["structural_completeness_resolved"] is False
