"""Launch orchestration: dry-run safety, error semantics, and fee handling.

StonkFun is stubbed here — these tests must never touch the network or spend
anything.
"""

from __future__ import annotations

import pytest

from stonkbot import launch as launch_module
from stonkbot import vault
from stonkbot.config import get_settings
from stonkbot.models import LaunchRequest, QuotePair
from stonkbot.stonkfun_client import StonkFunError

QUOTE = QuotePair(mint="Xsf9mBktVB9BSU5kf4nHxPq5hCBJ2j2ui3ecFGxPRGc", symbol="GMEX")


class FakeClient:
    """Stub StonkFun client. Records whether money-moving calls happened."""

    def __init__(self, *, prepare=None, submit=None, get=None):
        self._prepare = prepare
        self._submit = submit
        self._get = get
        self.prepare_calls = 0
        self.submit_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def find_pair(self, query, launchable=True):
        if query in (QUOTE.mint, QUOTE.symbol):
            return QUOTE
        return None

    def prepare_launch(self, req):
        self.prepare_calls += 1
        if isinstance(self._prepare, Exception):
            raise self._prepare
        return self._prepare or {}

    def submit_launch(self, signed_quote, signed_tx, logo=None):
        self.submit_calls += 1
        if isinstance(self._submit, Exception):
            raise self._submit
        return self._submit or {}

    def get_launch(self, signature):
        if isinstance(self._get, Exception):
            raise self._get
        return self._get or {}


@pytest.fixture
def request_obj():
    return LaunchRequest(
        name="GameStop",
        symbol="GMESTONK",
        quote_mint=QUOTE.mint,
        creator_wallet="placeholder",
    )


def _install(monkeypatch, client):
    monkeypatch.setattr(launch_module, "StonkFunClient", lambda *a, **k: client)
    return client


def test_dry_run_never_prepares_or_submits(monkeypatch, request_obj):
    """The headline safety property: DRY_RUN moves nothing and calls nothing."""
    vault.register("alice")
    client = _install(monkeypatch, FakeClient())

    result = launch_module.run_launch(request_obj, "alice", idempotency_key="t:1")

    assert result.status == "dry_run"
    assert client.prepare_calls == 0
    assert client.submit_calls == 0
    assert result.mint is None


def test_unregistered_user_cannot_launch(monkeypatch, request_obj):
    _install(monkeypatch, FakeClient())
    result = launch_module.run_launch(request_obj, "nobody", idempotency_key="t:1")
    assert result.status == "failed"
    assert "register" in result.message.lower()


def test_unknown_quote_is_rejected_before_spending(monkeypatch, request_obj):
    vault.register("alice")
    monkeypatch.setenv("STONKBOT_DRY_RUN", "false")
    get_settings.cache_clear()
    client = _install(monkeypatch, FakeClient())

    bad = request_obj.model_copy(update={"quote_mint": "NOTAPAIR"})
    result = launch_module.run_launch(bad, "alice", idempotency_key="t:1")

    assert result.status == "failed"
    assert client.prepare_calls == 0
    assert client.submit_calls == 0


def test_conflict_never_retries_and_warns_about_recovery(monkeypatch, request_obj):
    """409 means the payment landed. Re-paying would mint a second token."""
    vault.register("alice")
    monkeypatch.setenv("STONKBOT_DRY_RUN", "false")
    get_settings.cache_clear()

    client = _install(
        monkeypatch,
        FakeClient(
            prepare={"paymentTransaction": "dGVzdA==", "signedQuote": "q"},
            submit=StonkFunError("conflict", "needs manual recovery"),
        ),
    )
    # Bypass tx verification and signing; this test is about error semantics.
    monkeypatch.setattr(
        launch_module, "inspect_payment_transaction", lambda *a, **k: _FakeInspection()
    )
    monkeypatch.setattr(launch_module, "sign_transaction_b64", lambda *a, **k: "signed")
    monkeypatch.setattr(launch_module, "get_balance_sol", lambda *a, **k: 5.0)

    result = launch_module.run_launch(request_obj, "alice", idempotency_key="t:1")

    assert result.status == "failed"
    assert client.submit_calls == 1, "must not resubmit after a conflict"
    assert result.raw.get("needs_recovery") is True
    assert "do not retry" in result.message.lower()


