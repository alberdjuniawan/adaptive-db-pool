"""Adaptive DB pool ML pipeline package."""

__version__ = "0.2.0"
FEATURE_SCHEMA = "v2"

# Exogenous inputs: workload/system state that is NOT an effect of the
# admission limit. These are the only state features fed to the outcome
# model, keeping counterfactual candidate queries valid.
EXOGENOUS_FEATURES = [
    "request_rate",
    "simple_ratio",
    "medium_ratio",
    "complex_ratio",
    "aggregation_ratio",
    "pool_acquired",
    "pool_idle",
    "pool_utilization",
]

# Per-candidate system outcomes predicted by the model. The objective J
# is computed from these afterwards, never predicted directly.
OUTCOMES = [
    "p99_latency",
    "wait_ratio",
    "error_rate",
    "pool_utilization",
]

# Columns identifying which experiment/regime a row belongs to.
IDENTITY_COLUMNS = ["timestamp", "experiment_id", "scenario", "workload_rate"]

TARGET = "objective_j"

# Objective weights; keep in sync with controller env defaults
# (W_LATENCY / W_ERROR / W_WAIT / W_RESOURCE).
WEIGHT_DEFAULTS = {
    "w_latency": 1.0,
    "w_error": 3.0,
    "w_wait": 1.5,
    "w_resource": 0.05,
}
