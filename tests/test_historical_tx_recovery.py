from agia_trading.euphoria_data.historical_tx_recovery import _validate_case


def test_recovery_requires_hash_block_proxy_and_event_match() -> None:
    case = {
        "kind": "CLOSING_ONLY",
        "correlation_key": "0x" + "11" * 32,
        "tx_hash": "0x" + "22" * 32,
        "block_number": 123,
        "expected_topic0": "0x" + "33" * 32,
    }
    legacy = {
        "result": {
            "hash": case["tx_hash"],
            "blockNumber": "0x7b",
            "to": "0x12759afca690637b425ffba3265f0dc2f6242a8d",
            "input": "0x21d5c9bb",
            "logs": [
                {
                    "address": "0x12759afca690637b425ffba3265f0dc2f6242a8d",
                    "topics": [case["expected_topic0"], case["correlation_key"]],
                    "data": "0x",
                }
            ],
        }
    }
    v2_tx = {
        "hash": case["tx_hash"],
        "block_number": 123,
        "to": {"hash": "0x12759afca690637b425ffba3265f0dc2f6242a8d"},
        "status": "ok",
    }
    result = _validate_case(case, legacy, v2_tx, {"items": []})
    assert result["recovery_complete"] is True


def test_recovery_fails_closed_on_wrong_block() -> None:
    case = {
        "kind": "OPENING_ONLY",
        "correlation_key": "0x" + "11" * 32,
        "tx_hash": "0x" + "22" * 32,
        "block_number": 123,
        "expected_topic0": "0x" + "33" * 32,
    }
    legacy = {
        "result": {
            "hash": case["tx_hash"],
            "blockNumber": "0x7c",
            "to": "0x12759afca690637b425ffba3265f0dc2f6242a8d",
            "logs": [
                {
                    "topics": [case["expected_topic0"], case["correlation_key"]],
                    "data": "0x",
                }
            ],
        }
    }
    result = _validate_case(case, legacy, {}, {"items": []})
    assert result["block_verified"] is False
    assert result["recovery_complete"] is False
