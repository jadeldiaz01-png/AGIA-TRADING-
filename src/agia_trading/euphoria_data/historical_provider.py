from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Literal

from .rpc import RpcClient

Capability = Literal["STANDARD_ONLY", "MANAGED_CURSOR"]


def _sha256(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclasses.dataclass(frozen=True)
class HistoricalLogProvider:
    provider_id: str
    endpoint: str
    capability: Capability
    chain_id: int = 4326

    @property
    def endpoint_hash(self) -> str:
        return hashlib.sha256(self.endpoint.encode("utf-8")).hexdigest()

    def client(self) -> RpcClient:
        rpc = RpcClient(
            self.endpoint,
            user_agent=f"AGIA-TRADING-HISTORICAL/{self.provider_id} read-only",
        )
        rpc.verify_chain_id(self.chain_id)
        return rpc

    def capability_manifest(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "endpoint_hash": self.endpoint_hash,
            "chain_id": self.chain_id,
            "capability": self.capability,
            "effective_method": (
                "eth_getLogsWithCursor"
                if self.capability == "MANAGED_CURSOR"
                else "eth_getLogs"
            ),
        }


def provenance_record(
    provider: HistoricalLogProvider,
    *,
    effective_method: str,
    from_block: int,
    to_block: int,
    result_count: int,
    response: object,
    cursor_in: str | None = None,
    cursor_out: str | None = None,
    fallback_reason: str | None = None,
) -> dict:
    return {
        **provider.capability_manifest(),
        "effective_method": effective_method,
        "from_block": from_block,
        "to_block": to_block,
        "cursor_in": cursor_in,
        "cursor_out": cursor_out,
        "result_count": result_count,
        "response_sha256": _sha256(response),
        "fallback_reason": fallback_reason,
    }
