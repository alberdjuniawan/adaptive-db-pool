"""Regression metrics ."""

from __future__ import annotations

import time

import numpy as np


def mae(y_true, y_pred) -> float:
    return float(np.mean(np.abs(np.asarray(y_true) - np.asarray(y_pred))))


def rmse(y_true, y_pred) -> float:
    return float(np.sqrt(np.mean((np.asarray(y_true) - np.asarray(y_pred)) ** 2)))


def mape(y_true, y_pred, epsilon: float = 1e-9) -> float:
    """MAPE is only meaningful when targets are strictly positive."""
    y_true = np.asarray(y_true)
    if (y_true <= 0).any():
        return float("nan")
    denominator = np.maximum(np.abs(y_true), epsilon)
    return float(np.mean(np.abs((y_true - np.asarray(y_pred)) / denominator)) * 100.0)


def prediction_latency_ms(model, X, repeats: int = 20) -> float:
    """Median single-call latency in milliseconds (batch of len(X))."""
    timings = []
    for _ in range(repeats):
        start = time.perf_counter()
        model.predict(X)
        timings.append((time.perf_counter() - start) * 1000.0)
    return float(np.median(timings))
