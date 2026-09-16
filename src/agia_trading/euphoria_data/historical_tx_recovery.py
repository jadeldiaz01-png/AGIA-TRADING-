from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PROXY = "0x12759afca690637b425ffba3265f0dc2f6242a8d"
BLOCKSCOUT_BASE = "https://megaeth.blockscout.com"
MAX_RETRIES = 6


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _request_json(url: str) -> Any:
    headers = {
        "Accept": "application/json",
        "User-Agent": "AGIA-TRADING-HISTORICAL-TX-RECOVERY/1.0 read-only",
    }
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt >= MAX_RETRIES:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = float(retry_after) if retry_after else min(2**attempt, 60)
            time.sleep(max(delay, 1.0))
        except (TimeoutError, urllib.error.URLError):
            if attempt >= MAX_RETRIES:
                raise
            time.sleep(min(2**attempt, 30))
    raise RuntimeError("unreachable")


def _legacy_txinfo_url(base: str, tx_hash: str) -> str:
    query = urllib.parse.urlencode(
        {"module": "transaction", "action": "gettxinfo", "txhash": tx_hash}
    )
    return f"{base.rstrip('/')}/api?{query}"


def _v2_tx_url(base: str, tx_hash: str) -> str:
    return f"{base.rstrip('/')}/api/v2/transactions/{tx_hash}"


def _v2_logs_url(base: str, tx_hash: str) -> str:
    return f"{base.rstrip('/')}/api/v2/transactions/{tx_hash}/logs"


def _normalise_topic(value: Any) -> str:
    return str(value or "").lower()


def _normalise_legacy_logs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    logs = result.get("logs")
    if not isinstance(logs, list):
        return []
    normalised = []
    for log in logs:
        topics = [_normalise_topic(topic) for topic in log.get("topics", [])]
        normalised.append(
            {
                "address": str(log.get("address", "")).lower(),
                "topics": topics,
                "data": str(log.get("data", "0x")).lower(),
            }
        )
    return normalised


def _normalise_v2_logs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    normalised = []
    for log in items:
        topics = [_normalise_topic(topic) for topic in log.get("topics", [])]
        normalised.append(
            {
                "address": str((log.get("address") or {}).get("hash", log.get("address", ""))).lower()
                if isinstance(log.get("address"), dict)
                else str(log.get("address", "")).lower(),
                "topics": topics,
                "data": str(log.get("data", "0x")).lower(),
            }
        )
    return normalised


def _validate_case(
    case: dict[str, Any],
    legacy: dict[str, Any],
    v2_tx: dict[str, Any],
    v2_logs: dict[str, Any],
) -> dict[str, Any]:
    tx_hash = case["tx_hash"].lower()
    expected_block = int(case["block_number"])
    expected_topic0 = case["expected_topic0"].lower()
    expected_key = case["correlation_key"].lower()

    legacy_result = legacy.get("result") if isinstance(legacy, dict) else None
    legacy_result = legacy_result if isinstance(legacy_result, dict) else {}

    observed_hashes = {
        str(legacy_result.get("hash", "")).lower(),
        str(v2_tx.get("hash", "")).lower() if isinstance(v2_tx, dict) else "",
    }
    hash_verified = tx_hash in observed_hashes

    observed_blocks: set[int] = set()
    for value in (
        legacy_result.get("blockNumber"),
        v2_tx.get("block_number") if isinstance(v2_tx, dict) else None,
    ):
        if value is None or value == "":
            continue
        try:
            observed_blocks.add(int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value))
        except (TypeError, ValueError):
            continue
    block_verified = expected_block in observed_blocks

    to_values = {
        str(legacy_result.get("to", "")).lower(),
        str((v2_tx.get("to") or {}).get("hash", "")).lower()
        if isinstance(v2_tx, dict) and isinstance(v2_tx.get("to"), dict)
        else str(v2_tx.get("to", "")).lower() if isinstance(v2_tx, dict) else "",
    }
    proxy_verified = PROXY in to_values

    logs = _normalise_legacy_logs(legacy)
    logs.extend(_normalise_v2_logs(v2_logs if isinstance(v2_logs, dict) else {}))
    matching_logs = []
    for log in logs:
        topics = log["topics"]
        if len(topics) >= 2 and topics[0] == expected_topic0 and topics[1] == expected_key:
            matching_logs.append(log)
    event_verified = bool(matching_logs)

    complete = hash_verified and block_verified and proxy_verified and event_verified
    return {
        "tx_hash": tx_hash,
        "correlation_key": expected_key,
        "kind": case["kind"],
        "expected_block": expected_block,
        "hash_verified": hash_verified,
        "block_verified": block_verified,
        "proxy_verified": proxy_verified,
        "event_verified": event_verified,
        "matching_log_count": len(matching_logs),
        "recovery_complete": complete,
        "source": "megaeth.blockscout.com",
        "source_role": "secondary indexed recovery; accepted only when reconciled against certified expected tx/block/proxy/topic/key",
        "legacy_success": legacy_result.get("success"),
        "legacy_input": legacy_result.get("input"),
        "v2_status": v2_tx.get("status") if isinstance(v2_tx, dict) else None,
        "v2_method": v2_tx.get("method") if isinstance(v2_tx, dict) else None,
    }


def recover_case(case: dict[str, Any], base: str = BLOCKSCOUT_BASE) -> dict[str, Any]:
    tx_hash = case["tx_hash"]
    legacy = _request_json(_legacy_txinfo_url(base, tx_hash))
    v2_tx = _request_json(_v2_tx_url(base, tx_hash))
    v2_logs = _request_json(_v2_logs_url(base, tx_hash))
    return _validate_case(case, legacy, v2_tx, v2_logs)


def recover_manifest(manifest: dict[str, Any], base: str = BLOCKSCOUT_BASE) -> dict[str, Any]:
    if manifest.get("chain_id") != 4326:
        raise RuntimeError("wrong chain in recovery manifest")
    results = [recover_case(case, base) for case in manifest.get("cases", [])]
    unresolved = [item for item in results if not item["recovery_complete"]]
    return {
        "gate": "P0-EUPHORIA-HISTORICAL-TX-RECOVERY-001",
        "status": "PASS" if not unresolved else "PARTIAL_EVIDENCE",
        "chain_id": 4326,
        "case_count": len(results),
        "recovered_count": len(results) - len(unresolved),
        "unresolved_count": len(unresolved),
        "results": results,
        "results_sha256": _digest(results),
        "economic_semantics_verified": False,
        "deterministic_pnl_verified": False,
        "backfill_authorized": False,
        "paper_authorized": False,
        "testnet_authorized": False,
        "live_authorized": False,
        "max_autonomous_capital_usd": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--blockscout-base", default=BLOCKSCOUT_BASE)
    args = parser.parse_args()
    manifest = json.loads(args.input.read_text(encoding="utf-8"))
    evidence = recover_manifest(manifest, args.blockscout_base)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in evidence.items() if k != "results"}, sort_keys=True))


if __name__ == "__main__":
    main()
