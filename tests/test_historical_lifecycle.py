from agia_trading.euphoria_data.historical_lifecycle import build_structural_index


def _log(topic0: str, key: str, tx: str, block: int, index: int) -> dict:
    return {
        "transaction_hash": tx,
        "block_number": block,
        "log_index": index,
        "topic0": topic0,
        "correlation_key": key,
        "removed": False,
    }


def test_structural_index_passes_for_one_to_one_ordered_pairs() -> None:
    openings = [
        _log("open", "0x01", "0xa", 10, 1),
        _log("open", "0x02", "0xb", 20, 1),
    ]
    closings = [
        _log("close", "0x01", "0xc", 15, 1),
        _log("close", "0x02", "0xd", 30, 1),
    ]
    result = build_structural_index(openings, closings)
    assert result["correlation_key_uniqueness"] == "PASS"
    assert result["structural_lifecycle_linkage"] == "PASS"
    assert result["orphan_opening_count"] == 0
    assert result["orphan_closing_count"] == 0
    assert result["duplicate_lifecycle_count"] == 0
    assert result["ordering_error_count"] == 0


def test_structural_index_fails_closed_on_duplicates_and_orphans() -> None:
    openings = [
        _log("open", "0x01", "0xa", 10, 1),
        _log("open", "0x01", "0xb", 11, 1),
        _log("open", "0x02", "0xc", 20, 1),
    ]
    closings = [
        _log("close", "0x01", "0xd", 15, 1),
        _log("close", "0x03", "0xe", 30, 1),
    ]
    result = build_structural_index(openings, closings)
    assert result["correlation_key_uniqueness"] == "FAIL"
    assert result["structural_lifecycle_linkage"] == "FAIL"
    assert result["duplicate_lifecycle_count"] == 1
    assert result["orphan_opening_count"] == 1
    assert result["orphan_closing_count"] == 1


def test_structural_index_rejects_close_before_open() -> None:
    openings = [_log("open", "0x01", "0xa", 20, 2)]
    closings = [_log("close", "0x01", "0xb", 20, 1)]
    result = build_structural_index(openings, closings)
    assert result["ordering_error_count"] == 1
    assert result["structural_lifecycle_linkage"] == "FAIL"
