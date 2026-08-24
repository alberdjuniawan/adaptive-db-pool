#!/usr/bin/env python3
"""Train candidate models and persist artifacts with provenance.

Usage:
 python scripts/train.py [--dataset data/processed/dataset.csv] [--models linear_regression random_forest xgboost mlp]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ml"))

from src import FEATURES, TARGET  # noqa: E402
from src.data.split import temporal_split  # noqa: E402
from src.evaluation.evaluate import evaluate_models, select_best  # noqa: E402
from src.models import build_model  # noqa: E402
import pandas as pd  # noqa: E402

MODELS_DIR = REPO_ROOT / "ml" / "models"


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "data" / "processed" / "dataset.csv")
    parser.add_argument("--models", nargs="*", default=None)
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"dataset not found: {args.dataset}", file=sys.stderr)
        print("run scripts/prepare_data.py first", file=sys.stderr)
        return 1

    dataset = pd.read_csv(args.dataset).sort_values(
        by=["window", "admission_limit"], kind="stable"
    )

    # Temporal split; scaler fitting happens inside pipelines fitted on
    # training rows only .
    train, validation, test = temporal_split(dataset)

    X_train = train[FEATURES]
    y_train = train[TARGET]
    X_validation = validation[FEATURES]
    y_validation = validation[TARGET]

    reports = evaluate_models(args.models, X_train, y_train, X_validation, y_validation)
    best = select_best(reports)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    trained_at = datetime.now(timezone.utc).isoformat()
    for report in reports:
        if "error" in report:
            print(f"{report['model']}: SKIPPED ({report['error']})")
            continue
        print(
            f"{report['model']}: MAE={report['mae']:.5f} "
            f"RMSE={report['rmse']:.5f} latency={report['latency_ms']:.2f}ms"
        )
        artifact_path = MODELS_DIR / f"{report['model']}.joblib"
        model = build_model(report["model"])
        model.fit(X_train, y_train)
        # Persist the plain sklearn/xgboost estimator, NOT our wrapper:
        # unpickling wrapper classes would require an identically-named
        # package at load time (the controller service also uses `src`),
        # which silently breaks online model loading.
        inner = getattr(model, "_pipeline", None) or getattr(model, "_model", None)
        joblib.dump(inner if inner is not None else model, artifact_path)

        provenance = {
            "model": report["model"],
            "artifact": str(artifact_path),
            "trained_at": trained_at,
            "git_commit": git_commit(),
            "features": FEATURES,
            "target": TARGET,
            "train_rows": int(len(train)),
            "validation_rows": int(len(validation)),
            "test_rows": int(len(test)),
            "metrics": {key: report[key] for key in ("mae", "rmse", "mape", "latency_ms") if key in report},
            "is_selected": bool(best and best["model"] == report["model"]),
        }
        with (MODELS_DIR / f"{report['model']}.provenance.json").open("w") as handle:
            json.dump(provenance, handle, indent=2)

    if best:
        print(f"selected: {best['model']} (lowest RMSE)")
    else:
        print("no model could be trained", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
