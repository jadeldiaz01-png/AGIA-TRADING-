from agia_trading.euphoria_data.adjudicate_semantics import (
    _split_types,
    event_shape_compatible,
    function_selector,
    signature_hash,
    signature_shape_compatible,
)


def test_split_types_handles_dynamic_tuple_shape() -> None:
    assert _split_types("f(address,uint256,bytes)") == ["address", "uint256", "bytes"]


def test_static_signature_requires_enough_words() -> None:
    calldata = "0x12345678" + ("00" * 32)
    assert signature_shape_compatible("f(uint256)", calldata)
    assert not signature_shape_compatible("f(uint256,uint256)", calldata)


def test_dynamic_offset_must_point_inside_payload() -> None:
    good = "0x12345678" + ("00" * 31) + "20" + ("00" * 32)
    bad = "0x12345678" + ("00" * 30) + "0100" + ("00" * 32)
    assert signature_shape_compatible("f(bytes)", good)
    assert not signature_shape_compatible("f(bytes)", bad)


def test_ethereum_keccak_known_erc20_signatures() -> None:
    assert function_selector("transfer(address,uint256)") == "0xa9059cbb"
    assert signature_hash("Transfer(address,address,uint256)") == (
        "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    )


def test_event_shape_static_arguments() -> None:
    log = {
        "topics": ["0xtopic0", "0x" + "00" * 32],
        "data": "0x" + "00" * 32,
    }
    assert event_shape_compatible("BalanceUpdate(address,uint256)", log)
    assert not event_shape_compatible("TooMany(address,uint256,uint256)", log)
