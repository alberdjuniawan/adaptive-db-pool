"""Temporal train/validation/test split .

Random splits leak future information into training because the system
operates over time. We split chronologically instead.
"""

from __future__ import annotations

import pandas as pd


def temporal_split(
    dataset: pd.DataFrame,
    train_fraction: float = 0.7,
    validation_fraction: float = 0.15,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Splits preserving time order: earlier -> train, later -> test.

    The input frame must be sorted by its natural order (time).
    """
    n = len(dataset)
    if n < 10:
        raise ValueError(f"dataset too small to split: {n} rows")

    train_end = int(n * train_fraction)
    validation_end = int(n * (train_fraction + validation_fraction))

    train = dataset.iloc[:train_end]
    validation = dataset.iloc[train_end:validation_end]
    test = dataset.iloc[validation_end:]

    return train, validation, test
