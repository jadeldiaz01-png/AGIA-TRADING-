from agia_trading.euphoria_data.historical_lifecycle import INFLOW_EVENT, OUTFLOW_EVENT
from agia_trading.euphoria_data.orphan_semantics import (
    _address_topic,
    _origin_block,
    _participant_accounts,
    _topic_address,
)


def _event(topic0: str, accounts: list[str], block: int = 100) -> dict:
    return {
        "topic0": topic0,
        "block_number": block,
        "topics": [topic0, "0x" + "ab" * 32, *[_address_topic(a) for a in accounts]],
    }


def _bundle(kind: str) -> dict:
    a = "0x1111111111111111111111111111111111111111"
    b = "0x2222222222222222222222222222222222222222"
    event = _event(INFLOW_EVENT, [a, b]) if kind == "OPENING_ONLY" else _event(OUTFLOW_EVENT, [a])
    return {
        "original_kind": kind,
        "transaction_bundles": [{"complete": True, "block_number": 100, "euphoria_logs": [event]}],
    }


def test_address_topic_round_trip() -> None:
    address = "0x1234567890abcdef1234567890abcdef12345678"
    assert _topic_address(_address_topic(address)) == address


def test_opening_extracts_two_participants() -> None:
    assert _participant_accounts(_bundle("OPENING_ONLY")) == [
        "0x1111111111111111111111111111111111111111",
        "0x2222222222222222222222222222222222222222",
    ]


def test_closing_extracts_paid_participant() -> None:
    assert _participant_accounts(_bundle("CLOSING_ONLY")) == [
        "0x1111111111111111111111111111111111111111"
    ]


def test_targeted_log_fallback_for_opening() -> None:
    a = "0x1111111111111111111111111111111111111111"
    b = "0x2222222222222222222222222222222222222222"
    result = {
        "original_kind": "OPENING_ONLY",
        "transaction_bundles": [{"complete": False}],
        "targeted_lifecycle_logs": [_event(INFLOW_EVENT, [a, b], 321)],
    }
    assert _participant_accounts(result) == [a, b]
    assert _origin_block(result) == 321


def test_targeted_log_fallback_for_closing() -> None:
    a = "0x3333333333333333333333333333333333333333"
    result = {
        "original_kind": "CLOSING_ONLY",
        "transaction_bundles": [{"complete": False}],
        "targeted_lifecycle_logs": [_event(OUTFLOW_EVENT, [a], 654)],
    }
    assert _participant_accounts(result) == [a]
    assert _origin_block(result) == 654
