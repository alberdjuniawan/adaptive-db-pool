"""Offline optimizer: grid search over candidate limits using a trained
model. This mirrors controller/src/optimizer.py but operates offline on
recorded telemetry for research analysis."""

from __future__ import annotations

import numpy as np
import pandas as pd


def recommend_limit(
    model,
    window_features: dict,
    min_limit: int = 1,
    max_limit: int = 128,
    step: int = 2,
) -> tuple[int, float]:
    """Evaluates every candidate limit for one observation window.

    `window_features` maps the 11 base feature names to values; the
    candidate limit replaces `admission_limit` and recomputes derived
    utilization before prediction.
    """
    candidates = np.arange(min_limit, max_limit + 1, step)

    rows = []
    active = float(window_features["admission_active"])
    for candidate in candidates:
        row = dict(window_features)
        row["admission_limit"] = float(candidate)
        row["utilization"] = active / max(candidate, 1)
        rows.append(row)

    matrix = pd.DataFrame(rows)[list(window_features.keys())]
    costs = model.predict(matrix)
    best_index = int(np.argmin(costs))
    return int(candidates[best_index]), float(costs[best_index])
