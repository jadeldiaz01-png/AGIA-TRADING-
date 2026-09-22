import pytest

from agia_trading.euphoria_data import rpc_resilience_v3 as v3
from agia_trading.euphoria_data.historical_provider import HistoricalLogProvider


def _log(block):
    return {
        "transaction_hash": f"0x{block:064x}",
        "block_number": block,
        "log_index": 0,
        "topic0": "0x01",
        "correlation_key": "0x02",
        "removed": False,
    }


def test_reconciliation_requires_identical_canonical_logs():
    assert v3.reconcile_same_range([_log(10)], [_log(10)])["status"] == "PASS"
    with pytest.raises(RuntimeError, match="reconciliation mismatch"):
        v3.reconcile_same_range([_log(10)], [_log(11)])


def test_v3_standard_output_is_equivalent_to_v2(monkeypatch):
    provider = HistoricalLogProvider(
        "fixture-standard", "https://fixture.invalid/rpc", "STANDARD_ONLY"
    )

    class Rpc:
        endpoint = provider.endpoint
        def call(self, method, params):
            return []
    monkeypatch.setattr(provider.__class__, "client", lambda self: Rpc())
    expected = ([_log(10)], [{"from_block": 10, "to_block": 20, "log_count": 1}])
    monkeypatch.setattr(v3, "v2_get_logs", lambda rpc, topics, start, end: expected)

    logs, provenance = v3.collect_standard(
        provider, ["0x01"], 10, 20, limiter=v3.TokenBucket(1000, 1)
    )
    assert logs == expected[0]
    assert provenance[0]["response_sha256"]
    assert provenance[0]["provider_id"] == "fixture-standard"
    assert provenance[0]["collector_version"] == "rpc-resilience-v3"


def test_fallback_is_only_to_explicit_provider(monkeypatch):
    p1 = HistoricalLogProvider("primary", "https://one.invalid", "STANDARD_ONLY")
    p2 = HistoricalLogProvider("secondary", "https://two.invalid", "STANDARD_ONLY")

    def fake(provider, topics, start, end, **kwargs):
        if provider.provider_id == "primary":
            raise RuntimeError("rate limited")
        return [_log(10)], [{"fallback_reason": kwargs["fallback_reason"]}]

    monkeypatch.setattr(v3, "collect_standard", fake)
    logs, provenance, selection = v3.collect_with_explicit_fallback(
        [p1, p2], ["0x01"], 10, 20
    )
    assert logs == [_log(10)]
    assert provenance[0]["fallback_reason"] == "previous_explicit_provider_failed"
    assert selection["fallback_used"] is True
    assert selection["selected_provider"]["provider_id"] == "secondary"


def test_managed_cursor_cannot_silently_use_standard_path():
    p = HistoricalLogProvider("managed", "https://managed.invalid", "MANAGED_CURSOR")
    with pytest.raises(RuntimeError, match="dedicated cursor collector"):
        v3.collect_with_explicit_fallback([p], ["0x01"], 1, 2)
