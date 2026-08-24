"""Closed-loop controller entrypoint (Phase 9).

Loop:
    telemetry -> predictor -> optimizer -> safety layer -> actuator

Fail-safe behavior: any exception in a cycle skips that cycle; if the
backend stops accepting decisions for `fresh_timeout_seconds`, the
safety layer's cooldown plus the backend-side EnsureFresh fallback keep
the system within safe bounds.
"""

from __future__ import annotations

import logging
import sys
import time

from .actuator import BackendActuator, BackendActuatorError
from .config import ControllerConfig
from .optimizer import GridOptimizer
from .predictor import load_predictor
from .safety import SafetyLayer
from .telemetry import PrometheusTelemetrySource

logging.basicConfig(
    level=logging.INFO,
    format='{"ts": "%(asctime)s", "level": "%(levelname)s", "msg": %(message)s}',
    stream=sys.stdout,
)
logger = logging.getLogger("controller")


def run() -> int:
    config = ControllerConfig.from_env()

    predictor, predictor_kind = load_predictor(
        config.model_path,
        (
            config.weight_latency,
            config.weight_error,
            config.weight_wait,
            config.weight_resource,
        ),
    )
    logger.info('{"event": "startup", "predictor": "%s"}', predictor_kind)

    telemetry_source = PrometheusTelemetrySource(config.prometheus_url)
    optimizer = GridOptimizer()
    actuator = BackendActuator(config.backend_url)
    # One persistent instance: cooldown state must survive across cycles.
    safety = SafetyLayer(
        min_limit=config.min_limit,
        max_limit=config.max_limit,
        max_delta=config.max_delta,
        cooldown_seconds=config.cooldown_seconds,
        hysteresis_threshold=config.hysteresis_threshold,
    )

    current_limit = config.fallback_limit
    last_applied_ts = 0.0
    last_post_ts = 0.0
    stale_after = config.fresh_timeout_seconds

    while True:
        cycle_start = time.monotonic()

        try:
            telemetry = telemetry_source.fetch()
            if telemetry is None:
                raise RuntimeError("telemetry unavailable")

            # The backend is authoritative about the live limit.
            current_limit = int(telemetry.admission_limit)

            result = optimizer.optimize(
                predictor=predictor,
                telemetry=telemetry,
                current_limit=current_limit,
                min_limit=config.min_limit,
                max_limit=config.max_limit,
            )

            improvement_hint = None
            baseline_cost = result.evaluated.get(current_limit)
            if baseline_cost and result.best_cost:
                improvement_hint = (baseline_cost - result.best_cost) / abs(baseline_cost)

            decision = safety.evaluate(current_limit, result.best_limit, improvement_hint)

            if decision.changed:
                applied = actuator.apply_limit(decision.applied_limit, f"adaptive_{decision.reason}")
                logger.info(
                    '{"event": "limit_change", "reason": "%s", "old": %d, "new": %d, "cost": %.6f, "predictor": "%s"}',
                    decision.reason,
                    current_limit,
                    decision.applied_limit,
                    result.best_cost,
                    predictor_kind,
                )
                current_limit = applied
                last_applied_ts = time.monotonic()
                last_post_ts = time.monotonic()
            else:
                # Heartbeat: prove liveness or the backend fail-safe
                # will treat silence as controller death.
                now_mono = time.monotonic()
                if now_mono - last_post_ts >= config.heartbeat_seconds:
                    actuator.apply_limit(current_limit, "controller_heartbeat")
                    last_post_ts = now_mono
                logger.info(
                    '{"event": "no_change", "held": %d, "best_candidate": %d, "predictor": "%s"}',
                    current_limit,
                    result.best_limit,
                    predictor_kind,
                )

        except BackendActuatorError as exc:
            logger.error('{"event": "actuation_failed", "error": "%s"}', exc)
            if time.monotonic() - last_applied_ts > stale_after:
                logger.warning('{"event": "stale_controller_detected"}')
        except Exception as exc:  # noqa: BLE001 - the loop must never die
            logger.error('{"event": "cycle_failed", "error": "%s"}', exc)

        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0.0, config.interval_seconds - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    sys.exit(run())
