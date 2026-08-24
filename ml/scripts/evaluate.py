#!/usr/bin/env python3
"""Evaluate trained models on the held-out temporal test set.

Usage:
 python scripts/evaluate.py [--dataset data/processed/dataset.csv]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ml"))

from src import FEATURES, TARGET  # noqa: E402
from src.data.split import temporal_split  # noqa: E402
from src.evaluation.metrics import mae, mape, prediction_latency_ms, rmse  # noqa: E402

MODELS_DIR = REPO_ROOT / "ml" / "models"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "data" / "processed" / "dataset.csv")
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"dataset not found: {args.dataset}", file=sys.stderr)
        return 1

    dataset = pd.read_csv(args.dataset).sort_values(
        by=["window", "admission_limit"], kind="stable"
    )
    _, _, test = temporal_split(dataset)

    X_test = test[FEATURES]
    y_test = test[TARGET]

    results = []
    for artifact in sorted(MODELS_DIR.glob("*.joblib")):
        model = joblib.load(artifact)
        predictions = model.predict(X_test)
        report = {
            "model": artifact.stem,
            "test_rows": int(len(test)),
            "mae": mae(y_test, predictions),
            "rmse": rmse(y_test, predictions),
            "mape": mape(y_test, predictions),
            "latency_ms": prediction_latency_ms(model, X_test),
        }
        results.append(report)
        print(
            f"{report['model']}: MAE={report['mae']:.5f} "
            f"RMSE={report['rmse']:.5f} latency={report['latency_ms']:.2f}ms"
        )

    out_path = MODELS_DIR / "test_report.json"
    with out_path.open("w") as handle:
        json.dump(results, handle, indent=2)
    print(f"report: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
