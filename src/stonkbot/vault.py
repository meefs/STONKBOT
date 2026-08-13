"""Encrypted per-user agent wallet vault.

One Solana keypair per X handle. The secret key is encrypted at rest with
Fernet (AES-128-CBC + HMAC-SHA256, authenticated) and is only ever decrypted
inside :func:`load_keypair`, for the moment of signing.

Key derivation
--------------
The encryption key is derived from ``AGENT_VAULT_KEY`` with **scrypt**, using a
random per-install salt. Earlier versions used a single SHA-256 of the master
key, which is fast enough to brute-force offline if the database ever leaked
alongside a weak key. scrypt is deliberately slow and memory-hard, so the same
leak is far more expensive to attack.

Existing vaults keep working: each row records which derivation encrypted it,
and a v1 row is transparently re-encrypted to v2 the next time it is read.
Nothing needs to be migrated by hand.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings
from .db import connect, dialect
from .dialect import table
from .models import AgentAccount

log = logging.getLogger("stonkbot.vault")

DB_NAME = "vault.db"

# Minimum entropy insisted on for the master key.
MIN_VAULT_KEY_LENGTH = 32

# Encryption schemes, newest first.
KDF_LEGACY_SHA256 = 1
KDF_SCRYPT = 2
CURRENT_KDF = KDF_SCRYPT

# scrypt parameters. n=2**14 with r=8 costs ~16 MB and tens of milliseconds per
# derivation — negligible for a bot signing a handful of launches, and a hard
# wall for anyone brute-forcing a stolen database.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024

def _schema() -> tuple[str, ...]:
    d = dialect()
    return (
        table(
            d,
            "agent_wallets",
            "x_handle {text} PRIMARY KEY, "
            "pubkey {text} NOT NULL, "
            "enc_secret {text} NOT NULL, "
            "created_at {timestamp} NOT NULL, "
            "active INTEGER NOT NULL DEFAULT 1",
        ),
        table(d, "vault_meta", "key {text} PRIMARY KEY, value {text} NOT NULL"),
    )


class VaultError(Exception):
    """Vault is misconfigured or a secret could not be recovered."""


def _conn():
    return connect(DB_NAME, _schema())


def _ensure_columns(c) -> None:
    """Add the kdf column to databases created before versioned encryption.

    Only meaningful for SQLite installs that predate versioned encryption; a
    Postgres store is always created with the column. ``IF NOT EXISTS`` on
    ADD COLUMN is Postgres-only, so the backends take different routes to the
    same idempotent result.
    """
    if dialect().name == "postgres":
        c.execute(
            f"ALTER TABLE agent_wallets ADD COLUMN IF NOT EXISTS kdf "
            f"INTEGER NOT NULL DEFAULT {KDF_LEGACY_SHA256}"
        )
        return

    columns = {row[1] for row in c.execute("PRAGMA table_info(agent_wallets)")}
    if "kdf" not in columns:
        c.execute(
            f"ALTER TABLE agent_wallets ADD COLUMN kdf INTEGER NOT NULL "
            f"DEFAULT {KDF_LEGACY_SHA256}"
        )


def _get_salt(c) -> bytes:
    """Fetch this install's scrypt salt, creating one on first use.

    The salt is not a secret — it exists so two installs sharing a master key
    do not share derived keys, and so precomputation is useless.
    """
    row = c.execute("SELECT value FROM vault_meta WHERE key='kdf_salt'").fetchone()
    if row:
        return base64.b64decode(row[0])

    salt = os.urandom(16)
    c.execute(
        "INSERT OR IGNORE INTO vault_meta (key, value) VALUES ('kdf_salt', ?)",
        (base64.b64encode(salt).decode(),),
    )
    # Re-read: another process may have won the race and written a different
    # salt, and using ours would make their rows undecryptable.
    row = c.execute("SELECT value FROM vault_meta WHERE key='kdf_salt'").fetchone()
    return base64.b64decode(row[0])


def _master_key() -> str:
    key = get_settings().agent_vault_key
    if not key:
        raise VaultError("AGENT_VAULT_KEY not set — required for agent wallets")
    if len(key) < MIN_VAULT_KEY_LENGTH:
        raise VaultError(
            f"AGENT_VAULT_KEY must be at least {MIN_VAULT_KEY_LENGTH} characters"
        )
    return key


def _fernet(kdf: int, salt: bytes | None) -> Fernet:
    key = _master_key()
    if kdf == KDF_SCRYPT:
        if not salt:
            raise VaultError("vault salt missing")
        derived = hashlib.scrypt(
            key.encode(),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=32,
            maxmem=_SCRYPT_MAXMEM,
        )
    elif kdf == KDF_LEGACY_SHA256:
        derived = hashlib.sha256(key.encode()).digest()
    else:
        raise VaultError(f"unknown vault encryption version {kdf}")
    return Fernet(base64.urlsafe_b64encode(derived))


def normalize_handle(x_handle: str) -> str:
    """Canonical form of an X handle.

    X handles are case-insensitive and unique, so lowercasing is a safe
    identity. Anything outside X's own character set is rejected so a crafted
    handle cannot collide with another user's row.
    """
    handle = (x_handle or "").strip().lstrip("@").lower()
    if not handle or len(handle) > 15:
        raise VaultError("invalid X handle")
    if not all(c.isalnum() or c == "_" for c in handle):
        raise VaultError("invalid X handle")
    return handle


def register(x_handle: str) -> AgentAccount:
    """Create this handle's agent wallet, or return the existing one.

    Safe against concurrent callers: the INSERT is conditional and we re-read
    afterwards, so two racing registrations converge on one wallet. Overwriting
    would strand any funds already sent to the first address.
    """
    handle = normalize_handle(x_handle)

    existing = get(handle)
    if existing:
        return existing

    from solders.keypair import Keypair

    keypair = Keypair()
    pubkey = str(keypair.pubkey())
    now = datetime.now(UTC).isoformat()

    with _conn() as c:
        _ensure_columns(c)
        salt = _get_salt(c)
        encrypted = _fernet(CURRENT_KDF, salt).encrypt(bytes(keypair)).decode()
        c.execute(
            "INSERT OR IGNORE INTO agent_wallets "
            "(x_handle, pubkey, enc_secret, created_at, active, kdf) VALUES (?,?,?,?,1,?)",
            (handle, pubkey, encrypted, now, CURRENT_KDF),
        )

    account = get(handle)
    if not account:
        raise VaultError("failed to create agent wallet")
    if account.pubkey != pubkey:
        log.info("concurrent registration for @%s resolved to existing wallet", handle)
    return account


def get(x_handle: str) -> AgentAccount | None:
    try:
        handle = normalize_handle(x_handle)
    except VaultError:
        return None
    with _conn() as c:
        _ensure_columns(c)
        row = c.execute(
            "SELECT x_handle, pubkey, created_at, active FROM agent_wallets "
            "WHERE x_handle=? AND active=1",
            (handle,),
        ).fetchone()
    if not row:
        return None
    return AgentAccount(
        x_handle=row[0],
        pubkey=row[1],
        created_at=datetime.fromisoformat(row[2]),
        active=bool(row[3]),
    )


def load_keypair(x_handle: str):
    """Decrypt this handle's keypair for signing. Never log or return the bytes.

    Scoped strictly by handle: no code path loads a keypair for any handle
    other than the one asked for, so one user's command can never reach another
    user's wallet.

    A row still using the legacy derivation is re-encrypted with the current
    one here, so vaults migrate themselves as they are used.
    """
    handle = normalize_handle(x_handle)

    with _conn() as c:
        _ensure_columns(c)
        row = c.execute(
            "SELECT enc_secret, kdf FROM agent_wallets WHERE x_handle=? AND active=1",
            (handle,),
        ).fetchone()
        if not row:
            raise VaultError("no agent wallet — register first")

        encrypted, kdf = row[0], int(row[1])
        salt = _get_salt(c)

        try:
            raw = _fernet(kdf, salt).decrypt(encrypted.encode())
        except InvalidToken as e:
            raise VaultError(
                "could not decrypt agent wallet — AGENT_VAULT_KEY may have changed"
            ) from e

        if kdf != CURRENT_KDF:
            try:
                upgraded = _fernet(CURRENT_KDF, salt).encrypt(raw).decode()
                c.execute(
                    "UPDATE agent_wallets SET enc_secret=?, kdf=? WHERE x_handle=?",
                    (upgraded, CURRENT_KDF, handle),
                )
                log.info("re-encrypted @%s to vault scheme v%d", handle, CURRENT_KDF)
            except Exception:
                # A failed upgrade must never block a working launch.
                log.warning("could not upgrade vault encryption for @%s", handle)

    from solders.keypair import Keypair

    return Keypair.from_bytes(raw)


def sign_transaction_b64(x_handle: str, unsigned_b64: str) -> str:
    """Sign a base64 transaction with this handle's agent wallet.

    Callers must have already run the transaction through
    :mod:`stonkbot.txguard`; this only performs the signature, it does not
    decide whether signing is safe.
    """
    import base64 as b64

    from solders.message import to_bytes_versioned
    from solders.transaction import VersionedTransaction

    keypair = load_keypair(x_handle)
    raw = b64.b64decode(unsigned_b64)

    # VersionedTransaction parses both legacy and v0 wire formats.
    tx = VersionedTransaction.from_bytes(raw)
    signature = keypair.sign_message(to_bytes_versioned(tx.message))
    signed = VersionedTransaction.populate(tx.message, [signature])
    return b64.b64encode(bytes(signed)).decode()


def deactivate(x_handle: str) -> bool:
    """Soft-delete a wallet. The encrypted secret is retained so a user with
    funds still at that address is not locked out."""
    handle = normalize_handle(x_handle)
    with _conn() as c:
        cursor = c.execute(
            "UPDATE agent_wallets SET active=0 WHERE x_handle=?", (handle,)
        )
        return cursor.rowcount > 0


def encryption_status() -> dict:
    """Counts by encryption scheme, for `cli doctor`. Reveals no secrets."""
    with _conn() as c:
        _ensure_columns(c)
        rows = c.execute(
            "SELECT kdf, COUNT(*) FROM agent_wallets GROUP BY kdf"
        ).fetchall()
    by_scheme = {int(k): int(v) for k, v in rows}
    return {
        "total_wallets": sum(by_scheme.values()),
        "scrypt_v2": by_scheme.get(KDF_SCRYPT, 0),
        "legacy_sha256_v1": by_scheme.get(KDF_LEGACY_SHA256, 0),
    }
