"""Model evaluation across the candidate catalog."""

from __future__ import annotations

import numpy as np

from ..models import MODEL_CATALOG, build_model
from .metrics import mae, mape, prediction_latency_ms, rmse


def evaluate_models(
    models_catalog: list[str] | None,
    X_train,
    y_train,
    X_validation,
    y_validation,
) -> list[dict]:
    """Fits every candidate on training data only and reports validation
    metrics plus prediction latency. Model selection is evidence-based
    ."""
    catalog = models_catalog or MODEL_CATALOG
    reports = []

    for name in catalog:
        try:
            model = build_model(name)
        except ImportError:
            reports.append({"model": name, "error": "dependency not installed"})
            continue

        model.fit(X_train, y_train)

        predictions = model.predict(X_validation)
        reports.append({
            "model": name,
            "mae": mae(y_validation, predictions),
            "rmse": rmse(y_validation, predictions),
            "mape": mape(y_validation, predictions),
            "latency_ms": prediction_latency_ms(model, X_validation),
            "feature_importances": _safe_importances(model),
        })

    return reports


def _safe_importances(model) -> list[float] | None:
    try:
        values = model.feature_importances
        return [float(v) for v in values]
    except AttributeError:
        return None


def select_best(reports: list[dict]) -> dict | None:
    """Picks the report with lowest RMSE among successful models."""
    valid = [report for report in reports if "rmse" in report]
    if not valid:
        return None
    return min(valid, key=lambda report: np.inf if np.isnan(report["rmse"]) else report["rmse"])
