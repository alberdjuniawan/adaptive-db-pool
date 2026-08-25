"""Predictor interface: ML model when available, heuristic otherwise.

The controller must never blindly trust the ML prediction; downstream
safety layers bound every output regardless of the predictor used.
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Protocol

from .telemetry import Telemetry

logger = logging.getLogger(__name__)


class Predictor(Protocol):
    """Predicts the objective cost J for a candidate admission limit."""

    def predict(self, telemetry: Telemetry, candidate_limit: int) -> float:
        ...


class HeuristicPredictor:
    """Analytic fallback used when no trained model artifact exists.

    J(candidate) = w_lat * p99 * (waiting pressure factor)
                 + w_err * error_rate
                 + w_wait * waiting / max(limit, 1)
                 + w_res * candidate / pool_max

    Increasing concurrency reduces waiting but raises contention; the
    quadratic contention term captures the well-known knee behavior.
    """

    def __init__(
        self,
        weight_latency: float,
        weight_error: float,
        weight_wait: float,
        weight_resource: float,
    ) -> None:
        self._w_latency = weight_latency
        self._w_error = weight_error
        self._w_wait = weight_wait
        self._w_resource = weight_resource
        self.info_labels = {
            "kind": "heuristic",
            "model": "analytic",
            "dataset_version": "none",
            "git_commit": "none",
        }

    def predict(self, telemetry: Telemetry, candidate_limit: int) -> float:
        limit = max(candidate_limit, 1)

        # Waiting pressure shrinks as capacity grows.
        waiting_pressure = telemetry.admission_waiting / limit

        # Contention grows super-linearly with utilization.
        utilization = telemetry.admission_active / limit
        contention = (telemetry.p95_latency + telemetry.p99_latency) / 2.0
        latency_cost = telemetry.p99_latency * (1.0 + 2.0 * utilization ** 2) + contention * utilization

        resource_cost = telemetry.pool_acquired / max(telemetry.pool_max, 1.0)

        return (
            self._w_latency * latency_cost
            + self._w_error * telemetry.error_rate
            + self._w_wait * waiting_pressure
            + self._w_resource * resource_cost
        )


class JoblibModelPredictor:
    """Outcome-model predictor (feature schema v2).

    The artifact holds one estimator per system outcome. Inputs are
    exogenous state plus the candidate limit; J is computed from the
    predicted outcomes with the same weights as training and offline
    evaluation — never predicted directly.
    """

    def __init__(self, model_path: str, weights: tuple[float, float, float, float]) -> None:
        import joblib  # imported lazily: optional dependency at runtime

        if not os.path.exists(model_path):
            raise FileNotFoundError(model_path)
        artifact = joblib.load(model_path)
        if not isinstance(artifact, dict) or "estimators" not in artifact:
            raise ValueError("unsupported artifact schema; retrain with feature schema v2")
        self._estimators = artifact["estimators"]
        self._features = list(artifact["exogenous_features"])
        self._weights = dict(
            zip(("w_latency", "w_error", "w_wait", "w_resource"), weights)
        )
        self.info_labels = {
            "kind": "ml",
            "model": str(artifact.get("model", "unknown")),
            "dataset_version": str(artifact.get("dataset_version") or "unknown"),
            "git_commit": str(artifact.get("git_commit") or "unknown"),
        }
        logger.info(
            "loaded ML outcome predictor from %s (schema %s, outcomes %s)",
            model_path,
            artifact.get("schema"),
            sorted(self._estimators),
        )

    def _exogenous(self, telemetry: Telemetry) -> dict:
        total = (
            telemetry.simple_rate
            + telemetry.medium_rate
            + telemetry.complex_rate
            + telemetry.aggregation_rate
        )

        def ratio(rate: float) -> float:
            return rate / total if total else 0.0

        return {
            "request_rate": telemetry.request_rate,
            "simple_ratio": ratio(telemetry.simple_rate),
            "medium_ratio": ratio(telemetry.medium_rate),
            "complex_ratio": ratio(telemetry.complex_rate),
            "aggregation_ratio": ratio(telemetry.aggregation_rate),
            "pool_acquired": telemetry.pool_acquired,
            "pool_idle": telemetry.pool_idle,
            "pool_utilization": telemetry.pool_utilization,
        }

    def predict(self, telemetry: Telemetry, candidate_limit: int) -> float:
        import pandas as pd

        row = self._exogenous(telemetry)
        row["admission_limit"] = float(candidate_limit)
        frame = pd.DataFrame([row])[self._features + ["admission_limit"]]

        outcomes = {
            name: float(estimator.predict(frame)[0])
            for name, estimator in self._estimators.items()
        }
        return float(
            self._weights["w_latency"] * outcomes["p99_latency"]
            + self._weights["w_error"] * outcomes["error_rate"]
            + self._weights["w_wait"] * outcomes["wait_ratio"]
            + self._weights["w_resource"] * outcomes["pool_utilization"]
        )


def load_predictor(
    model_path: str,
    weights: tuple[float, float, float, float],
) -> tuple[Predictor, str]:
    """Returns (predictor, kind). Falls back to the analytic heuristic."""
    try:
        if model_path and os.path.exists(model_path):
            return JoblibModelPredictor(model_path, weights), "ml"
    except Exception as exc:  # noqa: BLE001 - fail safe to heuristic
        logger.warning("ML model unavailable (%s); using heuristic", exc)
    heuristic = HeuristicPredictor(*weights)
    return heuristic, "heuristic"
