"""Feature engineering .

The outcome model consumes exogenous state plus one candidate limit.
Exogenous features exclude every quantity that is an effect of the
current limit (latency, waiting, utilization of the semaphore), so
counterfactual candidate queries stay valid.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import EXOGENOUS_FEATURES

# Model input: exogenous state + the candidate admission limit.
MODEL_INPUT = EXOGENOUS_FEATURES + ["admission_limit"]


def build_features(dataset: pd.DataFrame) -> pd.DataFrame:
    """Model input matrix using each row's own admission limit."""
    missing = [column for column in MODEL_INPUT if column not in dataset.columns]
    if missing:
        raise KeyError(f"missing feature columns: {missing}")
    return dataset[MODEL_INPUT].copy()


def candidate_frame(exogenous_row: dict, candidates) -> pd.DataFrame:
    """One row per candidate limit over a fixed exogenous state."""
    rows = []
    for candidate in candidates:
        row = dict(exogenous_row)
        row["admission_limit"] = float(candidate)
        rows.append(row)
    frame = pd.DataFrame(rows)
    missing = [column for column in MODEL_INPUT if column not in frame.columns]
    if missing:
        raise KeyError(f"exogenous state incomplete, missing: {missing}")
    return frame[MODEL_INPUT]
