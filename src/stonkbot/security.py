"""Bankr-style rails: dry-run, rate limit, circuit breaker, daily budget. X-only — no Telegram."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from .config import get_settings


@dataclass
class CircuitBreaker:
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
class RateLimiter:
    max_per_minute: int = 5
    hits: deque = field(default_factory=deque)

    def allow(self) -> bool:
        now = time.time()
        while self.hits and now - self.hits[0] > 60:
            self.hits.popleft()
        if len(self.hits) >= self.max_per_minute:
            return False
        self.hits.append(now)
        return True


@dataclass
class DailyBudget:
    limit: int = 10
    used: int = 0
    day: str = ""

    def _roll(self) -> None:
        today = time.strftime("%Y-%m-%d")
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
    """Single entry point for all safety checks."""

    def __init__(self) -> None:
        s = get_settings()
        self.circuit = CircuitBreaker()
        self.rate = RateLimiter(max_per_minute=s.rate_limit_per_min)
        self.budget = DailyBudget(limit=s.daily_launch_budget)

    def can_launch(self) -> tuple[bool, str]:
        s = get_settings()
        if s.dry_run:
            return True, "dry_run"
        if self.circuit.is_open():
            return False, "circuit_open"
        if not self.rate.allow():
            return False, "rate_limited"
        if self.budget.remaining() <= 0:
            return False, "daily_budget_exhausted"
        return True, "ok"

    def on_success(self) -> None:
        self.circuit.record_success()
        self.budget.consume()

    def on_failure(self) -> None:
        self.circuit.record_failure()


guard = Guard()
