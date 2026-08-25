#!/usr/bin/env python3
"""Train outcome models and persist artifacts with provenance.

The model predicts per-candidate system outcomes [p99_latency,
wait_ratio, error_rate, pool_utilization] from exogenous state plus a
candidate limit. The objective J is computed afterwards, never learned
directly, so candidate queries remain counterfactually valid.

Usage:
 python scripts/train.py [--dataset data/processed/dataset.csv] [--models linear_regression random_forest xgboost mlp]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ml"))

from src import (  # noqa: E402
    EXOGENOUS_FEATURES,
    FEATURE_SCHEMA,
    OUTCOMES,
)
from src.data.split import experiment_block_split  # noqa: E402
from src.evaluation.evaluate import evaluate_models, select_best  # noqa: E402
from src.features.engineering import build_features  # noqa: E402
import pandas as pd  # noqa: E402

MODELS_DIR = REPO_ROOT / "ml" / "models"


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def dataset_version() -> str | None:
    """Read the DVC content hash of the prepared dataset."""
    lock_path = REPO_ROOT / "dvc.lock"
    try:
        import yaml

        lock = yaml.safe_load(lock_path.read_text())
        for out in lock["stages"]["prepare_data"]["outs"]:
            if out["path"].endswith("dataset.csv"):
                return out.get("md5")
    except Exception:  # noqa: BLE001 - provenance is best-effort
        return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "data" / "processed" / "dataset.csv")
    parser.add_argument("--models", nargs="*", default=None)
    parser.add_argument(
        "--ablation",
        action="store_true",
        help="retrain the selected model without each feature group",
    )
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"dataset not found: {args.dataset}", file=sys.stderr)
        print("run scripts/prepare_data.py first", file=sys.stderr)
        return 1

    dataset = pd.read_csv(args.dataset).sort_values(
        by=["experiment_id", "timestamp"], kind="stable"
    )

    # Whole-experiment blocks keep regimes from leaking across splits .
    train, validation, test = experiment_block_split(dataset)
    print(
        f"blocks: train={train['experiment_id'].nunique()} "
        f"val={validation['experiment_id'].nunique()} "
        f"test={test['experiment_id'].nunique()}"
    )

    X_train = build_features(train)

    reports = evaluate_models(args.models, train, validation, dataset)
    best = select_best(reports)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    trained_at = datetime.now(timezone.utc).isoformat()
    ds_version = dataset_version()
    selected_artifact_path = None

    for report in reports:
        if "error" in report:
            print(f"{report['model']}: SKIPPED ({report['error']})")
            continue

        level1 = {o: report["outcome_metrics"][o] for o in report["outcomes"]}
        level2 = report["control_quality"]
        print(
            f"{report['model']}: "
            f"p99_RMSE={level1['p99_latency']['rmse']:.4f} "
            f"limit_err={level2['optimal_limit_mae']} "
            f"regret={level2['mean_regret']}"
        )
        report.pop("validation_predictions", None)

        artifact_path = MODELS_DIR / f"{report['model']}.joblib"
        artifact = {
            "schema": FEATURE_SCHEMA,
            "model": report["model"],
            "trained_at": trained_at,
            "git_commit": git_commit(),
            "dataset_version": ds_version,
            "exogenous_features": EXOGENOUS_FEATURES,
            "outcomes": report["outcomes"],
            "estimators": report.pop("estimators"),
        }
        joblib.dump(artifact, artifact_path)
        if best and best["model"] == report["model"]:
            selected_artifact_path = artifact_path

        provenance = {
            "model": report["model"],
            "artifact": str(artifact_path),
            "feature_schema": FEATURE_SCHEMA,
            "dataset_version": ds_version,
            "git_commit": git_commit(),
            "features": EXOGENOUS_FEATURES,
            "outcomes": report["outcomes"],
            "train_rows": int(len(train)),
            "validation_rows": int(len(validation)),
            "test_rows": int(len(test)),
            "metrics_level1": report["outcome_metrics"],
            "metrics_level2": report["control_quality"],
            "is_selected": bool(best and best["model"] == report["model"]),
        }
        with (MODELS_DIR / f"{report['model']}.provenance.json").open("w") as handle:
            json.dump(provenance, handle, indent=2)

    if not best or selected_artifact_path is None:
        print("no model could be trained", file=sys.stderr)
        return 1

    # Quality gate: control quality decides promotion eligibility.
    gate_max_err = float(os.getenv("ML_GATE_MAX_LIMIT_ERROR", "12"))
    gate_max_rmse = float(os.getenv("ML_GATE_MAX_P99_RMSE", "5.0"))
    best_report = next(r for r in reports if r["model"] == best["model"])
    limit_err = best_report["control_quality"]["optimal_limit_mae"]
    p99_rmse = best_report["outcome_metrics"]["p99_latency"]["rmse"]

    passed = (
        limit_err is not None
        and limit_err <= gate_max_err
        and p99_rmse is not None
        and p99_rmse <= gate_max_rmse
    )
    print(f"selected: {best['model']} (control-quality ranking)")
    print(
        f"quality gate: limit_err={limit_err} (max {gate_max_err}), "
        f"p99_rmse={p99_rmse:.4f} (max {gate_max_rmse}) -> "
        f"{'PASS' if passed else 'FAIL'}"
    )

    summary = {
        "selected": best["model"],
        "artifact": str(selected_artifact_path),
        "gate_passed": bool(passed),
        "gate": {"max_limit_error": gate_max_err, "max_p99_rmse": gate_max_rmse},
    }
    with (MODELS_DIR / "training_summary.json").open("w") as handle:
        json.dump(summary, handle, indent=2)

    if args.ablation:
        groups = {
            "without_workload_mix": [
                "simple_ratio",
                "medium_ratio",
                "complex_ratio",
                "aggregation_ratio",
            ],
            "without_pool_state": ["pool_acquired", "pool_idle", "pool_utilization"],
            "without_intensity": ["request_rate"],
        }
        ablation = {"full": best_report["control_quality"]["optimal_limit_mae"]}
        for group_name, dropped in groups.items():
            exog_subset = [f for f in EXOGENOUS_FEATURES if f not in dropped]
            variant = evaluate_models(
                [best["model"]], train, validation, dataset, exogenous=exog_subset
            )
            ablation[group_name] = variant[0]["control_quality"]["optimal_limit_mae"]

        with (MODELS_DIR / "ablation_report.json").open("w") as handle:
            json.dump(ablation, handle, indent=2)
        print(f"ablation (optimal_limit_mae): {ablation}")

    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
