from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import CanonicalEvent, ReconciliationReport


def canonical_bytes(events: list[CanonicalEvent]) -> bytes:
    rows = [e.model_dump(mode="json", exclude_none=False) for e in events]
    rows.sort(key=lambda r: (r["block_number"], r["tx_hash"], -1 if r["log_index"] is None else r["log_index"]))
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return payload.encode("utf-8")


def freeze_dataset(events: list[CanonicalEvent], report: ReconciliationReport, out_dir: Path) -> dict:
    if report.unresolved_count != 0:
        raise RuntimeError(f"refusing freeze: unresolved_count={report.unresolved_count}")
    if not events:
        raise RuntimeError("refusing freeze: canonical dataset is empty")

    out_dir.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(events)
    dataset_sha256 = hashlib.sha256(payload).hexdigest()

    data_path = out_dir / "canonical-events.json"
    data_path.write_bytes(payload)

    manifest = {
        "dataset_id": "EUPHORIA-DATA-001",
        "schema_version": "1.0.0",
        "row_count": len(events),
        "unresolved_count": report.unresolved_count,
        "dataset_sha256": dataset_sha256,
        "frozen": True,
        "research_authorized": True,
        "paper_authorized": False,
        "testnet_authorized": False,
        "pilot_authorized": False,
        "live_authorized": False,
        "max_autonomous_capital_usd": 0,
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (out_dir / "frozen-dataset-manifest.json").write_bytes(manifest_bytes)
    (out_dir / "dataset.sha256").write_text(dataset_sha256 + "\n", encoding="utf-8")
    return manifest
