from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
from pathlib import Path

from .accounting import PROXY
from .rpc import RpcClient

COLLECTOR_VERSION = "rpc-resilience-v2"
MIN_WINDOW = 10
MAX_LOGS_RESPONSE = 1_000
MAX_RATE_LIMIT_RETRIES = 3
MAX_RATE_LIMIT_SPLITS = 16
SUCCESS_PAUSE_SECONDS = 0.05


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normalise_log(log: dict) -> dict:
    topics = [topic.lower() for topic in log.get("topics", [])]
    block = log["blockNumber"]
    index = log["logIndex"]
    return {
        "transaction_hash": log["transactionHash"].lower(),
        "block_number": int(block, 16) if isinstance(block, str) else int(block),
        "log_index": int(index, 16) if isinstance(index, str) else int(index),
        "topic0": topics[0] if topics else None,
        "correlation_key": topics[1] if len(topics) > 1 else None,
        "removed": bool(log.get("removed", False)),
    }


def _rate_limit_delay(exc: Exception, attempt: int) -> float | None:
    if not isinstance(exc, urllib.error.HTTPError) or exc.code != 429:
        return None
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return min(max(float(retry_after), 1.0), 60.0)
        except ValueError:
            pass
    base = min(float(2**attempt), 60.0)
    return min(base + ((attempt * 0.173) % 0.5), 60.0)


def _get_logs_window(rpc: RpcClient, topic0s: list[str], start: int, end: int) -> list[dict]:
    result = rpc.call("eth_getLogs", [{
        "fromBlock": hex(start), "toBlock": hex(end), "address": PROXY, "topics": [topic0s],
    }])
    return [_normalise_log(item) for item in result if not item.get("removed", False)]


def _fingerprint(rpc: RpcClient, topic0s: list[str], start: int, end: int) -> str:
    return _digest({
        "collector_version": COLLECTOR_VERSION, "endpoint": rpc.endpoint, "proxy": PROXY,
        "topics": sorted(topic.lower() for topic in topic0s), "from_block": start, "to_block": end,
    })


def _checkpoint_path(fingerprint: str) -> Path | None:
    root = os.getenv("AGIA_HISTORICAL_CHECKPOINT_DIR")
    return Path(root) / f"{fingerprint}.json" if root else None


def _write_checkpoint(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    body = {**payload, "checkpoint_sha256": _digest(payload)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_checkpoint(path: Path | None, fingerprint: str) -> dict | None:
    if path is None or not path.exists():
        return None
    body = json.loads(path.read_text(encoding="utf-8"))
    expected = body.pop("checkpoint_sha256", None)
    if expected != _digest(body):
        raise RuntimeError("historical checkpoint digest mismatch")
    if body.get("fingerprint") != fingerprint or body.get("collector_version") != COLLECTOR_VERSION:
        raise RuntimeError("historical checkpoint is incompatible with this collection")
    return body


def adaptive_get_logs(rpc: RpcClient, topic0s: list[str], start: int, end: int,
                      initial_window: int = 200_000) -> tuple[list[dict], list[dict]]:
    fingerprint = _fingerprint(rpc, topic0s, start, end)
    checkpoint_path = _checkpoint_path(fingerprint)
    checkpoint = _load_checkpoint(checkpoint_path, fingerprint)
    cursor, window = start, max(initial_window, MIN_WINDOW)
    logs, windows = [], []
    rate_limit_events = rate_limit_splits = truncation_splits = 0
    resumed = False
    if checkpoint:
        cursor, window = int(checkpoint["next_block"]), int(checkpoint["window"])
        logs, windows = list(checkpoint["logs"]), list(checkpoint["windows"])
        rate_limit_events = int(checkpoint["rate_limit_events"])
        rate_limit_splits = int(checkpoint["rate_limit_splits"])
        truncation_splits = int(checkpoint["truncation_splits"])
        resumed = True

    while cursor <= end:
        window_end = min(cursor + window - 1, end)
        rate_attempt = 0
        while True:
            try:
                batch = _get_logs_window(rpc, topic0s, cursor, window_end)
            except Exception as exc:
                delay = _rate_limit_delay(exc, rate_attempt)
                if delay is not None:
                    rate_limit_events += 1
                    if rate_attempt < MAX_RATE_LIMIT_RETRIES:
                        time.sleep(delay)
                        rate_attempt += 1
                        continue
                    if window <= MIN_WINDOW or rate_limit_splits >= MAX_RATE_LIMIT_SPLITS:
                        raise RuntimeError(
                            f"eth_getLogs rate limit persisted at minimum/resilience bound "
                            f"{cursor}-{window_end}; window={window}; splits={rate_limit_splits}"
                        ) from exc
                    window = max(window // 2, MIN_WINDOW)
                    window_end = min(cursor + window - 1, end)
                    rate_limit_splits += 1
                    rate_attempt = 0
                    continue
                if window <= MIN_WINDOW:
                    raise RuntimeError(f"eth_getLogs failed at minimum window {cursor}-{window_end}: {exc}") from exc
                window = max(window // 2, MIN_WINDOW)
                window_end = min(cursor + window - 1, end)
                rate_attempt = 0
                continue
            if len(batch) >= MAX_LOGS_RESPONSE:
                if window <= MIN_WINDOW:
                    raise RuntimeError(f"possible log truncation at minimum window {cursor}-{window_end}: received {len(batch)} logs")
                truncation_splits += 1
                window = max(window // 2, MIN_WINDOW)
                window_end = min(cursor + window - 1, end)
                rate_attempt = 0
                continue
            break

        logs.extend(batch)
        windows.append({
            "from_block": cursor, "to_block": window_end, "log_count": len(batch),
            "window_size": window_end - cursor + 1,
            "rate_limit_events_cumulative": rate_limit_events,
            "rate_limit_splits_cumulative": rate_limit_splits,
            "truncation_splits_cumulative": truncation_splits,
            "collector_version": COLLECTOR_VERSION, "resumed_from_checkpoint": resumed,
        })
        cursor = window_end + 1
        if len(batch) < 250 and window < initial_window:
            window = min(window * 2, initial_window)
        _write_checkpoint(checkpoint_path, {
            "collector_version": COLLECTOR_VERSION, "fingerprint": fingerprint,
            "next_block": cursor, "window": window, "logs": logs, "windows": windows,
            "rate_limit_events": rate_limit_events, "rate_limit_splits": rate_limit_splits,
            "truncation_splits": truncation_splits,
        })
        time.sleep(SUCCESS_PAUSE_SECONDS)
    logs.sort(key=lambda item: (item["block_number"], item["log_index"]))
    return logs, windows