def test_unsafe_transaction_is_never_signed(monkeypatch, request_obj):
    """If the guard rejects the payment tx, submit must not happen."""
    from stonkbot.txguard import UnsafeTransaction

    vault.register("alice")
    monkeypatch.setenv("STONKBOT_DRY_RUN", "false")
    get_settings.cache_clear()

    client = _install(
        monkeypatch,
        FakeClient(prepare={"paymentTransaction": "dGVzdA==", "signedQuote": "q"}),
    )

    def refuse(*a, **k):
        raise UnsafeTransaction("fee payer is someone else")

    monkeypatch.setattr(launch_module, "inspect_payment_transaction", refuse)

    signed = []
    monkeypatch.setattr(
        launch_module, "sign_transaction_b64", lambda *a, **k: signed.append(1)
    )

    result = launch_module.run_launch(request_obj, "alice", idempotency_key="t:1")

    assert result.status == "failed"
    assert not signed, "a rejected transaction must never be signed"
    assert client.submit_calls == 0


def test_insufficient_balance_blocks_before_signing(monkeypatch, request_obj):
    vault.register("alice")
    monkeypatch.setenv("STONKBOT_DRY_RUN", "false")
    get_settings.cache_clear()

    client = _install(
        monkeypatch,
        FakeClient(prepare={"paymentTransaction": "dGVzdA==", "signedQuote": "q"}),
    )
    monkeypatch.setattr(
        launch_module, "inspect_payment_transaction", lambda *a, **k: _FakeInspection()
    )
    monkeypatch.setattr(launch_module, "get_balance_sol", lambda *a, **k: 0.01)

    result = launch_module.run_launch(request_obj, "alice", idempotency_key="t:1")

    assert result.status == "failed"
    assert client.submit_calls == 0
    # The message quotes the real requirement, not a hardcoded number.
    assert "need" in result.message.lower()


def test_incomplete_quote_is_rejected(monkeypatch, request_obj):
    vault.register("alice")
    monkeypatch.setenv("STONKBOT_DRY_RUN", "false")
    get_settings.cache_clear()

    client = _install(monkeypatch, FakeClient(prepare={"signedQuote": "q"}))
    result = launch_module.run_launch(request_obj, "alice", idempotency_key="t:1")

    assert result.status == "failed"
    assert client.submit_calls == 0


def test_replayed_command_does_not_launch_twice(monkeypatch, request_obj):
    """The same tweet id must produce at most one paid launch."""
    vault.register("alice")
    monkeypatch.setenv("STONKBOT_DRY_RUN", "false")
    get_settings.cache_clear()

    client = _install(
        monkeypatch,
        FakeClient(
            prepare={"paymentTransaction": "dGVzdA==", "signedQuote": "q"},
            submit={"status": "completed", "mint": "MINT1", "paymentSignature": "sig1"},
        ),
    )
    monkeypatch.setattr(
        launch_module, "inspect_payment_transaction", lambda *a, **k: _FakeInspection()
    )
    monkeypatch.setattr(launch_module, "sign_transaction_b64", lambda *a, **k: "signed")
    monkeypatch.setattr(launch_module, "get_balance_sol", lambda *a, **k: 5.0)
    monkeypatch.setattr(launch_module, "_pay_service_fee", lambda *a, **k: (True, "fee", {}))

    first = launch_module.run_launch(request_obj, "alice", idempotency_key="tweet:99")
    second = launch_module.run_launch(request_obj, "alice", idempotency_key="tweet:99")

    assert first.status == "completed"
    assert second.mint == "MINT1"
    assert client.submit_calls == 1, "the replay must not submit a second payment"


class _FakeInspection:
    cost_sol = 0.2
    cost_is_simulated = True
    declared_debit_sol = 0.2
    simulated_debit_sol = 0.2
    signers_required = 1
    fee_payer = "x"
