import json
import urllib.error

import pytest

from agia_trading.euphoria_data import rpc_resilience_v2 as r


class FakeRpc:
    endpoint = "https://example.invalid/rpc"

    def __init__(self, responder):
        self.responder, self.calls = responder, []

    def call(self, method, params):
        self.calls.append((method, params))
        return self.responder(method, params)

    def verify_chain_id(self, expected):
        assert expected == 4326


def _raw(block, index):
    return {
        "transactionHash": "0xabc",
        "blockNumber": hex(block),
        "logIndex": hex(index),
        "topics": ["0x01", "0x02"],
        "removed": False,
    }


def _http_429():
    return urllib.error.HTTPError(
        "https://example.invalid", 429, "Too Many Requests", {}, None
    )


def test_v2_remains_standard_only_reference_collector(monkeypatch):
    monkeypatch.setattr(r.time, "sleep", lambda _: None)
    rpc = FakeRpc(lambda method, params: [])
    logs, windows = r.adaptive_get_logs(
        rpc, ["0x01"], 100, 109, initial_window=10
    )
    assert logs == []
    assert windows[0]["collector_version"] == "rpc-resilience-v2"
    assert rpc.calls[0][0] == "eth_getLogs"


def test_rpc_resilience_v2_preserves_log_semantics(monkeypatch):
    monkeypatch.setattr(r.time, "sleep", lambda _: None)

    def responder(method, params):
        start, end = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
        return [_raw(b, 1) for b in range(start, end + 1) if b in {10, 12, 15}]

    logs, _ = r.adaptive_get_logs(
        FakeRpc(responder), ["0x01"], 10, 15, initial_window=2
    )
    assert [(x["block_number"], x["log_index"]) for x in logs] == [
        (10, 1),
        (12, 1),
        (15, 1),
    ]


def test_persistent_429_splits_window_then_recovers(monkeypatch):
    monkeypatch.setattr(r.time, "sleep", lambda _: None)
    attempts = {"large": 0}

    def responder(method, params):
        start, end = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
        if end - start + 1 > 10:
            attempts["large"] += 1
            raise _http_429()
        return []

    _, windows = r.adaptive_get_logs(
        FakeRpc(responder), ["0x01"], 1, 20, initial_window=20
    )
    assert attempts["large"] == r.MAX_RATE_LIMIT_RETRIES + 1
    assert windows[0]["window_size"] == 10
    assert windows[-1]["rate_limit_splits_cumulative"] >= 1


def test_checkpoint_resume_and_digest_validation(monkeypatch, tmp_path):
    monkeypatch.setattr(r.time, "sleep", lambda _: None)
    monkeypatch.setenv("AGIA_HISTORICAL_CHECKPOINT_DIR", str(tmp_path))
    state = {"fail": True}

    def responder(method, params):
        start = int(params[0]["fromBlock"], 16)
        if start >= 11 and state["fail"]:
            raise RuntimeError("provider down")
        return []

    rpc = FakeRpc(responder)
    with pytest.raises(RuntimeError):
        r.adaptive_get_logs(rpc, ["0x01"], 1, 20, initial_window=10)
    checkpoint = next(tmp_path.glob("*.json"))
    state["fail"] = False
    _, windows = r.adaptive_get_logs(rpc, ["0x01"], 1, 20, initial_window=10)
    assert windows[-1]["resumed_from_checkpoint"] is True

    body = json.loads(checkpoint.read_text())
    body["next_block"] = 999
    checkpoint.write_text(json.dumps(body))
    with pytest.raises(RuntimeError, match="digest mismatch"):
        r.adaptive_get_logs(rpc, ["0x01"], 1, 20, initial_window=10)
