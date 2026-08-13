"""Agent wallet vault — isolation, encryption at rest, and handle safety."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stonkbot import vault
from stonkbot.config import get_settings


def test_register_creates_a_wallet():
    account = vault.register("alice")
    assert account.x_handle == "alice"
    assert len(account.pubkey) >= 32


def test_register_is_idempotent():
    """Re-registering must return the same address.

    A new address would strand any SOL already sent to the first one.
    """
    first = vault.register("alice")
    second = vault.register("alice")
    assert first.pubkey == second.pubkey


def test_handles_are_case_insensitive():
    """X handles are case-insensitive, so @Alice and @alice are one user."""
    assert vault.register("Alice").pubkey == vault.register("@alice").pubkey


def test_users_are_isolated():
    """The core wallet-security property: one user cannot reach another's."""
    alice = vault.register("alice")
    bob = vault.register("bob")
    assert alice.pubkey != bob.pubkey

    alice_key = vault.load_keypair("alice")
    bob_key = vault.load_keypair("bob")
    assert str(alice_key.pubkey()) == alice.pubkey
    assert str(bob_key.pubkey()) == bob.pubkey
    assert str(alice_key.pubkey()) != str(bob_key.pubkey())


def test_secret_is_encrypted_at_rest():
    """The raw secret must never be readable from the database file."""
    account = vault.register("alice")
    keypair = vault.load_keypair("alice")
    secret = bytes(keypair)

    db = Path(get_settings().data_dir) / "vault.db"
    raw = db.read_bytes()

    assert secret not in raw, "private key found in plaintext in the vault DB"
    # The public key is not a secret, but confirm the stored blob is ciphertext.
    with sqlite3.connect(db) as conn:
        stored = conn.execute(
            "SELECT enc_secret FROM agent_wallets WHERE x_handle='alice'"
        ).fetchone()[0]
    conn.close()
    assert stored.startswith("gAAAAA"), "expected a Fernet token"
    assert account.pubkey not in stored


def test_wrong_vault_key_cannot_decrypt(monkeypatch):
    """A rotated or wrong AGENT_VAULT_KEY fails loudly, not silently."""
    vault.register("alice")
    monkeypatch.setenv("AGENT_VAULT_KEY", "a-completely-different-key-0123456789abcd")
    get_settings.cache_clear()

    with pytest.raises(vault.VaultError, match="decrypt"):
        vault.load_keypair("alice")


def test_short_vault_key_is_rejected(monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_KEY", "tooshort")
    get_settings.cache_clear()
    with pytest.raises(vault.VaultError, match="32 characters"):
        vault.register("alice")


@pytest.mark.parametrize(
    "bad", ["", "@", "a" * 16, "alice bob", "../../etc/passwd", "alice';--", "ali\x00ce"]
)
def test_invalid_handles_are_rejected(bad):
    """A crafted handle must not create or reach a row."""
    with pytest.raises(vault.VaultError):
        vault.normalize_handle(bad)


def test_get_returns_none_for_unknown_handle():
    assert vault.get("nobody") is None
    assert vault.get("!!invalid!!") is None


def test_missing_wallet_raises_on_load():
    with pytest.raises(vault.VaultError, match="register first"):
        vault.load_keypair("ghost")
