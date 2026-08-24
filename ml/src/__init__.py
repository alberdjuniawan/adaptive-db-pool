"""Adaptive DB pool ML pipeline package."""

__version__ = "0.1.0"

# Canonical feature order shared by training, evaluation, prediction,
# and the online controller. Changing this order invalidates every
# persisted model artifact.
FEATURES = [
    "request_rate",
    "p95_latency",
    "p99_latency",
    "error_rate",
    "admission_active",
    "admission_waiting",
    "admission_limit",
    "utilization",
    "pool_acquired",
    "pool_idle",
    "pool_utilization",
]

TARGET = "objective_j"
