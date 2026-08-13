"""Safety rails: dry-run, rate limits, circuit breaker, daily budget.

Two bugs in the previous version are fixed here:

  * ``can_launch()`` consumed a rate-limit token every time it was called, and
    it was called twice per launch (once in the X handler, once in
    ``run_launch``). Half the configured allowance was being burned on checks
    that never launched anything. Checking and consuming are now separate.

  * Rate limiting was global only, so one user could exhaust the whole
    platform's allowance. There is now a per-user limit as well.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from .config import get_settings


@dataclass
class CircuitBreaker:
    """Stops hammering a failing downstream after repeated errors."""

    failure_threshold: int = 3
    open_seconds: float = 300.0
    failures: int = 0
    opened_at: float | None = None

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.time()

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.time() - self.opened_at >= self.open_seconds:
            self.failures = 0
            self.opened_at = None
            return False
        return True


@dataclass
class SlidingWindow:
    """Sliding-window counter. ``allow`` is a pure check; ``consume`` records."""

    max_events: int
    window_seconds: float = 60.0
    hits: deque = field(default_factory=deque)

    def _trim(self) -> None:
        cutoff = time.time() - self.window_seconds
        while self.hits and self.hits[0] <= cutoff:
            self.hits.popleft()

    def allow(self) -> bool:
        self._trim()
        return len(self.hits) < self.max_events

    def consume(self) -> None:
        self._trim()
        self.hits.append(time.time())


@dataclass
class DailyBudget:
    """Hard ceiling on launches per UTC day."""

    limit: int = 10
    used: int = 0
    day: str = ""

    def _roll(self) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if self.day != today:
            self.day = today
            self.used = 0

    def remaining(self) -> int:
        self._roll()
        return max(0, self.limit - self.used)

    def consume(self) -> bool:
        self._roll()
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


class Guard:
    """Single entry point for all pre-launch safety checks."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """(Re)build every window from current settings.

        The limits are read here rather than captured once at import, so a
        settings change actually takes effect — and so tests start from a clean
        slate instead of inheriting another test's counters.
        """
        s = get_settings()
        self.circuit = CircuitBreaker()
        self.rate = SlidingWindow(max_events=s.rate_limit_per_min, window_seconds=60.0)
        self.budget = DailyBudget(limit=s.daily_launch_budget)
        self.per_user: dict[str, SlidingWindow] = defaultdict(
            lambda: SlidingWindow(
                max_events=get_settings().user_launches_per_hour,
                window_seconds=3600.0,
            )
        )

    def can_launch(self, x_handle: str | None = None) -> tuple[bool, str]:
        """Check whether a launch may proceed, and consume the allowance if so.

        In dry-run the rails still apply, so behaviour under test matches
        production instead of diverging from it.
        """
        if self.circuit.is_open():
            return False, "circuit_open"
        if not self.rate.allow():
            return False, "rate_limited"
        if self.budget.remaining() <= 0:
            return False, "daily_budget_exhausted"

        user_window = None
        if x_handle:
            user_window = self.per_user[x_handle.lstrip("@").lower()]
            if not user_window.allow():
                return False, "too many launches — try again later"

        self.rate.consume()
        if user_window is not None:
            user_window.consume()
        return True, "ok"

    def peek(self, x_handle: str | None = None) -> tuple[bool, str]:
        """Non-consuming variant, for status output."""
        if self.circuit.is_open():
            return False, "circuit_open"
        if not self.rate.allow():
            return False, "rate_limited"
        if self.budget.remaining() <= 0:
            return False, "daily_budget_exhausted"
        if x_handle and not self.per_user[x_handle.lstrip("@").lower()].allow():
            return False, "user_rate_limited"
        return True, "ok"

    def on_success(self) -> None:
        self.circuit.record_success()
        self.budget.consume()

    def on_failure(self) -> None:
        self.circuit.record_failure()


guard = Guard()
