"""Regression tests for the double-payment guard.

StonkFun: "never pay twice — a second payment creates a second token."

The subtle failure is not the happy path, it is the *failure* path: if a launch
fails after the payment may have landed and we release the idempotency key, a
retry of the same command can pay again. These tests pin down exactly which
outcomes free the key and which do not.
"""

from __future__ import annotations

import pytest

from stonkbot import idempotency, vault
from stonkbot import launch as launch_module
from stonkbot.config import get_settings
from stonkbot.models import LaunchRequest, QuotePair
from stonkbot.stonkfun_client import StonkFunError

QUOTE = QuotePair(mint="Xsf9mBktVB9BSU5kf4nHxPq5hCBJ2j2ui3ecFGxPRGc", symbol="GMEX")
KEY = "tweet:12345"


class FakeClient:
    def __init__(self, submit=None):
        self._submit = submit
        self.submit_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def find_pair(self, query, launchable=True):
        return QUOTE if query in (QUOTE.mint, QUOTE.symbol) else None

    def prepare_launch(self, req):
        return {"paymentTransaction": "dGVzdA==", "signedQuote": "q"}

    def submit_launch(self, signed_quote, signed_tx, logo=None):
        self.submit_calls += 1
        if isinstance(self._submit, Exception):
            raise self._submit
        return self._submit or {}

    def get_launch(self, signature):
        return {}


class _Inspection:
    cost_sol = 0.2
    cost_is_simulated = True
    declared_debit_sol = 0.2
    simulated_debit_sol = 0.2
    signers_required = 1
    fee_payer = "x"


@pytest.fixture
def live(monkeypatch):
    """A registered user with live spending enabled and signing stubbed out."""
    vault.register("alice")
    monkeypatch.setenv("STONKBOT_DRY_RUN", "false")
    get_settings.cache_clear()
    from stonkbot.security import guard

    guard.reset()
    monkeypatch.setattr(
        launch_module, "inspect_payment_transaction", lambda *a, **k: _Inspection()
    )
    monkeypatch.setattr(launch_module, "sign_transaction_b64", lambda *a, **k: "signed")
    monkeypatch.setattr(launch_module, "get_balance_sol", lambda *a, **k: 5.0)
    monkeypatch.setattr(
        launch_module, "_pay_service_fee", lambda *a, **k: (True, "feesig", {})
    )


def _request():
    return LaunchRequest(
        name="GameStop", symbol="GME", quote_mint=QUOTE.mint, creator_wallet="x"
    )


def test_conflict_keeps_the_key_locked(monkeypatch, live):
    """A 409 means the payment landed. Retrying would mint a second token.

    This is the bug this test exists for: releasing the key here would let the
    very next delivery of the same tweet pay again.
    """
    client = FakeClient(submit=StonkFunError("conflict", "needs manual recovery"))
    monkeypatch.setattr(launch_module, "StonkFunClient", lambda *a, **k: client)

    result = launch_module.run_launch(_request(), "alice", idempotency_key=KEY)
    assert result.status == "failed"
    assert client.submit_calls == 1

    # The key must still be held, so a replay cannot reach submit again.
    assert idempotency.state_of(KEY) == "running"
    second = launch_module.run_launch(_request(), "alice", idempotency_key=KEY)
    assert second.status == "failed"
    assert client.submit_calls == 1, "a conflict must never lead to a second payment"


def test_charged_false_frees_the_key_for_a_real_retry(monkeypatch, live):
    """StonkFun explicitly said nothing was charged, so retrying is correct."""
    client = FakeClient(
        submit=StonkFunError("service_unavailable", "busy", charged=False)
    )
    monkeypatch.setattr(launch_module, "StonkFunClient", lambda *a, **k: client)

    result = launch_module.run_launch(_request(), "alice", idempotency_key=KEY)
    assert result.status == "failed"
    assert result.raw.get("charged") is False
    assert idempotency.state_of(KEY) is None, "an uncharged failure should be retryable"


def test_ambiguous_failure_keeps_the_key_locked(monkeypatch, live):
    """If we cannot prove nothing was charged, we must assume it might have been."""
    client = FakeClient(submit=StonkFunError("internal", "boom"))
    monkeypatch.setattr(launch_module, "StonkFunClient", lambda *a, **k: client)

    launch_module.run_launch(_request(), "alice", idempotency_key=KEY)
    assert idempotency.state_of(KEY) == "running"


def test_unexpected_exception_keeps_the_key_locked(monkeypatch, live):
    """A crash mid-submit is ambiguous too, so the key stays held."""

    class Boom(FakeClient):
        def submit_launch(self, *a, **k):
            self.submit_calls += 1
            raise RuntimeError("connection reset")

    client = Boom()
    monkeypatch.setattr(launch_module, "StonkFunClient", lambda *a, **k: client)

    result = launch_module.run_launch(_request(), "alice", idempotency_key=KEY)
    assert result.status == "failed"
    assert idempotency.state_of(KEY) == "running"


def test_pre_submit_failures_are_retryable(monkeypatch, live):
    """Nothing is charged before submit, so those failures must not lock a user out."""
    client = FakeClient()
    monkeypatch.setattr(launch_module, "StonkFunClient", lambda *a, **k: client)
    monkeypatch.setattr(launch_module, "get_balance_sol", lambda *a, **k: 0.0)

    result = launch_module.run_launch(_request(), "alice", idempotency_key=KEY)
    assert result.status == "failed"
    assert client.submit_calls == 0
    assert idempotency.state_of(KEY) is None


def test_rate_limited_launch_is_retryable(monkeypatch, live):
    """Being rate limited attempts nothing, so the key must be freed."""
    from stonkbot.security import SlidingWindow, guard

    client = FakeClient()
    monkeypatch.setattr(launch_module, "StonkFunClient", lambda *a, **k: client)
    guard.rate = SlidingWindow(max_events=0, window_seconds=60.0)

    result = launch_module.run_launch(_request(), "alice", idempotency_key=KEY)
    assert result.status == "failed"
    assert idempotency.state_of(KEY) is None
    guard.reset()


def test_replay_does_not_consume_rate_limit(monkeypatch, live):
    """A replayed launch does no work, so it must not burn the user's allowance."""
    from stonkbot.security import guard

    client = FakeClient(
        submit={"status": "completed", "mint": "MINT1", "paymentSignature": "sig1"}
    )
    monkeypatch.setattr(launch_module, "StonkFunClient", lambda *a, **k: client)

    launch_module.run_launch(_request(), "alice", idempotency_key=KEY)
    used_after_first = len(guard.per_user["alice"].hits)

    launch_module.run_launch(_request(), "alice", idempotency_key=KEY)
    assert len(guard.per_user["alice"].hits) == used_after_first
    assert client.submit_calls == 1


def test_dry_run_does_not_lock_the_key(monkeypatch):
    """Dry-run spends nothing, so it must not block a later real launch."""
    vault.register("alice")
    client = FakeClient()
    monkeypatch.setattr(launch_module, "StonkFunClient", lambda *a, **k: client)

    result = launch_module.run_launch(_request(), "alice", idempotency_key=KEY)
    assert result.status == "dry_run"
    assert idempotency.state_of(KEY) is None
