from agia_trading.euphoria_data.historical_lifecycle import INFLOW_EVENT, OUTFLOW_EVENT
from agia_trading.euphoria_data.orphan_semantics import (
    _address_topic,
    _participant_accounts,
    _topic_address,
)


def _bundle(kind: str) -> dict:
    account_a = "0x1111111111111111111111111111111111111111"
    account_b = "0x2222222222222222222222222222222222222222"
    if kind == "OPENING_ONLY":
        logs = [
            {
                "topic0": INFLOW_EVENT,
                "topics": [
                    INFLOW_EVENT,
                    "0x" + "ab" * 32,
                    _address_topic(account_a),
                    _address_topic(account_b),
                ],
            }
        ]
    else:
        logs = [
            {
                "topic0": OUTFLOW_EVENT,
                "topics": [
                    OUTFLOW_EVENT,
                    "0x" + "cd" * 32,
                    _address_topic(account_a),
                ],
            }
        ]
    return {
        "original_kind": kind,
        "transaction_bundles": [
            {
                "complete": True,
                "block_number": 100,
                "euphoria_logs": logs,
            }
        ],
    }


def test_address_topic_round_trip() -> None:
    address = "0x1234567890abcdef1234567890abcdef12345678"
    topic = _address_topic(address)
    assert len(topic) == 66
    assert _topic_address(topic) == address


def test_opening_extracts_two_participants() -> None:
    assert _participant_accounts(_bundle("OPENING_ONLY")) == [
        "0x1111111111111111111111111111111111111111",
        "0x2222222222222222222222222222222222222222",
    ]


def test_closing_extracts_paid_participant() -> None:
    assert _participant_accounts(_bundle("CLOSING_ONLY")) == [
        "0x1111111111111111111111111111111111111111"
    ]


def test_incomplete_bundle_has_no_participants() -> None:
    result = {
        "original_kind": "OPENING_ONLY",
        "transaction_bundles": [{"complete": False}],
    }
    assert _participant_accounts(result) == []
