"""Controller configuration: YAML defaults with environment overrides.

Resolution order (highest precedence first):
    1. environment variables
    2. YAML config file (explicit path or CONTROLLER_CONFIG)
    3. built-in defaults

Experimental parameters are never hard-coded in decision logic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _flatten(mapping: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested mappings: ``objective.weight_latency`` becomes
    ``objective_weight_latency``."""
    flat: dict[str, Any] = {}
    for key, value in mapping.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, prefix=f"{path}_"))
        else:
            flat[path] = value
    return flat


@dataclass(frozen=True)
class ControllerConfig:
    backend_url: str
    prometheus_url: str

    interval_seconds: float
    min_limit: int
    max_limit: int
    max_delta: int
    cooldown_seconds: float
    hysteresis_threshold: float
    fallback_limit: int
    fresh_timeout_seconds: float
    heartbeat_seconds: float

    model_path: str

    # Objective weights. Documented and justified: tail latency dominates
    # because RQ2 targets p99 improvements; errors and admission waits
    # are strong penalties; resource cost is mild pressure against
    # over-provisioning.
    weight_latency: float
    weight_error: float
    weight_wait: float
    weight_resource: float

    @staticmethod
    def load_yaml(config_file: str) -> dict[str, Any]:
        file = Path(config_file).expanduser()
        if not file.is_file():
            raise FileNotFoundError(f"controller config not found: {config_file}")
        with file.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle) or {}
        if not isinstance(document, dict):
            raise ValueError(f"controller config must be a mapping: {config_file}")
        return _flatten(document)

    @classmethod
    def from_env(cls, config_file: str | None = None) -> "ControllerConfig":
        source = config_file or os.getenv("CONTROLLER_CONFIG")
        values = cls.load_yaml(source) if source else {}

        def resolve(env_key: str, yaml_key: str, default: str) -> str:
            raw = os.getenv(env_key)
            if raw:
                return raw
            if yaml_key in values:
                return str(values[yaml_key])
            return default

        return cls(
            backend_url=resolve("BACKEND_URL", "backend_url", "http://localhost:8080"),
            prometheus_url=resolve("PROMETHEUS_URL", "prometheus_url", "http://localhost:9090"),
            interval_seconds=float(resolve("CONTROLLER_INTERVAL", "interval_seconds", "5")),
            min_limit=int(resolve("ADMISSION_MIN_LIMIT", "min_limit", "4")),
            max_limit=int(resolve("ADMISSION_MAX_LIMIT", "max_limit", "64")),
            max_delta=int(resolve("ADMISSION_MAX_DELTA", "max_delta", "4")),
            cooldown_seconds=float(resolve("ADMISSION_COOLDOWN", "cooldown_seconds", "5")),
            hysteresis_threshold=float(
                resolve("HYSTERESIS_THRESHOLD", "hysteresis_threshold", "0.05")
            ),
            fallback_limit=int(resolve("ADMISSION_FALLBACK_LIMIT", "fallback_limit", "20")),
            fresh_timeout_seconds=float(resolve("FRESH_TIMEOUT", "fresh_timeout_seconds", "60")),
            heartbeat_seconds=float(resolve("HEARTBEAT_SECONDS", "heartbeat_seconds", "15")),
            model_path=resolve("MODEL_PATH", "model_path", "/models/predictor.joblib"),
            weight_latency=float(resolve("W_LATENCY", "objective_weight_latency", "1.0")),
            weight_error=float(resolve("W_ERROR", "objective_weight_error", "3.0")),
            weight_wait=float(resolve("W_WAIT", "objective_weight_wait", "1.5")),
            weight_resource=float(resolve("W_RESOURCE", "objective_weight_resource", "0.05")),
        )
