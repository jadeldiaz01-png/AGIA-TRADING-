from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter

from .freeze import freeze_dataset
from .models import CanonicalEvent, RawChainRecord
from .reconcile import reconcile


def _load_list(path: Path, model_type):
    data = json.loads(path.read_text(encoding="utf-8"))
    return TypeAdapter(list[model_type]).validate_python(data)


def certify(raw_path: Path, canonical_path: Path, out_dir: Path) -> int:
    raw = _load_list(raw_path, RawChainRecord)
    canonical = _load_list(canonical_path, CanonicalEvent)
    report = reconcile(raw, canonical)

    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "reconciliation-ledger.json"
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), sort_keys=True, indent=2),
        encoding="utf-8",
    )

    if report.unresolved_count != 0:
        print("EUPHORIA_DATA_NOT_CERTIFIED")
        print(f"unresolved_count={report.unresolved_count}")
        return 2

    manifest = freeze_dataset(canonical, report, out_dir)
    print("EUPHORIA_DATA_001_CERTIFIED")
    print("unresolved_count=0")
    print(f"dataset_sha256={manifest['dataset_sha256']}")
    print("research_authorized=true")
    print("live_authorized=false")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(certify(args.raw, args.canonical, args.out))


if __name__ == "__main__":
    main()
