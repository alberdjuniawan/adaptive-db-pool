"""Load raw telemetry exports produced by experiments/scripts/collect.sh."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .. import FEATURES, TARGET

RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parents[3] / "data" / "processed"


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


def load_window(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Assemble one aligned feature window from all raw exports.

    Exports are grouped by collection timestamp prefix; each group is
    joined on the rounded timestamp so features stay aligned without
    leakage across windows.
    """
    groups: dict[str, list[Path]] = {}
    for path in sorted(raw_dir.glob("*.json")):
        stamp = path.name.split("_")[0]
        groups.setdefault(stamp, []).append(path)

    frames: list[pd.DataFrame] = []
    for stamp, paths in groups.items():
        merged: pd.DataFrame | None = None
        for path in paths:
            slug = path.stem[len(stamp) + 1 :]
            column = _slug_to_metric(slug)
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

        merged = merged.sort_index().ffill().dropna()
        if not {"admission_limit", "p99_latency"} <= set(merged.columns):
            continue

        # Feature engineering happens here so windows are self-contained.
        merged["request_rate"] = merged.get("request_rate", 0.0)
        merged["error_rate"] = merged.get("error_rate", 0.0)
        merged["utilization"] = merged["admission_active"] / merged["admission_limit"].clip(lower=1)
        if "pool_max" in merged.columns:
            merged["pool_utilization"] = merged["pool_acquired"] / merged["pool_max"].replace(0, 1)
        else:
            # db_pool_max is static; utilization from acquired alone.
            merged["pool_utilization"] = merged.get("pool_acquired", 0.0) / max(
                float(merged.get("pool_acquired", pd.Series([0.0])).max()), 1.0
            )

        # Observed objective J for the window .
        merged[TARGET] = (
            1.0 * merged["p99_latency"]
            + 3.0 * merged.get("error_rate", 0.0)
            + 1.5 * merged["admission_waiting"] / merged["admission_limit"].clip(lower=1)
            + 0.05 * merged["pool_utilization"]
        )

        merged["window"] = stamp
        frames.append(merged.reset_index(drop=True))

    if not frames:
        return pd.DataFrame(columns=FEATURES + [TARGET])

    return pd.concat(frames, ignore_index=True)


def save_processed(dataset: pd.DataFrame, name: str = "dataset.csv") -> Path:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / name
    dataset.to_csv(out_path, index=False)
    return out_path


_METRIC_SLUGS = {
    # Slugs as produced by collect.sh: non-alphanumerics collapse to "_"
    # and the name is truncated to 60 characters.
    "rate_adaptive_db_pool_requests_total_30s___": "request_rate",
    "histogram_quantile_0_95__sum_rate_adaptive_db_pool_request_d": "p95_latency",
    "histogram_quantile_0_99__sum_rate_adaptive_db_pool_request_d": "p99_latency",
    "adaptive_db_pool_request_errors_total_30s___c": "error_rate",
    "adaptive_db_pool_admission_active_": "admission_active",
    "adaptive_db_pool_admission_waiting_": "admission_waiting",
    "adaptive_db_pool_admission_limit_": "admission_limit",
    "adaptive_db_pool_db_pool_acquired_connections_": "pool_acquired",
    "adaptive_db_pool_db_pool_idle_connections_": "pool_idle",
}


def _slug_to_metric(slug: str) -> str | None:
    return _METRIC_SLUGS.get(slug)
