"""Controller-side Prometheus instrumentation.

Exposes model identity, prediction latency/throughput, and the latest
recommendation so MLOps monitoring can watch the online loop itself.
"""

from __future__ import annotations

import os

from prometheus_client import Counter, Gauge, Histogram, start_http_server

MODEL_INFO = Gauge(
    "adaptive_ml_model_info",
    "Currently loaded predictor identity.",
    ["kind", "model", "dataset_version", "git_commit"],
)

PREDICTIONS_TOTAL = Counter(
    "adaptive_ml_predictions_total",
    "Outcome predictions evaluated across candidate grids.",
)

PREDICTION_LATENCY = Histogram(
    "adaptive_ml_prediction_latency_seconds",
    "Latency of one full candidate-grid prediction cycle.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

RECOMMENDED_LIMIT = Gauge(
    "adaptive_ml_recommended_limit",
    "Argmin limit from the last optimization cycle.",
)

HELD_LIMIT = Gauge(
    "adaptive_ml_held_limit",
    "Admission limit currently held by the backend.",
)


def start_metrics_server() -> None:
    port = int(os.getenv("METRICS_PORT", "9877"))
    start_http_server(port)
