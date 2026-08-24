"""Main tabular candidate: Random Forest (no scaling required)."""

from __future__ import annotations

from sklearn.ensemble import RandomForestRegressor


class RandomForestModel:
    name = "random_forest"

    def __init__(self, n_estimators: int = 300, max_depth: int | None = None) -> None:
        self._model = RandomForestRegressor(
            n_estimators=n_estimators,
            max_depth=max_depth,
            n_jobs=-1,
            random_state=42,
        )

    def fit(self, X, y) -> None:
        self._model.fit(X, y)

    def predict(self, X):
        return self._model.predict(X)

    @property
    def feature_importances(self):
        return self._model.feature_importances_.tolist()
