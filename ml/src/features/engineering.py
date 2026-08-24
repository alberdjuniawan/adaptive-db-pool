"""Feature engineering .

Features must be timestamped and aligned; avoid leakage. This module
contains only row-wise derivations — windowing and alignment happen in
ml/src/data/load.py so train/serving stay consistent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import FEATURES


def build_features(dataset: pd.DataFrame) -> pd.DataFrame:
    """Returns the model input matrix in canonical FEATURE order."""
    missing = [column for column in FEATURES if column not in dataset.columns]
    if missing:
        raise KeyError(f"missing feature columns: {missing}")

    frame = dataset[FEATURES].copy()

    # Derived, leakage-free ratios.
    frame["utilization"] = frame["admission_active"] / frame["admission_limit"].clip(lower=1)
    frame.loc[frame["admission_limit"] <= 0, "utilization"] = 0.0
    frame["pool_utilization"] = np.where(
        (dataset["pool_acquired"] + dataset["pool_idle"]) > 0,
        dataset["pool_acquired"] / (dataset["pool_acquired"] + dataset["pool_idle"]).replace(0, 1),
        0.0,
    )

    return frame
