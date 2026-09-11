from __future__ import annotations

import argparse
import json
from pathlib import Path

INFLOW_SELECTOR = "0x93ca625d"
OUTFLOW_SELECTOR = "0x21d5c9bb"
INFLOW_EVENT = "0x9f039a0ca58d6157d7b6914e2d60cedacf65fea21a365e93d708a5e5c25454f3"
OUTFLOW_EVENT = "0x90cacdb710d0777a8e5fb6387c414be82e2ffe362a703077117a2a995fce12c1"


def _events(tx: dict, topic0: str) -> list[dict]:
    return [event for event in tx.get("unknown_events", []) if event.get("topic0") == topic0]


def _correlation_key(event: dict) -> str | None:
    topics = event.get("indexed_topics", [])
    return topics[0].lower() if topics else None


def derive_sample_lifecycle_pairs(transactions: list[dict]) -> list[dict]:
    inflows: dict[str, list[dict]] = {}
    outflows: dict[str, list[dict]] = {}

    for tx in transactions:
        if tx.get("selector") == INFLOW_SELECTOR:
            for event in _events(tx, INFLOW_EVENT):
                key = _correlation_key(event)
                if key:
                    inflows.setdefault(key, []).append(tx)
        elif tx.get("selector") == OUTFLOW_SELECTOR:
            for event in _events(tx, OUTFLOW_EVENT):
                key = _correlation_key(event)
                if key:
                    outflows.setdefault(key, []).append(tx)

    pairs: list[dict] = []
    for key in sorted(set(inflows) & set(outflows)):
        for inflow in inflows[key]:
            later = [tx for tx in outflows[key] if tx["block_number"] > inflow["block_number"]]
            if not later:
                continue
            outflow = min(later, key=lambda tx: tx["block_number"])
            inflow_amount = inflow.get("usdm_in_raw", 0)
            outflow_amount = outflow.get("usdm_out_raw", 0)
            pairs.append(
                {
                    "correlation_key": key,
                    "inflow_tx_hash": inflow["tx_hash"],
                    "inflow_block": inflow["block_number"],
                    "inflow_selector": inflow["selector"],
                    "usdm_in_raw": inflow_amount,
                    "outflow_tx_hash": outflow["tx_hash"],
                    "outflow_block": outflow["block_number"],
                    "outflow_selector": outflow["selector"],
                    "usdm_out_raw": outflow_amount,
                    "block_delta": outflow["block_number"] - inflow["block_number"],
                    "amounts_equal": inflow_amount > 0 and inflow_amount == outflow_amount,
                    "outflow_has_redstone_payload": outflow.get("redstone") is not None,
                    "economic_role_verified": False,
                }
            )
    return pairs


def enrich(evidence: dict) -> dict:
    pairs = derive_sample_lifecycle_pairs(evidence.get("transactions", []))
    inflow_count = sum(
        1
        for tx in evidence.get("transactions", [])
        if tx.get("selector") == INFLOW_SELECTOR and _events(tx, INFLOW_EVENT)
    )
    outflow_count = sum(
        1
        for tx in evidence.get("transactions", [])
        if tx.get("selector") == OUTFLOW_SELECTOR and _events(tx, OUTFLOW_EVENT)
    )
    linkage_verified = (
        bool(pairs)
        and len(pairs) == inflow_count == outflow_count
        and all(pair["amounts_equal"] and pair["outflow_has_redstone_payload"] for pair in pairs)
    )

    evidence["sample_lifecycle_pairs"] = pairs
    evidence["sample_lifecycle_linkage_verified"] = linkage_verified
    evidence["sample_lifecycle_pair_count"] = len(pairs)
    evidence["sample_lifecycle_block_deltas"] = sorted({pair["block_delta"] for pair in pairs})
    evidence["sample_lifecycle_rule"] = (
        "Behavioral linkage is verified only for sampled transactions when the same raw indexed "
        "correlation key appears on the inflow and later outflow event, USDm amounts match exactly, "
        "and the outflow leg carries a structurally valid RedStone payload. This does not assign "
        "economic names such as open, settlement, win, fee, or PnL."
    )

    unresolved = [
        item for item in evidence.get("unresolved", []) if item != "settlement_lifecycle_linkage"
    ]
    if linkage_verified:
        unresolved.append("historical_lifecycle_linkage_completeness")
    else:
        unresolved.append("sample_lifecycle_linkage")
    evidence["unresolved"] = unresolved
    evidence["unresolved_count"] = len(unresolved)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    enriched = enrich(evidence)
    args.evidence.write_text(json.dumps(enriched, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(enriched, sort_keys=True))


if __name__ == "__main__":
    main()
