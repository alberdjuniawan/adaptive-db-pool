"""Prometheus telemetry client for the closed-loop controller."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Telemetry:
    """One observation window of aligned system state."""

    request_rate: float
    p95_latency: float
    p99_latency: float
    error_rate: float
    admission_active: float
    admission_waiting: float
    admission_limit: float
    pool_acquired: float
    pool_idle: float
    pool_max: float
    simple_rate: float = 0.0
    medium_rate: float = 0.0
    complex_rate: float = 0.0
    aggregation_rate: float = 0.0

    @property
    def utilization(self) -> float:
        if self.admission_limit <= 0:
            return 0.0
        return self.admission_active / self.admission_limit

    @property
    def pool_utilization(self) -> float:
        if self.pool_max <= 0:
            return 0.0
        return self.pool_acquired / self.pool_max


class PrometheusTelemetrySource:
    """Fetches the latest values of backend metrics from Prometheus."""

    def __init__(self, prometheus_url: str, timeout: float = 3.0) -> None:
        self._url = prometheus_url.rstrip("/")
        self._timeout = timeout

    def _query(self, query: str) -> Optional[float]:
        try:
            response = requests.get(
                f"{self._url}/api/v1/query",
                params={"query": query},
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("data", {}).get("result", [])
            if not results:
                return None
            value = results[0].get("value", [None, None])[1]
            if value is None:
                return None
            return float(value)
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            logger.debug("prometheus query failed for %s: %s", query, exc)
            return None

    def fetch(self) -> Optional[Telemetry]:
        rate_2xx = self._query('sum(rate(adaptive_db_pool_requests_total{status="2xx"}[30s]))') or 0.0
        rate_all = self._query("sum(rate(adaptive_db_pool_requests_total[30s]))") or 0.0
        error_rate = self._query(
            'sum(rate(adaptive_db_pool_request_errors_total[30s])) / clamp_min(sum(rate(adaptive_db_pool_requests_total[30s])), 1e-9)'
        ) or 0.0

        telemetry = Telemetry(
            request_rate=rate_all or rate_2xx,
            p95_latency=self._query(
                'histogram_quantile(0.95, sum(rate(adaptive_db_pool_request_duration_seconds_bucket[30s])) by (le))'
            )
            or 0.0,
            p99_latency=self._query(
                'histogram_quantile(0.99, sum(rate(adaptive_db_pool_request_duration_seconds_bucket[30s])) by (le))'
            )
            or 0.0,
            error_rate=error_rate,
            admission_active=self._query("adaptive_db_pool_admission_active") or 0.0,
            admission_waiting=self._query("adaptive_db_pool_admission_waiting") or 0.0,
            admission_limit=self._query("adaptive_db_pool_admission_limit"),
            pool_acquired=self._query("adaptive_db_pool_db_pool_acquired_connections") or 0.0,
            pool_idle=self._query("adaptive_db_pool_db_pool_idle_connections") or 0.0,
            pool_max=self._query("adaptive_db_pool_db_pool_max_connections"),
            simple_rate=self._query(
                'sum(rate(adaptive_db_pool_requests_total{route="/api/workload/simple/:id"}[30s]))'
            )
            or 0.0,
            medium_rate=self._query(
                'sum(rate(adaptive_db_pool_requests_total{route="/api/workload/medium/:id"}[30s]))'
            )
            or 0.0,
            complex_rate=self._query(
                'sum(rate(adaptive_db_pool_requests_total{route="/api/workload/complex/:id"}[30s]))'
            )
            or 0.0,
            aggregation_rate=self._query(
                'sum(rate(adaptive_db_pool_requests_total{route="/api/workload/aggregation"}[30s]))'
            )
            or 0.0,
        )

        if telemetry.admission_limit is None or telemetry.pool_max is None:
            logger.warning("telemetry incomplete; skipping decision cycle")
            return None
        return telemetry
