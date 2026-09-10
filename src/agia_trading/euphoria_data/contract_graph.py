from __future__ import annotations

import hashlib


ERC1967_IMPLEMENTATION_SLOT = (
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
)


def normalize_address(value: str) -> str:
    raw = value.lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    if len(raw) != 40 or any(c not in "0123456789abcdef" for c in raw):
        raise ValueError("invalid EVM address")
    return "0x" + raw


def implementation_from_storage_word(storage_word: str) -> str:
    raw = storage_word.lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    if len(raw) != 64 or any(c not in "0123456789abcdef" for c in raw):
        raise ValueError("ERC1967 storage word must be 32 bytes")
    return normalize_address(raw[-40:])


def runtime_bytecode_sha256(bytecode: str) -> str:
    raw = bytecode.lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    if not raw or len(raw) % 2 or any(c not in "0123456789abcdef" for c in raw):
        raise ValueError("runtime bytecode must be non-empty even-length hex")
    return hashlib.sha256(bytes.fromhex(raw)).hexdigest()


def verify_expected_implementation(storage_word: str, expected: str) -> None:
    observed = implementation_from_storage_word(storage_word)
    if observed != normalize_address(expected):
        raise RuntimeError(
            f"ERC1967 implementation mismatch: expected {normalize_address(expected)}, "
            f"observed {observed}"
        )
