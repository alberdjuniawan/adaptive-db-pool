"""Offline optimizer: grid search over candidate limits using a trained
outcome model. Mirrors controller/src/predictor.py so training-time
evaluation and online inference compute J identically."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..features.engineering import candidate_frame

WEIGHT_KEYS = ("w_latency", "w_error", "w_wait", "w_resource")


def objective_from_outcomes(outcomes: pd.DataFrame, weights: dict) -> np.ndarray:
    """J = w_lat*p99 + w_err*error + w_wait*wait_ratio + w_res*pool_util."""
    return (
        weights["w_latency"] * outcomes["p99_latency"]
        + weights["w_error"] * outcomes["error_rate"]
        + weights["w_wait"] * outcomes["wait_ratio"]
        + weights["w_resource"] * outcomes["pool_utilization"]
    ).to_numpy()


def recommend_limit(
    artifact: dict,
    exogenous_row: dict,
    candidates,
    weights: dict | None = None,
) -> dict:
    """Predict outcomes for every candidate, compute J, return argmin.

    Returns {"best_limit", "best_cost", "curve": {c: J}, "outcomes": DataFrame}.
    """
    weights = weights or {
        "w_latency": 1.0,
        "w_error": 3.0,
        "w_wait": 1.5,
        "w_resource": 0.05,
    }
    candidate_list = [float(c) for c in candidates]
    frame = candidate_frame(exogenous_row, candidate_list)

    columns = {}
    for outcome, estimator in artifact["estimators"].items():
        columns[outcome] = estimator.predict(frame)
    outcomes = pd.DataFrame(columns, index=frame.index)
    costs = objective_from_outcomes(outcomes, weights)

    best_index = int(np.argmin(costs))
    curve = {int(c): float(j) for c, j in zip(candidate_list, costs)}
    return {
        "best_limit": int(candidate_list[best_index]),
        "best_cost": float(costs[best_index]),
        "curve": curve,
        "outcomes": outcomes.assign(objective_j=costs),
    }
