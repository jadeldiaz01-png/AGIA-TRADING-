from agia_trading.euphoria_data.adjudicate_semantics import (
    _split_types,
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
