from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

FUNCTION_SELECTORS = ["0x21d5c9bb", "0x93ca625d"]
EVENT_TOPICS = [
    "0x163aa6d2d8e5248ce93dbd22509af93e5f27f8e0edc15c9e26f2867c593e0870",
    "0x90cacdb710d0777a8e5fb6387c414be82e2ffe362a703077117a2a995fce12c1",
    "0x9f039a0ca58d6157d7b6914e2d60cedacf65fea21a365e93d708a5e5c25454f3",
]
FOURBYTE_BASE = "https://www.4byte.directory/api/v1"
USER_AGENT = "AGIA-TRADING-semantic-adjudicator/1.0"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20.0) as response:
        return json.loads(response.read())


def _lookup(path: str, field: str, value: str) -> list[str]:
    query = urllib.parse.urlencode({field: value})
    payload = _get_json(f"{FOURBYTE_BASE}/{path}/?{query}")
    return sorted({item["text_signature"] for item in payload.get("results", [])})


def collect() -> dict:
    function_candidates = {
        selector: _lookup("signatures", "hex_signature", selector)
        for selector in FUNCTION_SELECTORS
    }
    event_candidates = {
        topic: _lookup("event-signatures", "hex_signature", topic)
        for topic in EVENT_TOPICS
    }

    unresolved = []
    for selector, candidates in function_candidates.items():
        if len(candidates) != 1:
            unresolved.append(
                {
                    "kind": "function_selector",
                    "id": selector,
                    "reason": "signature database does not yield exactly one candidate",
                    "candidate_count": len(candidates),
                }
            )
        else:
            unresolved.append(
                {
                    "kind": "function_selector",
                    "id": selector,
                    "reason": "single reverse-lookup candidate is insufficient without ABI-shape and behavioral proof",
                    "candidate_count": 1,
                }
            )

    for topic, candidates in event_candidates.items():
        if len(candidates) != 1:
            unresolved.append(
                {
                    "kind": "event_topic0",
                    "id": topic,
                    "reason": "signature database does not yield exactly one candidate",
                    "candidate_count": len(candidates),
                }
            )
        else:
            unresolved.append(
                {
                    "kind": "event_topic0",
                    "id": topic,
                    "reason": "single reverse-lookup candidate is insufficient without indexed/data layout proof",
                    "candidate_count": 1,
                }
            )

    return {
        "gate": "P0-EUPHORIA-SEMANTIC-ADJUDICATION-001",
        "status": "PARTIAL_EVIDENCE",
        "sources": {
            "signature_database": "4byte.directory",
            "redstone_euphoria_reference": "https://blog.redstone.finance/2026/01/13/euphoria-redstone-bolt/",
        },
        "function_candidates": function_candidates,
        "event_candidates": event_candidates,
        "documented_oracle_feed_labels": ["ETH", "ETH_HIGH", "ETH_LOW"],
        "documented_oracle_provider": "RedStone Bolt",
        "observed_asset": {
            "symbol": "USDm",
            "address": "0xfafddbb3fc7688494971a79cc65dca3ef82079e7",
        },
        "state_machine": {
            "input_funds": "PARTIAL_EVIDENCE",
            "position_or_market_state": "UNKNOWN",
            "resolution": "UNKNOWN",
            "gross_payout": "PARTIAL_EVIDENCE",
            "fee": "UNKNOWN",
            "net_payout": "UNKNOWN",
        },
        "selector_semantics_verified": False,
        "event_semantics_verified": False,
        "oracle_dependency_verified": False,
        "settlement_semantics_verified": False,
        "fee_payout_semantics_verified": False,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "live_authorized": False,
        "max_autonomous_capital_usd": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = collect()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
