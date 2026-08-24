"""Load raw telemetry exports produced by experiments/scripts/collect.sh."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from .. import EXOGENOUS_FEATURES, IDENTITY_COLUMNS, OUTCOMES, TARGET, WEIGHT_DEFAULTS

RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parents[3] / "data" / "processed"

# Objective weights come from the environment so training ground truth
# and the online controller always share a single source of truth.
W_LATENCY = float(os.getenv("W_LATENCY", WEIGHT_DEFAULTS["w_latency"]))
W_ERROR = float(os.getenv("W_ERROR", WEIGHT_DEFAULTS["w_error"]))
W_WAIT = float(os.getenv("W_WAIT", WEIGHT_DEFAULTS["w_wait"]))
W_RESOURCE = float(os.getenv("W_RESOURCE", WEIGHT_DEFAULTS["w_resource"]))

# collect.sh writes one file per (timestamp, name) with explicit names.
_METRIC_FILES = {
    "request_rate": "request_rate",
    "p95_latency": "p95_latency",
    "p99_latency": "p99_latency",
    "error_rate": "error_rate",
    "admission_active": "admission_active",
    "admission_waiting": "admission_waiting",
    "admission_limit": "admission_limit",
    "pool_acquired": "pool_acquired",
    "pool_idle": "pool_idle",
    "pool_max": "pool_max",
    "rate_simple": "rate_simple",
    "rate_medium": "rate_medium",
    "rate_complex": "rate_complex",
    "rate_aggregation": "rate_aggregation",
}


def load_prometheus_export(path: Path) -> pd.DataFrame:
    """Flatten one Prometheus query_range export into (timestamp, value)."""
    with path.open() as handle:
        payload = json.load(handle)

    result = payload.get("data", {}).get("result", [])
    if not result:
        return pd.DataFrame(columns=["timestamp", "value"])

    rows = [
        {"timestamp": float(ts), "value": float(value)}
        for ts, value in result[0].get("values", [])
    ]
    return pd.DataFrame(rows)


def _load_metadata(raw_dir: Path, stamp: str) -> dict:
    """Read collection metadata for provenance identity columns."""
    path = raw_dir / f"{stamp}_metadata.json"
    if not path.is_file():
        return {
            "experiment_id": stamp,
            "scenario": "collection",
            "workload_rate": 0.0,
        }
    try:
        with path.open() as handle:
            payload = json.load(handle)
    except json.JSONDecodeError:
        return {
            "experiment_id": stamp,
            "scenario": "collection",
            "workload_rate": 0.0,
        }
    experiment = payload.get("experiment_id") or payload.get("collection_id") or stamp
    workload = payload.get("workload_configuration", {})
    return {
        "experiment_id": str(experiment),
        "scenario": str(workload.get("scenario", "unknown")),
        "workload_rate": float(workload.get("arrival_rate", 0.0) or 0.0),
    }


def load_window(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Assemble aligned feature windows from all raw exports.

    Exports are grouped by collection timestamp prefix; each group is
    joined on rounded timestamps and annotated with its collection
    metadata so rows carry their experiment identity.
    """
    groups: dict[str, list[Path]] = {}
    for path in sorted(raw_dir.glob("*.json")):
        stamp = path.name.split("_")[0]
        groups.setdefault(stamp, []).append(path)

    frames: list[pd.DataFrame] = []
    for stamp, paths in sorted(groups.items()):
        merged: pd.DataFrame | None = None
        for path in paths:
            name = path.stem[len(stamp) + 1 :]
            column = _METRIC_FILES.get(name)
            if column is None:
                continue

            series = load_prometheus_export(path)
            if series.empty:
                continue
            # Round to 5s buckets for alignment.
            series["bucket"] = (series["timestamp"] // 5 * 5).astype(int)
            bucketed = series.groupby("bucket")["value"].mean().rename(column)

            if merged is None:
                merged = bucketed.to_frame()
            else:
                merged = merged.join(bucketed, how="outer")

        if merged is None or merged.empty:
            continue
        if not {"admission_limit", "p99_latency"} <= set(merged.columns):
            continue

        merged = merged.sort_index().ffill().dropna(
            subset=["admission_limit", "p99_latency"]
        )

        # Exogenous state features.
        total_rate = (
            merged.get("rate_simple", 0.0)
            + merged.get("rate_medium", 0.0)
            + merged.get("rate_complex", 0.0)
            + merged.get("rate_aggregation", 0.0)
        )
        merged["request_rate"] = merged.get("request_rate", total_rate).fillna(0.0)
        for class_name in ("simple", "medium", "complex", "aggregation"):
            rates = merged.get(f"rate_{class_name}", 0.0)
            merged[f"{class_name}_ratio"] = rates / total_rate.replace(0.0, float("nan"))
        merged[[f"{c}_ratio" for c in ("simple", "medium", "complex", "aggregation")]] = (
            merged[[f"{c}_ratio" for c in ("simple", "medium", "complex", "aggregation")]]
            .fillna(0.0)
        )
        if "pool_max" not in merged.columns or (merged["pool_max"] <= 0).all():
            # Legacy exports without pool_max; approximate with the
            # observed acquired ceiling rather than dropping rows.
            merged["pool_max"] = max(float(merged.get("pool_acquired", pd.Series([0.0])).max()), 1.0)
        merged["pool_utilization"] = merged.get("pool_acquired", 0.0) / merged[
            "pool_max"
        ].replace(0, 1)

        # Outcomes per row (each row's outcomes belong to that row's limit).
        merged["wait_ratio"] = merged.get("admission_waiting", 0.0) / merged[
            "admission_limit"
        ].clip(lower=1)
        merged["error_rate"] = merged.get("error_rate", 0.0).fillna(0.0)

        # Ground-truth objective J for optimal-limit evaluation.
        merged[TARGET] = (
            W_LATENCY * merged["p99_latency"]
            + W_ERROR * merged["error_rate"]
            + W_WAIT * merged["wait_ratio"]
            + W_RESOURCE * merged["pool_utilization"]
        )

        merged["timestamp"] = merged.index.astype(int)
        for column, value in _load_metadata(raw_dir, stamp).items():
            merged[column] = value
        frames.append(merged.reset_index(drop=True))

    if not frames:
        columns = IDENTITY_COLUMNS + EXOGENOUS_FEATURES + OUTCOMES + [TARGET]
        return pd.DataFrame(columns=columns)

    return pd.concat(frames, ignore_index=True)


def save_processed(dataset: pd.DataFrame, name: str = "dataset.csv") -> Path:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / name
    dataset.to_csv(out_path, index=False)
    return out_path
