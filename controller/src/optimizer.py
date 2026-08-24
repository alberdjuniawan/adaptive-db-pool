"""Optimizer: chooses the candidate limit minimizing predicted J
subject to the safety constraints."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .predictor import Predictor
from .telemetry import Telemetry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptimizationResult:
    best_limit: int
    best_cost: float
    evaluated: dict[int, float]


class GridOptimizer:
    """Evaluates a bounded grid of candidate limits and returns argmin.

    The grid spans +-`window` around the current limit in steps of
    `step`, clipped to [min_limit, max_limit]. This keeps evaluation
    cheap (a handful of model calls) while allowing the controller to
    traverse the operating range over successive cycles.
    """

    def __init__(self, window: int = 8, step: int = 2) -> None:
        if step < 1:
            raise ValueError("step must be >= 1")
        self._window = window
        self._step = step

    def optimize(
        self,
        predictor: Predictor,
        telemetry: Telemetry,
        current_limit: int,
        min_limit: int,
        max_limit: int,
    ) -> OptimizationResult:
        candidates = sorted(
            {
                candidate
                for candidate in range(
                    max(min_limit, current_limit - self._window),
                    min(max_limit, current_limit + self._window) + 1,
                    self._step,
                )
            }
        )
        if current_limit not in candidates:
            candidates.append(current_limit)

        evaluated: dict[int, float] = {}
        for candidate in candidates:
            try:
                evaluated[candidate] = predictor.predict(telemetry, candidate)
            except Exception as exc:  # noqa: BLE001 - prediction must never crash the loop
                logger.warning("prediction failed for %d: %s", candidate, exc)

        if not evaluated:
            return OptimizationResult(best_limit=current_limit, best_cost=float("inf"), evaluated={})

        best_limit = min(evaluated, key=evaluated.__getitem__)
        return OptimizationResult(
            best_limit=best_limit,
            best_cost=evaluated[best_limit],
            evaluated=evaluated,
        )
