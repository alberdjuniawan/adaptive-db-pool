"""Dataset validation gates ."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from .. import EXOGENOUS_FEATURES, IDENTITY_COLUMNS, OUTCOMES, TARGET


class ValidationError(Exception):
    pass


def validate(dataset: pd.DataFrame) -> dict:
    """Runs structural + statistical checks; returns a report dict."""
    problems: list[str] = []

    required = IDENTITY_COLUMNS + EXOGENOUS_FEATURES + OUTCOMES + [TARGET]
    missing = [column for column in required if column not in dataset.columns]
    if missing:
        raise ValidationError(f"missing columns: {missing}")

    if dataset.empty:
        raise ValidationError("dataset is empty")

    null_counts = dataset[required].isna().sum()
    if (null_counts > 0).any():
        problems.append(f"null values: {null_counts[null_counts > 0].to_dict()}")

    numeric_columns = (
        EXOGENOUS_FEATURES + OUTCOMES + [TARGET, "admission_limit", "timestamp", "workload_rate"]
    )
    for column in [c for c in numeric_columns if c in dataset.columns]:
        if not np.isfinite(pd.to_numeric(dataset[column], errors="coerce")).all():
            problems.append(f"non-finite values in {column}")

    if (dataset["admission_limit"] <= 0).any():
        problems.append("admission_limit contains non-positive values")

    min_rows = int(os.getenv("VALIDATE_MIN_ROWS", "100"))
    if len(dataset) < min_rows:
        problems.append(f"dataset too small: {len(dataset)} rows (< {min_rows})")

    if dataset["experiment_id"].nunique() < 2:
        problems.append(
            "fewer than 2 experiment blocks; experiment-based split will fall back to temporal"
        )

    report = {
        "rows": int(len(dataset)),
        "columns": list(dataset.columns),
        "experiments": int(dataset["experiment_id"].nunique()),
        "target_mean": float(dataset[TARGET].mean()),
        "target_std": float(dataset[TARGET].std()),
        "problems": problems,
        "valid": not problems,
    }
    return report
