#!/usr/bin/env python3
"""Promote a trained model artifact to production in the registry.

The registry is intentionally lightweight: registry.json points at the
production artifact; rollback is re-promoting the previous version.

Usage:
 python scripts/promote.py --model random_forest
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "ml" / "models"
REGISTRY_DIR = REPO_ROOT / "ml" / "registry"
REGISTRY_PATH = REGISTRY_DIR / "registry.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="model name, e.g. random_forest")
    parser.add_argument("--note", default="", help="promotion reason")
    args = parser.parse_args()

    provenance_path = MODELS_DIR / f"{args.model}.provenance.json"
    artifact_path = MODELS_DIR / f"{args.model}.joblib"
    if not provenance_path.is_file() or not artifact_path.is_file():
        print(f"model artifacts not found for: {args.model}", file=sys.stderr)
        return 1

    with provenance_path.open() as handle:
        provenance = json.load(handle)

    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    registry = {"schema": 1, "history": []}
    if REGISTRY_PATH.is_file():
        registry = json.loads(REGISTRY_PATH.read_text())

    version = f"v{len(registry.get('history', [])) + 1:03d}"
    entry = {
        "version": version,
        "model": args.model,
        "artifact": str(artifact_path.relative_to(REPO_ROOT)),
        "feature_schema": provenance.get("feature_schema"),
        "git_commit": provenance.get("git_commit"),
        "dataset_version": provenance.get("dataset_version"),
        "metrics_level2": provenance.get("metrics_level2"),
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "status": "production",
        "note": args.note,
    }

    for previous in registry.get("history", []):
        previous["status"] = "superseded"

    registry.setdefault("history", []).append(entry)
    registry["current"] = entry
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))

    print(f"promoted {args.model} as {version} (production)")
    print(f"dataset_version={entry['dataset_version']} git={entry['git_commit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
