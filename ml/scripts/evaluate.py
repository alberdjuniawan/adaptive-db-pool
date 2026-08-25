#!/usr/bin/env python3
"""Evaluate trained outcome models on the held-out temporal test set.

Reports Level-1 prediction metrics per outcome and Level-2 control
quality (recommended-limit accuracy against measured block optima).

Usage:
 python scripts/evaluate.py [--dataset data/processed/dataset.csv]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import joblib
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ml"))

from src import TARGET  # noqa: E402
from src.data.split import experiment_block_split  # noqa: E402
from src.evaluation.metrics import mae, rmse  # noqa: E402
from src.features.engineering import build_features  # noqa: E402
from src.optimization.optimizer import objective_from_outcomes  # noqa: E402

MODELS_DIR = REPO_ROOT / "ml" / "models"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "data" / "processed" / "dataset.csv")
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"dataset not found: {args.dataset}", file=sys.stderr)
        return 1

    dataset = pd.read_csv(args.dataset).sort_values(
        by=["experiment_id", "timestamp"], kind="stable"
    )
    _, _, test = experiment_block_split(dataset)
    if test.empty:
        print("no test block available", file=sys.stderr)
        return 1

    X_test = build_features(test)

    results = []
    for artifact_path in sorted(MODELS_DIR.glob("*.joblib")):
        artifact = joblib.load(artifact_path)
        if not isinstance(artifact, dict) or artifact.get("schema") != "v2":
            continue  # skip legacy single-estimator artifacts
        estimators = artifact.get("estimators", {})
        if not estimators:
            continue

        predictions = {
            outcome: estimator.predict(X_test)
            for outcome, estimator in estimators.items()
        }

        weights = {
            "w_latency": float(os.getenv("W_LATENCY", "1.0")),
            "w_error": float(os.getenv("W_ERROR", "3.0")),
            "w_wait": float(os.getenv("W_WAIT", "1.5")),
            "w_resource": float(os.getenv("W_RESOURCE", "0.05")),
        }
        predicted_j = objective_from_outcomes(pd.DataFrame(predictions), weights)

        limit_errors: list[int] = []
        regrets: list[float] = []

        if {"scenario", "workload_rate"} <= set(test.columns):
            groups = test.groupby(["scenario", "workload_rate"])
        else:
            groups = test.groupby("experiment_id")

        for _, block in groups:
            if block["admission_limit"].nunique() < 3:
                continue
            frame = block.assign(_pj=predicted_j).sort_values("admission_limit")
            chosen_row = frame.loc[frame["_pj"].idxmin()]
            true_optimum = frame.loc[frame[TARGET].idxmin()]
            limit_errors.append(
                abs(
                    int(chosen_row["admission_limit"])
                    - int(true_optimum["admission_limit"])
                )
            )
            regrets.append(float(chosen_row[TARGET] - true_optimum[TARGET]))

        report = {
            "model": artifact_path.stem,
            "schema": artifact.get("schema"),
            "git_commit": artifact.get("git_commit"),
            "dataset_version": artifact.get("dataset_version"),
            "test_rows": int(len(test)),
            "level1": {
                outcome: {
                    "mae": mae(test[outcome], preds),
                    "rmse": rmse(test[outcome], preds),
                }
                for outcome, preds in predictions.items()
                if outcome in test.columns
            },
            "level2": {
                "optimal_limit_mae": (
                    sum(limit_errors) / len(limit_errors) if limit_errors else None
                ),
                "mean_regret": sum(regrets) / len(regrets) if regrets else None,
                "per_block_limit_errors": limit_errors,
            },
        }
        results.append(report)
        print(
            f"{report['model']}: p99_RMSE={report['level1'].get('p99_latency', {}).get('rmse', 'n/a')} "
            f"limit_err={report['level2']['optimal_limit_mae']} "
            f"regret={report['level2']['mean_regret']}"
        )

    out_path = MODELS_DIR / "test_report.json"
    with out_path.open("w") as handle:
        json.dump(results, handle, indent=2)
    print(f"report: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
