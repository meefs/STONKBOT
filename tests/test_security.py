"""Safety rails: rate limits, circuit breaker, daily budget."""

from __future__ import annotations

from stonkbot.security import CircuitBreaker, DailyBudget, Guard, SlidingWindow


def test_checking_does_not_consume_allowance():
    """peek() must be free.

    The old can_launch() consumed a token on every call and was called twice
    per launch, silently halving the configured rate limit.
    """
    guard = Guard()
    for _ in range(50):
        assert guard.peek()[0] is True
    assert guard.can_launch("alice")[0] is True


def test_global_rate_limit_applies(monkeypatch):
    guard = Guard()
    guard.rate = SlidingWindow(max_events=2, window_seconds=60.0)

    assert guard.can_launch("alice")[0] is True
    assert guard.can_launch("bob")[0] is True
    allowed, reason = guard.can_launch("carol")
    assert allowed is False
    assert reason == "rate_limited"


def test_one_user_cannot_exhaust_everyone_elses_allowance():
    """Per-user limiting: previously a single user could burn the global budget."""
    guard = Guard()
    guard.rate = SlidingWindow(max_events=100, window_seconds=60.0)
    guard.per_user["alice"] = SlidingWindow(max_events=2, window_seconds=3600.0)

    assert guard.can_launch("alice")[0] is True
    assert guard.can_launch("alice")[0] is True
    assert guard.can_launch("alice")[0] is False
    # Another user is unaffected.
    assert guard.can_launch("bob")[0] is True


def test_per_user_limit_is_case_insensitive():
    """@Alice and @alice are the same person and share one budget."""
    guard = Guard()
    guard.rate = SlidingWindow(max_events=100, window_seconds=60.0)
    guard.per_user["alice"] = SlidingWindow(max_events=1, window_seconds=3600.0)

    assert guard.can_launch("Alice")[0] is True
    assert guard.can_launch("@ALICE")[0] is False


def test_circuit_opens_after_repeated_failures():
    guard = Guard()
    for _ in range(3):
        guard.on_failure()
    allowed, reason = guard.can_launch("alice")
    assert allowed is False
    assert reason == "circuit_open"


def test_success_resets_the_circuit():
    breaker = CircuitBreaker(failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.is_open() is False


def test_daily_budget_is_enforced():
    budget = DailyBudget(limit=2)
    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.consume() is False
    assert budget.remaining() == 0


def test_sliding_window_expires_old_events():
    window = SlidingWindow(max_events=1, window_seconds=60.0)
    window.consume()
    assert window.allow() is False
    # Age the recorded hit past the window.
    window.hits[0] -= 61
    assert window.allow() is True


def test_dry_run_still_applies_the_rails():
    """Rails must behave the same in dry-run, so tests match production.

    Previously can_launch() short-circuited to True under DRY_RUN, meaning the
    rails were never exercised until real money was already at stake.
    """
    guard = Guard()
    guard.rate = SlidingWindow(max_events=1, window_seconds=60.0)
    assert guard.can_launch("alice")[0] is True
    assert guard.can_launch("alice")[0] is False
