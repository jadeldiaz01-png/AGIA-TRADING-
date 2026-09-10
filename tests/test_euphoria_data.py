from pathlib import Path

import pytest

from agia_trading.euphoria_data.freeze import canonical_bytes, freeze_dataset
from agia_trading.euphoria_data.models import CanonicalEvent, RawChainRecord
from agia_trading.euphoria_data.reconcile import reconcile


def raw() -> RawChainRecord:
    return RawChainRecord(
        chain_id=4326,
        evm_block_number=1,
        evm_block_hash="0xabc",
        tx_hash="0xdef",
        tx_index=0,
        from_address="0x1",
        to_address="0x2",
        nonce=1,
        status=1,
        gas_used=21000,
        effective_gas_price=1,
        transaction_fee_wei=21000,
        contract_address="0x2",
        log_index=0,
        topic0="0x123",
        source_endpoint="fixture",
        extractor_git_sha="deadbeef",
    )


def event(confidence="VERIFIED") -> CanonicalEvent:
    return CanonicalEvent(
        chain_id=4326,
        tx_hash="0xdef",
        log_index=0,
        block_number=1,
        raw_record_sha256="a" * 64,
        abi_sha256="b" * 64,
        bytecode_sha256="c" * 64,
        extractor_git_sha="deadbeef",
        semantic_confidence=confidence,
    )


def test_unverified_semantics_fail_closed():
    report = reconcile([raw()], [event("UNKNOWN")])
    assert report.unresolved_count > 0


def test_verified_fixture_can_freeze(tmp_path: Path):
    events = [event()]
    report = reconcile([raw()], events)
    assert report.unresolved_count == 0
    manifest = freeze_dataset(events, report, tmp_path)
    assert manifest["frozen"] is True
    assert manifest["research_authorized"] is True
    assert manifest["live_authorized"] is False
    assert len(manifest["dataset_sha256"]) == 64


def test_freeze_is_deterministic():
    e1 = event()
    e2 = event().model_copy(update={"tx_hash": "0xaaa", "block_number": 2, "log_index": 1})
    assert canonical_bytes([e1, e2]) == canonical_bytes([e2, e1])


def test_nonzero_unresolved_refuses_freeze(tmp_path: Path):
    events = [event("PARTIAL")]
    report = reconcile([raw()], events)
    with pytest.raises(RuntimeError):
        freeze_dataset(events, report, tmp_path)
