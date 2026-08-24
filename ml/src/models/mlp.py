"""Neural baseline: MLP. Numerical features are scaled with a scaler
fitted on training data only ."""

from __future__ import annotations

from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


class MLPModel:
    name = "mlp"

    def __init__(
        self,
        hidden_layer_sizes: tuple[int, ...] = (64, 32),
        max_iter: int = 500,
    ) -> None:
        self._pipeline = Pipeline(
            steps=[
                ("scaler", StandardScaler()),
                ("regressor", MLPRegressor(
                    hidden_layer_sizes=hidden_layer_sizes,
                    activation="relu",
                    early_stopping=True,
                    random_state=42,
                    max_iter=max_iter,
                )),
            ]
        )

    def fit(self, X, y) -> None:
        self._pipeline.fit(X, y)

    def predict(self, X):
        return self._pipeline.predict(X)

    @property
    def feature_importances(self):
        # Permutation-free surrogate: absolute first-layer weights.
        regressor: MLPRegressor = self._pipeline.named_steps["regressor"]
        return abs(regressor.coefs_[0]).mean(axis=1).tolist()
