"""Baseline A for the model comparison: Linear Regression."""

from __future__ import annotations

from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


class LinearRegressionModel:
    name = "linear_regression"

    def __init__(self) -> None:
        self._pipeline = Pipeline(
            steps=[
                ("scaler", StandardScaler()),
                ("regressor", LinearRegression()),
            ]
        )

    def fit(self, X, y) -> None:
        self._pipeline.fit(X, y)

    def predict(self, X):
        return self._pipeline.predict(X)

    @property
    def feature_importances(self):
        regressor: LinearRegression = self._pipeline.named_steps["regressor"]
        return regressor.coef_.tolist()
