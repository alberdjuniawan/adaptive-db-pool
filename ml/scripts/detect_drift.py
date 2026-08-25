#!/usr/bin/env python3
"""Detect feature drift between the training dataset and live telemetry.

Compares exogenous feature distributions (KS test + PSI) between
data/processed/dataset.csv and a recent Prometheus window. Exit code:
  0 = no drift, 1 = inputs error, 2 = drift detected (retrain advised).

Usage:
 python scripts/detect_drift.py [--window 1800] [--step 30]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ml"))

from src import EXOGENOUS_FEATURES  # noqa: E402

PROM_URL = "http://localhost:9090"

# PromQL per exogenous signal; ratios derived after fetch.
RANGE_QUERIES = {
    "request_rate": "sum(rate(adaptive_db_pool_requests_total[{win}]))",
    "pool_acquired": "adaptive_db_pool_db_pool_acquired_connections",
    "pool_idle": "adaptive_db_pool_db_pool_idle_connections",
    "pool_max": "adaptive_db_pool_db_pool_max_connections",
    "rate_simple": 'sum(rate(adaptive_db_pool_requests_total{route="/api/workload/simple/:id"}[{win}]))',
    "rate_medium": 'sum(rate(adaptive_db_pool_requests_total{route="/api/workload/medium/:id"}[{win}]))',
    "rate_complex": 'sum(rate(adaptive_db_pool_requests_total{route="/api/workload/complex/:id"}[{win}]))',
    "rate_aggregation": 'sum(rate(adaptive_db_pool_requests_total{route="/api/workload/aggregation"}[{win}]))',
}


def fetch_recent(prometheus_url: str, window: str, step: str) -> pd.DataFrame:
    end = int(__import__("time").time())
    start = end - _seconds(window)
    frame = None

    for name, template in RANGE_QUERIES.items():
        payload = requests.get(
            f"{prometheus_url.rstrip('/')}/api/v1/query_range",
            params={
                "query": template.replace("{win}", window),
                "start": start,
                "end": end,
                "step": step,
            },
            timeout=10,
        ).json()
        result = payload.get("data", {}).get("result", [])
        if not result:
            continue
        series = pd.DataFrame(
            [{"bucket": int(float(ts) // 30 * 30), name: float(v)} for ts, v in result[0]["values"]]
        )
        bucketed = series.groupby("bucket")[name].mean()
        frame = bucketed.to_frame() if frame is None else frame.join(bucketed, how="outer")

    if frame is None:
        return pd.DataFrame()
    return frame.sort_index().ffill().dropna()


def _seconds(window: str) -> int:
    digits = "".join(ch for ch in window if ch.isdigit())
    unit = window[-1].lower()
    multiplier = {"s": 1, "m": 60, "h": 3600}.get(unit, 1)
    return int(digits or 900) * multiplier


def psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index over quantile bins of the reference."""
    edges = np.quantile(expected, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    e_hist = np.histogram(expected, edges)[0] / len(expected)
    a_hist = np.histogram(actual, edges)[0] / max(len(actual), 1)
    e_hist = np.clip(e_hist, 1e-6, None)
    a_hist = np.clip(a_hist, 1e-6, None)
    return float(np.sum((a_hist - e_hist) * np.log(a_hist / e_hist)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "data" / "processed" / "dataset.csv")
    parser.add_argument("--prometheus-url", default=PROM_URL)
    parser.add_argument("--window", default="30m")
    parser.add_argument("--step", default="30s")
    parser.add_argument("--psi-threshold", type=float, default=0.25)
    parser.add_argument("--ks-alpha", type=float, default=0.01)
    args = parser.parse_args()

    from scipy.stats import ks_2samp

    if not args.dataset.exists():
        print(f"reference dataset not found: {args.dataset}", file=sys.stderr)
        return 1
    reference = pd.read_csv(args.dataset)

    recent = fetch_recent(args.prometheus_url, args.window, args.step)
    if recent.empty:
        print("no recent telemetry; cannot evaluate drift", file=sys.stderr)
        return 1

    # Reconstruct ratio features on the live window; idle systems may
    # lack per-class rate series entirely.
    for class_name in ("simple", "medium", "complex", "aggregation"):
        if f"rate_{class_name}" not in recent.columns:
            recent[f"rate_{class_name}"] = 0.0
    if "request_rate" not in recent.columns:
        recent["request_rate"] = 0.0
    total = sum(recent.get(f"rate_{c}", 0.0) for c in ("simple", "medium", "complex", "aggregation"))
    for class_name in ("simple", "medium", "complex", "aggregation"):
        rates = recent.get(f"rate_{class_name}", 0.0)
        recent[f"{class_name}_ratio"] = rates / total.replace(0.0, np.nan)
    recent["pool_utilization"] = recent.get("pool_acquired", 0.0) / recent.get(
        "pool_max", np.nan
    ).replace(0, np.nan)
    recent = recent.replace([np.inf, -np.inf], np.nan).dropna(axis=1, how="all")

    report: dict = {"features": {}, "drifted_features": []}
    for feature in EXOGENOUS_FEATURES:
        if feature not in reference.columns or feature not in recent.columns:
            continue
        ref = pd.to_numeric(reference[feature], errors="coerce").dropna()
        cur = pd.to_numeric(recent[feature], errors="coerce").dropna()
        if len(ref) < 20 or len(cur) < 5 or ref.nunique() < 2:
            continue
        ks_stat = float(ks_2samp(ref, cur).statistic)
        p_value = float(ks_2samp(ref, cur).pvalue)
        psi_value = psi(ref.to_numpy(), cur.to_numpy())
        drifted = p_value < args.ks_alpha and psi_value > args.psi_threshold
        report["features"][feature] = {
            "ks_statistic": round(ks_stat, 4),
            "ks_p_value": round(p_value, 5),
            "psi": round(psi_value, 4),
            "drifted": bool(drifted),
        }
        if drifted:
            report["drifted_features"].append(feature)

    report["retrain_required"] = bool(report["drifted_features"])
    out_path = REPO_ROOT / "data" / "processed" / "drift_report.json"
    out_path.write_text(json.dumps(report, indent=2))

    print(json.dumps(report["features"], indent=2))
    print(
        f"drifted={report['drifted_features'] or 'none'} "
        f"retrain_required={report['retrain_required']} report={out_path}"
    )
    return 2 if report["retrain_required"] else 0


if __name__ == "__main__":
    sys.exit(main())
