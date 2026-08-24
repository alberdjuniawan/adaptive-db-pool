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
    """Wraps the supervised regression model trained by ml/pipeline.

    Expected artifact: a sklearn-compatible estimator over the canonical
    FEATURES vector defined in ml/src/__init__.py. Counterfactual
    candidates are evaluated by substituting `admission_limit` and
    recomputing `utilization` — exactly what ml/src/optimization/
    optimizer.py does offline.
    """

    def __init__(self, model_path: str) -> None:
        import joblib  # imported lazily: optional dependency at runtime

        if not os.path.exists(model_path):
            raise FileNotFoundError(model_path)
        self._model = joblib.load(model_path)
        logger.info("loaded ML predictor from %s", model_path)

    def predict(self, telemetry: Telemetry, candidate_limit: int) -> float:
        # Canonical order: ml/src/__init__.py::FEATURES.
        features = [
            telemetry.request_rate,
            telemetry.p95_latency,
            telemetry.p99_latency,
            telemetry.error_rate,
            telemetry.admission_active,
            telemetry.admission_waiting,
            float(candidate_limit),  # admission_limit -> counterfactual
            telemetry.admission_active / max(candidate_limit, 1),  # utilization
            telemetry.pool_acquired,
            telemetry.pool_idle,
            telemetry.pool_utilization,
        ]
        prediction = self._model.predict([features])
        return float(prediction[0])


def load_predictor(
    model_path: str,
    weights: tuple[float, float, float, float],
) -> tuple[Predictor, str]:
    """Returns (predictor, kind). Falls back to the analytic heuristic."""
    try:
        if model_path and os.path.exists(model_path):
            return JoblibModelPredictor(model_path), "ml"
    except Exception as exc:  # noqa: BLE001 - fail safe to heuristic
        logger.warning("ML model unavailable (%s); using heuristic", exc)
    heuristic = HeuristicPredictor(*weights)
    return heuristic, "heuristic"
