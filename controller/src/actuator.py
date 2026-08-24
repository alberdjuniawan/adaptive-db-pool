"""Actuator: applies decisions to the backend control plane."""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


class BackendActuatorError(Exception):
    pass


class BackendActuator:
    def __init__(self, backend_url: str, timeout: float = 3.0) -> None:
        self._url = backend_url.rstrip("/")
        self._timeout = timeout

    def apply_limit(self, limit: int, reason: str) -> int:
        try:
            response = requests.post(
                f"{self._url}/api/admin/admission/limit",
                json={"limit": limit, "reason": reason},
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            applied = int(payload.get("applied_limit", limit))
            return applied
        except (requests.RequestException, ValueError) as exc:
            raise BackendActuatorError(f"failed to apply limit {limit}: {exc}") from exc

    def health(self) -> bool:
        try:
            response = requests.get(f"{self._url}/health", timeout=self._timeout)
            return response.ok
        except requests.RequestException:
            return False
