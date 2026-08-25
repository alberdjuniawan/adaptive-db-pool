#!/usr/bin/env python3
"""Offline prediction: recommend an admission limit from live Prometheus
telemetry using a trained outcome model (the manual counterpart of the
online controller service).

Usage:
 python scripts/predict.py [--prometheus-url http://localhost:9090] [--backend-url http://localhost:8080] [--apply]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import joblib
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ml"))

from src import EXOGENOUS_FEATURES  # noqa: E402
from src.optimization.optimizer import recommend_limit  # noqa: E402

MODEL_PATH = REPO_ROOT / "ml" / "models" / "predictor.joblib"


def fetch_telemetry(prometheus_url: str) -> dict | None:
    def query(expr: str) -> float:
        response = requests.get(
            f"{prometheus_url.rstrip('/')}/api/v1/query",
            params={"query": expr},
            timeout=5,
        )
        results = response.json().get("data", {}).get("result", [])
        return float(results[0]["value"][1]) if results else 0.0

    values = {
        "request_rate": query("sum(rate(adaptive_db_pool_requests_total[30s]))"),
        "error_rate": query(
            "sum(rate(adaptive_db_pool_request_errors_total[30s])) / "
            "clamp_min(sum(rate(adaptive_db_pool_requests_total[30s])), 1e-9)"
        ),
        "admission_active": query("adaptive_db_pool_admission_active"),
        "admission_waiting": query("adaptive_db_pool_admission_waiting"),
        "admission_limit": query("adaptive_db_pool_admission_limit"),
        "pool_acquired": query("adaptive_db_pool_db_pool_acquired_connections"),
        "pool_idle": query("adaptive_db_pool_db_pool_idle_connections"),
        "pool_max": query("adaptive_db_pool_db_pool_max_connections"),
    }
    for class_name, route in (
        ("simple", "/api/workload/simple/:id"),
        ("medium", "/api/workload/medium/:id"),
        ("complex", "/api/workload/complex/:id"),
        ("aggregation", "/api/workload/aggregation"),
    ):
        values[f"rate_{class_name}"] = query(
            f'sum(rate(adaptive_db_pool_requests_total{{route="{route}"}}[30s]))'
        )

    if values["admission_limit"] in (None, 0):
        return None

    total = sum(values[f"rate_{c}"] for c in ("simple", "medium", "complex", "aggregation"))
    for class_name in ("simple", "medium", "complex", "aggregation"):
        rate = values[f"rate_{class_name}"]
        values[f"{class_name}_ratio"] = rate / total if total else 0.0
    values["pool_utilization"] = (
        values["pool_acquired"] / values["pool_max"] if values["pool_max"] else 0.0
    )
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--backend-url", default="http://localhost:8080")
    parser.add_argument("--apply", action="store_true", help="POST the recommendation to the backend")
    parser.add_argument("--min-limit", type=int, default=4)
    parser.add_argument("--max-limit", type=int, default=64)
    args = parser.parse_args()

    if not MODEL_PATH.exists():
        print(f"model artifact missing: {MODEL_PATH}", file=sys.stderr)
        print("train a model and copy it to predictor.joblib first", file=sys.stderr)
        return 1

    telemetry = fetch_telemetry(args.prometheus_url)
    if telemetry is None:
        print("telemetry incomplete; cannot predict", file=sys.stderr)
        return 1

    artifact = joblib.load(MODEL_PATH)
    exogenous = {key: telemetry[key] for key in EXOGENOUS_FEATURES}
    weights = {
        "w_latency": float(os.getenv("W_LATENCY", "1.0")),
        "w_error": float(os.getenv("W_ERROR", "3.0")),
        "w_wait": float(os.getenv("W_WAIT", "1.5")),
        "w_resource": float(os.getenv("W_RESOURCE", "0.05")),
    }

    result = recommend_limit(
        artifact,
        exogenous,
        range(args.min_limit, args.max_limit + 1),
        weights,
    )

    print(f"current limit : {int(telemetry['admission_limit'])}")
    print(f"recommended   : {result['best_limit']} (predicted J={result['best_cost']:.6f})")
    top = sorted(result["curve"].items(), key=lambda kv: kv[1])[:3]
    print("top candidates:", ", ".join(f"{c}: J={j:.4f}" for c, j in top))

    if args.apply:
        response = requests.post(
            f"{args.backend_url.rstrip('/')}/api/admin/admission/limit",
            json={"limit": result["best_limit"], "reason": "offline_predict"},
            timeout=5,
        )
        print(f"applied       : {response.json()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
