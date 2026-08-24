#!/usr/bin/env python3
"""Offline prediction: recommend an admission limit from live Prometheus
telemetry using a trained model (the manual counterpart of the online
controller service).

Usage:
 python scripts/predict.py [--prometheus-url http://localhost:9090] [--backend-url http://localhost:8080] [--apply]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import joblib
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ml"))

from src.optimization.optimizer import recommend_limit  # noqa: E402

MODEL_PATH = REPO_ROOT / "ml" / "models" / "predictor.joblib"


def fetch_telemetry(prometheus_url: str) -> dict | None:
    queries = {
        "request_rate": 'sum(rate(adaptive_db_pool_requests_total[30s]))',
        "p95_latency": 'histogram_quantile(0.95, sum(rate(adaptive_db_pool_request_duration_seconds_bucket[30s])) by (le))',
        "p99_latency": 'histogram_quantile(0.99, sum(rate(adaptive_db_pool_request_duration_seconds_bucket[30s])) by (le))',
        "error_rate": 'sum(rate(adaptive_db_pool_request_errors_total[30s]))',
        "admission_active": "adaptive_db_pool_admission_active",
        "admission_waiting": "adaptive_db_pool_admission_waiting",
        "admission_limit": "adaptive_db_pool_admission_limit",
        "utilization": None,  # derived below
        "pool_acquired": "adaptive_db_pool_db_pool_acquired_connections",
        "pool_idle": "adaptive_db_pool_db_pool_idle_connections",
        "pool_utilization": None,  # derived below
    }

    values = {}
    for name, query in queries.items():
        if query is None:
            continue
        response = requests.get(
            f"{prometheus_url.rstrip('/')}/api/v1/query", params={"query": query}, timeout=5
        )
        payload = response.json()
        results = payload.get("data", {}).get("result", [])
        values[name] = float(results[0]["value"][1]) if results else 0.0

    if values.get("admission_active") is None or values.get("admission_limit") in (None, 0):
        return None

    values["utilization"] = values["admission_active"] / max(values["admission_limit"], 1)
    total_pool = values["pool_acquired"] + values["pool_idle"]
    values["pool_utilization"] = values["pool_acquired"] / total_pool if total_pool else 0.0
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

    model = joblib.load(MODEL_PATH)
    recommended, cost = recommend_limit(model, telemetry, args.min_limit, args.max_limit)

    print(f"current limit : {int(telemetry['admission_limit'])}")
    print(f"recommended   : {recommended} (predicted J={cost:.6f})")

    if args.apply:
        response = requests.post(
            f"{args.backend_url.rstrip('/')}/api/admin/admission/limit",
            json={"limit": recommended, "reason": "offline_predict"},
            timeout=5,
        )
        print(f"applied       : {response.json()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
