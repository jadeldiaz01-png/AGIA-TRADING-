from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error

from .historical_provider import HistoricalLogProvider, provenance_record
from .rpc_resilience_v2 import adaptive_get_logs as v2_get_logs

COLLECTOR_VERSION = "rpc-resilience-v3"


class TokenBucket:
    def __init__(self, rate_per_second: float = 1.0, capacity: float = 1.0):
        if rate_per_second <= 0 or capacity <= 0:
            raise ValueError("token bucket rate and capacity must be positive")
        self.rate = rate_per_second
        self.capacity = capacity
        self.tokens = capacity
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity, self.tokens + (now - self.updated) * self.rate
                )
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            time.sleep(wait)


class RateLimitedRpc:
    def __init__(self, rpc, limiter: TokenBucket):
        self._rpc = rpc
        self._limiter = limiter
        self.endpoint = rpc.endpoint

    def call(self, method, params):
        self._limiter.acquire()
        return self._rpc.call(method, params)


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def collect_standard(
    provider: HistoricalLogProvider,
    topic0s: list[str],
    start: int,
    end: int,
    *,
    limiter: TokenBucket | None = None,
    fallback_reason: str | None = None,
) -> tuple[list[dict], list[dict]]:
    if provider.capability != "STANDARD_ONLY":
        raise RuntimeError("standard collector requires STANDARD_ONLY capability")
    rpc = provider.client()
    limited = RateLimitedRpc(rpc, limiter or TokenBucket())
    logs, windows = v2_get_logs(limited, topic0s, start, end)
    enriched = []
    for window in windows:
        rows = [
            row
            for row in logs
            if window["from_block"] <= row["block_number"] <= window["to_block"]
        ]
        enriched.append(
            {
                **window,
                **provenance_record(
                    provider,
                    effective_method="eth_getLogs",
                    from_block=window["from_block"],
                    to_block=window["to_block"],
                    result_count=len(rows),
                    response=rows,
                    fallback_reason=fallback_reason,
                ),
                "collector_version": COLLECTOR_VERSION,
            }
        )
    return logs, enriched


def reconcile_same_range(primary: list[dict], secondary: list[dict]) -> dict:
    primary_sorted = sorted(
        primary, key=lambda x: (x["block_number"], x["log_index"], x["transaction_hash"])
    )
    secondary_sorted = sorted(
        secondary, key=lambda x: (x["block_number"], x["log_index"], x["transaction_hash"])
    )
    primary_sha = _digest(primary_sorted)
    secondary_sha = _digest(secondary_sorted)
    if primary_sha != secondary_sha:
        raise RuntimeError(
            f"historical provider reconciliation mismatch primary={primary_sha} secondary={secondary_sha}"
        )
    return {
        "status": "PASS",
        "primary_sha256": primary_sha,
        "secondary_sha256": secondary_sha,
        "log_count": len(primary_sorted),
    }


def collect_with_explicit_fallback(
    providers: list[HistoricalLogProvider],
    topic0s: list[str],
    start: int,
    end: int,
    *,
    limiter: TokenBucket | None = None,
) -> tuple[list[dict], list[dict], dict]:
    if not providers:
        raise RuntimeError("at least one explicitly configured provider is required")
    errors = []
    for index, provider in enumerate(providers):
        if provider.capability != "STANDARD_ONLY":
            raise RuntimeError(
                "MANAGED_CURSOR provider requires the dedicated cursor collector"
            )
        reason = None if index == 0 else "previous_explicit_provider_failed"
        try:
            logs, provenance = collect_standard(
                provider,
                topic0s,
                start,
                end,
                limiter=limiter,
                fallback_reason=reason,
            )
            return logs, provenance, {
                "selected_provider": provider.capability_manifest(),
                "fallback_used": index > 0,
                "prior_provider_errors": errors,
            }
        except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            errors.append(
                {
                    "provider": provider.capability_manifest(),
                    "error_type": type(exc).__name__,
                    "error_sha256": hashlib.sha256(str(exc).encode()).hexdigest(),
                }
            )
    raise RuntimeError(f"all explicitly configured historical providers failed: {errors}")
