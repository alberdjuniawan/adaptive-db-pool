"""Safety layer: the last line of defense before a limit is applied.

Enforces, in order:
  1. minimum bound          L >= L_min
  2. maximum bound          L <= L_max
  3. rate limiting          |L_new - L_old| <= delta_max
  4. cooldown               no change within `cooldown_seconds`
  5. hysteresis             ignore insignificant improvements
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SafetyDecision:
    applied_limit: int
    changed: bool
    reason: str


@dataclass
class SafetyLayer:
    min_limit: int
    max_limit: int
    max_delta: int
    cooldown_seconds: float
    hysteresis_threshold: float

    _last_change_ts: float = field(default=0.0, init=False)

    def evaluate(self, current_limit: int, candidate_limit: int, improvement_hint: float | None = None) -> SafetyDecision:
        now = time.monotonic()

        # Bounds.
        bounded = max(self.min_limit, min(self.max_limit, candidate_limit))

        # Hysteresis: keep the current limit when the predicted gain is
        # insignificant relative to it.
        if improvement_hint is not None and improvement_hint < self.hysteresis_threshold:
            return SafetyDecision(applied_limit=current_limit, changed=False, reason="hysteresis")

        if bounded == current_limit:
            return SafetyDecision(applied_limit=current_limit, changed=False, reason="no_change")

        # Rate limiting.
        clamped = max(current_limit - self.max_delta, min(current_limit + self.max_delta, bounded))

        # Cooldown.
        if self._last_change_ts > 0 and (now - self._last_change_ts) < self.cooldown_seconds:
            return SafetyDecision(applied_limit=current_limit, changed=False, reason="cooldown")

        self._last_change_ts = now

        reason = "increase" if clamped > current_limit else "decrease"
        return SafetyDecision(applied_limit=clamped, changed=True, reason=reason)
