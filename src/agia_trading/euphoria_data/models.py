from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SemanticConfidence = Literal["VERIFIED", "PARTIAL", "UNKNOWN"]


class RawChainRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int = Field(ge=1)
    evm_block_number: int = Field(ge=0)
    evm_block_hash: str
    mini_block_number: int | None = Field(default=None, ge=0)
    mini_block_timestamp: datetime | None = None
    tx_hash: str
    tx_index: int = Field(ge=0)
    from_address: str
    to_address: str | None = None
    nonce: int = Field(ge=0)
    status: int | None = Field(default=None, ge=0, le=1)
    gas_used: int | None = Field(default=None, ge=0)
    effective_gas_price: int | None = Field(default=None, ge=0)
    transaction_fee_wei: int | None = Field(default=None, ge=0)
    contract_address: str | None = None
    log_index: int | None = Field(default=None, ge=0)
    topic0: str | None = None
    topics: list[str] = Field(default_factory=list)
    data: str | None = None
    source_endpoint: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extractor_git_sha: str
    schema_version: str = "1.0.0"

    @field_validator("tx_hash", "evm_block_hash")
    @classmethod
    def require_hex_hash(cls, value: str) -> str:
        if not value.startswith("0x"):
            raise ValueError("chain hashes must be 0x-prefixed")
        return value.lower()


class CanonicalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int
    tx_hash: str
    log_index: int | None
    protocol: str = "EUPHORIA"
    contract_role: str = "UNKNOWN"
    event_type: str = "UNKNOWN"
    trader: str | None = None
    market: str | None = None
    direction: str | None = None
    stake_atomic: int | None = None
    quote_atomic: int | None = None
    payout_atomic: int | None = None
    fee_atomic: int | None = None
    pnl_atomic: int | None = None
    block_number: int
    mini_block_number: int | None = None
    event_timestamp: datetime | None = None
    raw_record_sha256: str
    abi_sha256: str | None = None
    bytecode_sha256: str | None = None
    extractor_git_sha: str
    semantic_confidence: SemanticConfidence = "UNKNOWN"


class ReconciliationIssue(BaseModel):
    code: str
    key: str
    detail: str
    blocking: bool = True


class ReconciliationReport(BaseModel):
    dataset_id: str = "EUPHORIA-DATA-001"
    total_raw_records: int
    total_canonical_events: int
    issues: list[ReconciliationIssue]
    unresolved_count: int

    @field_validator("unresolved_count")
    @classmethod
    def nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("unresolved_count cannot be negative")
        return value
