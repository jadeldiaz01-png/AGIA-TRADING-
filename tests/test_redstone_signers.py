import pytest

from agia_trading.euphoria_data.redstone_signers import _encode_address, _selector, recover_signer


def test_policy_selectors_are_four_bytes() -> None:
    for signature in (
        "getUniqueSignersThreshold()",
        "getAuthorisedSignerIndex(address)",
        "getDataServiceId()",
    ):
        selector = _selector(signature)
        assert selector.startswith("0x")
        assert len(selector) == 10


def test_encode_address_is_left_zero_padded() -> None:
    address = "0x1111111111111111111111111111111111111111"
    encoded = _encode_address(address)
    assert len(encoded) == 32
    assert encoded[:12] == b"\x00" * 12
    assert encoded[-20:] == bytes.fromhex("11" * 20)


def test_recover_signer_rejects_non_ethereum_recovery_id() -> None:
    signature = "0x" + ("00" * 64) + "00"
    with pytest.raises(ValueError, match="27 or 28"):
        recover_signer("0x" + ("00" * 32), signature)
