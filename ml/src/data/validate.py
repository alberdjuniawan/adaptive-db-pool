"""Dataset validation gates ."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import FEATURES, TARGET


class ValidationError(Exception):
    pass


def validate(dataset: pd.DataFrame) -> dict:
    """Runs structural + statistical checks; returns a report dict."""
    problems: list[str] = []

    missing = [column for column in FEATURES + [TARGET] if column not in dataset.columns]
    if missing:
        raise ValidationError(f"missing columns: {missing}")

    if dataset.empty:
        raise ValidationError("dataset is empty")

    null_counts = dataset[FEATURES + [TARGET]].isna().sum()
    if (null_counts > 0).any():
        problems.append(f"null values: {null_counts[null_counts > 0].to_dict()}")

    for column in FEATURES + [TARGET]:
        if not np.isfinite(dataset[column]).all():
            problems.append(f"non-finite values in {column}")

    if (dataset["admission_limit"] <= 0).any():
        problems.append("admission_limit contains non-positive values")

    if len(dataset) < 100:
        problems.append(f"dataset too small: {len(dataset)} rows (< 100)")

    report = {
        "rows": int(len(dataset)),
        "columns": list(dataset.columns),
        "target_mean": float(dataset[TARGET].mean()),
        "target_std": float(dataset[TARGET].std()),
        "problems": problems,
        "valid": not problems,
    }
    return report
