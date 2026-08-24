"""Model registry: uniform fit/predict interface across candidates."""

from __future__ import annotations

from typing import Protocol

from .baseline import LinearRegressionModel
from .mlp import MLPModel
from .random_forest import RandomForestModel


class Model(Protocol):
    name: str

    def fit(self, X, y) -> None: ...
    def predict(self, X): ...


def build_model(name: str):
    """Factory used by scripts/train.py. Names are stable identifiers."""
    models: dict[str, callable] = {
        "linear_regression": LinearRegressionModel,
        "random_forest": RandomForestModel,
        "xgboost": None,  # imported lazily; optional dependency
        "mlp": MLPModel,
    }
    if name == "xgboost":
        from .xgboost_model import XGBoostModel

        return XGBoostModel()
    if name not in models or models[name] is None:
        raise ValueError(f"unknown model: {name}")
    return models[name]()


MODEL_CATALOG = ["linear_regression", "random_forest", "xgboost", "mlp"]
