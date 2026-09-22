from agia_trading.euphoria_data.historical_provider import (
    HistoricalLogProvider,
    provenance_record,
)


def test_standard_provider_manifest_is_hashable_and_read_only():
    p = HistoricalLogProvider(
        "blockscout-standard", "https://example.invalid/rpc", "STANDARD_ONLY"
    )
    m = p.capability_manifest()
    assert m["provider_id"] == "blockscout-standard"
    assert len(m["endpoint_hash"]) == 64
    assert m["chain_id"] == 4326
    assert m["effective_method"] == "eth_getLogs"


def test_managed_cursor_is_explicit_not_inferred():
    p = HistoricalLogProvider(
        "managed-fixture", "https://managed.invalid/rpc", "MANAGED_CURSOR"
    )
    assert p.capability_manifest()["effective_method"] == "eth_getLogsWithCursor"


def test_provenance_binds_response_and_fallback_reason():
    p = HistoricalLogProvider(
        "fallback-fixture", "https://fallback.invalid/rpc", "STANDARD_ONLY"
    )
    a = provenance_record(
        p,
        effective_method="eth_getLogs",
        from_block=10,
        to_block=20,
        result_count=1,
        response=[{"block": 12}],
        fallback_reason="primary_rate_limited",
    )
    b = provenance_record(
        p,
        effective_method="eth_getLogs",
        from_block=10,
        to_block=20,
        result_count=1,
        response=[{"block": 13}],
        fallback_reason="primary_rate_limited",
    )
    assert a["response_sha256"] != b["response_sha256"]
    assert a["fallback_reason"] == "primary_rate_limited"
