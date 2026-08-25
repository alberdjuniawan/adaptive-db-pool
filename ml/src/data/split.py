"""Train/validation/test split .

Random splits leak future information into training because the system
operates over time. Splits must respect time and experiment boundaries.
"""

from __future__ import annotations

import pandas as pd


def temporal_split(
    dataset: pd.DataFrame,
    train_fraction: float = 0.7,
    validation_fraction: float = 0.15,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Row-wise chronological fallback for datasets without identity."""
    n = len(dataset)
    if n < 10:
        raise ValueError(f"dataset too small to split: {n} rows")

    train_end = int(n * train_fraction)
    validation_end = int(n * (train_fraction + validation_fraction))

    train = dataset.iloc[:train_end]
    validation = dataset.iloc[train_end:validation_end]
    test = dataset.iloc[validation_end:]

    return train, validation, test


def experiment_block_split(
    dataset: pd.DataFrame,
    id_column: str = "experiment_id",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split whole experiment blocks: earlier blocks → train/val, last → test.

    Rows inside one experiment share workload regime; splitting by rows
    would leak that regime across train and test.
    """
    if id_column not in dataset.columns:
        return temporal_split(dataset)

    order = (
        dataset.groupby(id_column)["timestamp"]
        .min()
        .sort_values()
        .index.tolist()
    )
    if len(order) < 3:
        return temporal_split(dataset)

    test_block = order[-1]
    validation_block = order[-2]

    train = dataset[~dataset[id_column].isin([validation_block, test_block])]
    validation = dataset[dataset[id_column] == validation_block]
    test = dataset[dataset[id_column] == test_block]

    if any(part.empty for part in (train, validation, test)):
        return temporal_split(dataset)
    return train, validation, test
