"""Model evaluation across the candidate catalog.

Two levels:
  Level 1 - prediction quality per outcome (MAE/RMSE).
  Level 2 - control quality: how close each model's recommended limit
            lands to the optimum measured inside every workload regime.
Model selection is evidence-based on Level 2 first .
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from ..features.engineering import build_features
from ..models import MODEL_CATALOG, build_model
from ..optimization.optimizer import objective_from_outcomes
from .metrics import mae, mape, rmse


def _weights() -> dict:
    return {
        "w_latency": float(os.getenv("W_LATENCY", "1.0")),
        "w_error": float(os.getenv("W_ERROR", "3.0")),
        "w_wait": float(os.getenv("W_WAIT", "1.5")),
        "w_resource": float(os.getenv("W_RESOURCE", "0.05")),
    }


def _regime_groups(frame: pd.DataFrame):
    """Group rows into regimes: same workload composition + intensity.

    A sweep visits multiple admission limits inside one regime; the
    measured J curve across those limits defines the true optimum.
    """
    if {"scenario", "workload_rate"} <= set(frame.columns):
        return frame.groupby(["scenario", "workload_rate"])
    return frame.groupby("experiment_id")


def control_quality(
    report: dict,
    dataset: pd.DataFrame,
) -> dict:
    """Recommended-limit accuracy against regime-wise measured optima.

    Computed over FULL regime curves (every limit of a sweep). With a
    single sweep session this is in-sample ranking evidence; true
    out-of-sample control quality requires multiple sessions .
    """
    from src import TARGET

    weights = _weights()
    limit_errors: list[int] = []
    regrets: list[float] = []

    for _, block in _regime_groups(dataset):
        if block["admission_limit"].nunique() < 3:
            continue
        X_block = build_features(block, report.get("_exogenous"))
        predicted = pd.DataFrame(
            {o: est.predict(X_block) for o, est in report["estimators"].items()},
            index=block.index,
        )
        predicted_j = objective_from_outcomes(predicted, weights)

        frame = block.assign(_pj=predicted_j).sort_values("admission_limit")
        best_pred = frame.loc[frame["_pj"].idxmin()]

        true_optimum = frame.loc[frame[TARGET].idxmin()]
        limit_errors.append(
            abs(int(best_pred["admission_limit"]) - int(true_optimum["admission_limit"]))
        )
        chosen = frame[frame["admission_limit"] == best_pred["admission_limit"]]
        measured_chosen = (
            chosen[TARGET].iloc[0] if not chosen.empty else true_optimum[TARGET]
        )
        regrets.append(float(measured_chosen - true_optimum[TARGET]))

    return {
        "optimal_limit_mae": (
            float(sum(limit_errors) / len(limit_errors)) if limit_errors else None
        ),
        "optimal_limit_errors": limit_errors,
        "mean_regret": float(sum(regrets) / len(regrets)) if regrets else None,
        "scope": "in_sample_regime_curves",
    }


def evaluate_models(
    models_catalog: list[str] | None,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    dataset: pd.DataFrame,
    exogenous: list[str] | None = None,
) -> list[dict]:
    """Fit one estimator per (model, outcome) on training data only.

    Level-2 control quality is evaluated over full regime curves from
    the complete dataset . `exogenous` restricts input state columns
    for ablation runs.
    """
    from src import OUTCOMES

    catalog = models_catalog or MODEL_CATALOG
    X_train = build_features(train, exogenous)
    X_validation = build_features(validation, exogenous)

    reports = []
    for name in catalog:
        try:
            build_model(name)
        except ImportError:
            reports.append({"model": name, "error": "dependency not installed"})
            continue

        estimators: dict = {}
        predictions: dict = {}
        outcome_metrics: dict = {}
        importances: dict = {}

        for outcome in OUTCOMES:
            if outcome not in train.columns:
                continue
            model = build_model(name)
            model.fit(X_train, train[outcome])
            predictions[outcome] = model.predict(X_validation)
            estimators[outcome] = getattr(model, "_pipeline", None) or getattr(
                model, "_model", None
            )
            outcome_metrics[outcome] = {
                "mae": mae(validation[outcome], predictions[outcome]),
                "rmse": rmse(validation[outcome], predictions[outcome]),
                "mape": mape(validation[outcome], predictions[outcome]),
            }
            try:
                importances[outcome] = [float(v) for v in model.feature_importances]
            except AttributeError:
                pass

        if not estimators:
            reports.append({"model": name, "error": "no trainable outcomes"})
            continue

        report = {
            "model": name,
            "outcomes": list(estimators),
            "outcome_metrics": outcome_metrics,
            "validation_predictions": predictions,
            "estimators": estimators,
            "feature_importances": importances or None,
            "_exogenous": exogenous,
        }
        report["control_quality"] = control_quality(report, dataset)
        reports.append(report)

    return reports


def select_best(reports: list[dict]) -> dict | None:
    """Rank by control quality: limit error, then regret, then p99 RMSE."""
    valid = [r for r in reports if "error" not in r]
    if not valid:
        return None

    def key(r: dict):
        cq = r["control_quality"]
        limit_err = (
            cq["optimal_limit_mae"] if cq["optimal_limit_mae"] is not None else np.inf
        )
        regret = cq["mean_regret"] if cq["mean_regret"] is not None else np.inf
        p99_rmse = r["outcome_metrics"].get("p99_latency", {}).get("rmse", np.inf)
        return (limit_err, regret, p99_rmse if not np.isnan(p99_rmse) else np.inf)

    return min(valid, key=key)
