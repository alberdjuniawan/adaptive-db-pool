#!/usr/bin/env python3
"""Prepare the processed dataset from raw telemetry exports.

Pipeline :
 raw -> validation -> cleaning -> alignment -> features -> processed

Usage:
 python scripts/prepare_data.py [--raw-dir data/raw]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ml"))

from src.data.load import load_window, save_processed  # noqa: E402
from src.data.validate import ValidationError, validate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=None)
    args = parser.parse_args()

    raw_dir = args.raw_dir or REPO_ROOT / "data" / "raw"

    dataset = load_window(raw_dir)
    if dataset.empty:
        print("no usable raw telemetry found; run experiments/scripts/collect.sh first", file=sys.stderr)
        return 1

    try:
        report = validate(dataset)
    except ValidationError as exc:
        print(f"validation failed hard: {exc}", file=sys.stderr)
        return 1

    out_path = save_processed(dataset)

    report_path = raw_dir.parent / "processed" / "validation_report.json"
    with report_path.open("w") as handle:
        json.dump(report, handle, indent=2)

    print(f"dataset: {out_path} ({report['rows']} rows)")
    print(f"report : {report_path}")
    if not report["valid"]:
        print(f"warnings: {report['problems']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
