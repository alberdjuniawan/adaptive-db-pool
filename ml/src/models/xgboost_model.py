"""Gradient boosting candidate: XGBoost (no scaling required)."""

from __future__ import annotations

from xgboost import XGBRegressor


class XGBoostModel:
    name = "xgboost"

    def __init__(
        self,
        n_estimators: int = 400,
        max_depth: int = 6,
        learning_rate: float = 0.05,
    ) -> None:
        self._model = XGBRegressor(
            n_estimators=n_estimators,
            max_depth=max_depth,
            learning_rate=learning_rate,
            subsample=0.9,
            colsample_bytree=0.9,
            random_state=42,
            n_jobs=-1,
        )

    def fit(self, X, y) -> None:
        self._model.fit(X, y, verbose=False)

    def predict(self, X):
        return self._model.predict(X)

    @property
    def feature_importances(self):
        return self._model.feature_importances_.tolist()
