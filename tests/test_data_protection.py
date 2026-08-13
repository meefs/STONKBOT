"""Data-at-rest protection: encryption scheme, migration, and file permissions."""

from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from stonkbot import db, fees, vault
from stonkbot.config import get_settings


def _vault_db() -> Path:
    return Path(get_settings().data_dir) / "vault.db"


# --- encryption scheme ------------------------------------------------------


def test_new_wallets_use_scrypt():
    """New rows must use the slow KDF, not the legacy single SHA-256."""
    vault.register("alice")
    status = vault.encryption_status()
    assert status["total_wallets"] == 1
    assert status["scrypt_v2"] == 1
    assert status["legacy_sha256_v1"] == 0


def test_secret_is_not_recoverable_with_the_legacy_derivation():
    """A v2 row must not be decryptable by the old sha256(key) scheme.

    If it were, the upgrade would be cosmetic.
    """
    vault.register("alice")
    with sqlite3.connect(_vault_db()) as conn:
        stored = conn.execute(
            "SELECT enc_secret FROM agent_wallets WHERE x_handle='alice'"
        ).fetchone()[0]
    conn.close()

    legacy_key = base64.urlsafe_b64encode(
        hashlib.sha256(get_settings().agent_vault_key.encode()).digest()
    )
    with pytest.raises(InvalidToken):
        Fernet(legacy_key).decrypt(stored.encode())


def test_legacy_rows_still_decrypt_and_are_upgraded():
    """An existing v1 vault must keep working and migrate itself on read.

    Breaking these rows would strand every wallet created before the upgrade.
    """
    from solders.keypair import Keypair

    account = vault.register("alice")  # creates schema + salt
    keypair = Keypair()
    legacy_key = base64.urlsafe_b64encode(
        hashlib.sha256(get_settings().agent_vault_key.encode()).digest()
    )
    legacy_blob = Fernet(legacy_key).encrypt(bytes(keypair)).decode()

    with sqlite3.connect(_vault_db()) as conn:
        conn.execute(
            "INSERT INTO agent_wallets "
            "(x_handle, pubkey, enc_secret, created_at, active, kdf) "
            "VALUES ('bob', ?, ?, '2026-01-01T00:00:00+00:00', 1, 1)",
            (str(keypair.pubkey()), legacy_blob),
        )
    conn.close()

    assert vault.encryption_status()["legacy_sha256_v1"] == 1

    # Reading it must succeed...
    loaded = vault.load_keypair("bob")
    assert str(loaded.pubkey()) == str(keypair.pubkey())

    # ...and silently re-encrypt it under the current scheme.
    status = vault.encryption_status()
    assert status["legacy_sha256_v1"] == 0
    assert status["scrypt_v2"] == 2

    # Still loadable after the upgrade.
    assert str(vault.load_keypair("bob").pubkey()) == str(keypair.pubkey())
    assert account.pubkey != str(keypair.pubkey())


def test_plaintext_secret_never_touches_disk():
    vault.register("alice")
    secret = bytes(vault.load_keypair("alice"))
    raw = _vault_db().read_bytes()
    assert secret not in raw
    # Nor any 16-byte window of it, which would indicate partial leakage.
    assert secret[:16] not in raw


# --- file permissions -------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes only")
def test_data_directory_is_owner_only():
    vault.register("alice")
    mode = stat.S_IMODE(Path(get_settings().data_dir).stat().st_mode)
    assert not (mode & 0o077), f"data dir is group/world accessible: {oct(mode)}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes only")
def test_every_database_file_is_owner_only():
    """Vault, ledger and state must all be unreadable by other host accounts."""
    vault.register("alice")
    fees.record_expected("alice", "MINT1")

    directory = Path(get_settings().data_dir)
    files = list(directory.glob("*.db*"))
    assert files, "expected database files to exist"

    for path in files:
        mode = stat.S_IMODE(path.stat().st_mode)
        assert not (mode & 0o077), f"{path.name} is group/world readable: {oct(mode)}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes only")
def test_permissions_report_flags_a_loosened_file():
    """The doctor check must actually notice a bad mode."""
    vault.register("alice")
    assert all(entry["ok"] for entry in db.permissions_report())

    os.chmod(_vault_db(), 0o644)
    report = db.permissions_report()
    assert any(not entry["ok"] and entry["path"] == "vault.db" for entry in report)


# --- ledger -----------------------------------------------------------------


def test_fee_ledger_round_trips_through_the_shared_layer():
    """The storage refactor must not have changed ledger behaviour."""
    referrer = vault.register("bob")
    owed = fees.record_expected(
        "alice", "MINT1", ref_handle="bob", ref_recipient=referrer.pubkey
    )
    fees.mark_paid(owed["referrer_id"], "sig")
    assert fees.referral_earnings("bob")["paid_sol"] == pytest.approx(0.03)
