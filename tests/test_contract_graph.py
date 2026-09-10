import pytest

from agia_trading.euphoria_data.contract_graph import (
    ERC1967_IMPLEMENTATION_SLOT,
    implementation_from_storage_word,
    normalize_address,
    runtime_bytecode_sha256,
    verify_expected_implementation,
)

EXPECTED_IMPLEMENTATION = "0x95d2a2cb2e9f1efb89f435752bbc8ccf61c3485a"


def test_erc1967_slot_constant_is_canonical() -> None:
    assert ERC1967_IMPLEMENTATION_SLOT == (
        "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
    )


def test_extract_implementation_from_storage_word() -> None:
    word = "0x" + ("00" * 12) + EXPECTED_IMPLEMENTATION[2:]
    assert implementation_from_storage_word(word) == EXPECTED_IMPLEMENTATION


def test_implementation_mismatch_fails_closed() -> None:
    word = "0x" + ("00" * 12) + EXPECTED_IMPLEMENTATION[2:]
    with pytest.raises(RuntimeError):
        verify_expected_implementation(word, "0x0000000000000000000000000000000000000001")


def test_bytecode_hash_is_deterministic() -> None:
    assert runtime_bytecode_sha256("0x6001600055") == runtime_bytecode_sha256("6001600055")


def test_invalid_address_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_address("0x1234")
