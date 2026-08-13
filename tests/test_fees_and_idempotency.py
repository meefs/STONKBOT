"""Service-fee splits, referral rebates, and the double-payment guard."""

from __future__ import annotations

import pytest

from stonkbot import fees, idempotency, vault

# --- fee splits -------------------------------------------------------------


def test_split_without_referrer_pays_operator_everything():
    split = fees.split_amounts(None, "alice")
    assert split["total"] == pytest.approx(0.1)
    assert split["platform"] == pytest.approx(0.1)
    assert split["referrer"] == 0.0


def test_split_with_referrer_is_70_30():
    split = fees.split_amounts("bob", "alice")
    assert split["referrer"] == pytest.approx(0.03)
    assert split["platform"] == pytest.approx(0.07)
    # The split must never invent or lose SOL.
    assert split["platform"] + split["referrer"] == pytest.approx(split["total"])


def test_self_referral_is_blocked():
    """Otherwise a user rebates themselves 30% of their own fee every launch."""
    split = fees.split_amounts("alice", "alice")
    assert split["referrer"] == 0.0
    assert split["platform"] == pytest.approx(0.1)


def test_self_referral_blocked_regardless_of_case_or_at_sign():
    assert fees.split_amounts("@Alice", "alice")["referrer"] == 0.0


def test_referrer_without_a_wallet_is_not_owed():
    """We cannot pay a referrer with no address; the operator keeps the fee.

    The important part is that no SOL is recorded as owed to nowhere.
    """
    owed = fees.record_expected("alice", "MINT1", ref_handle="ghost", ref_recipient=None)
    assert owed["referrer_id"] is None
    assert owed["referrer_sol"] == 0.0
    assert owed["platform_sol"] == pytest.approx(0.1)


def test_referral_is_recorded_and_reported():
    referrer = vault.register("bob")
    owed = fees.record_expected(
        "alice", "MINT1", ref_handle="bob", ref_recipient=referrer.pubkey
    )
    assert owed["referrer_sol"] == pytest.approx(0.03)
    assert owed["referrer_recipient"] == referrer.pubkey

    earnings = fees.referral_earnings("bob")
    assert earnings["pending_sol"] == pytest.approx(0.03)
    assert earnings["paid_sol"] == 0.0

    fees.mark_paid(owed["referrer_id"], "sig123")
    settled = fees.referral_earnings("bob")
    assert settled["paid_sol"] == pytest.approx(0.03)
    assert settled["pending_sol"] == 0.0


def test_unpaid_fees_are_visible_to_the_operator():
    """A failed transfer must remain a visible debt, not disappear."""
    owed = fees.record_expected("alice", "MINT1")
    fees.mark_failed(owed["platform_id"])
    rows = fees.outstanding()
    assert any(r["id"] == owed["platform_id"] and r["status"] == "failed" for r in rows)


# --- idempotency ------------------------------------------------------------


def test_first_claim_succeeds():
    assert idempotency.claim("tweet:1") is None


def test_second_concurrent_claim_is_refused():
    """Two workers must not both launch the same command."""
    idempotency.claim("tweet:1")
    with pytest.raises(idempotency.LaunchAlreadyRunning):
        idempotency.claim("tweet:1")


def test_completed_launch_replays_instead_of_re_paying():
    """The core double-spend guard.

    StonkFun: 'a second payment creates a second token.' A replayed mention
    must return the original result, not launch again.
    """
    idempotency.claim("tweet:1")
    idempotency.resolve("tweet:1", {"status": "completed", "mint": "MINT1"})

    replayed = idempotency.claim("tweet:1")
    assert replayed is not None
    assert replayed["mint"] == "MINT1"
    assert replayed["status"] == "completed"


def test_release_allows_a_genuine_retry():
    """A failure that charged nothing should be retryable."""
    idempotency.claim("tweet:1")
    idempotency.release("tweet:1")
    assert idempotency.claim("tweet:1") is None


def test_release_cannot_erase_a_completed_launch():
    """Releasing a done key would reopen the door to a second payment."""
    idempotency.claim("tweet:1")
    idempotency.resolve("tweet:1", {"status": "completed", "mint": "MINT1"})
    idempotency.release("tweet:1")

    assert idempotency.state_of("tweet:1") == "done"
    assert idempotency.claim("tweet:1") is not None


def test_distinct_commands_do_not_collide():
    assert idempotency.claim("tweet:1") is None
    assert idempotency.claim("tweet:2") is None
