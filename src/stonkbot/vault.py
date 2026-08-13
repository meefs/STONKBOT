"""Encrypted per-user agent wallet vault.

Keys never appear in logs or X replies. Master key = AGENT_VAULT_KEY in env.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from .config import get_settings
from .models import AgentAccount

DB_PATH = Path("data/vault.db")


def _fernet() -> Fernet:
    s = get_settings()
    if not s.agent_vault_key:
        raise RuntimeError("AGENT_VAULT_KEY not set — required for agent wallets")
    # Derive 32-byte url-safe key from master string
    digest = hashlib.sha256(s.agent_vault_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_wallets (
            x_handle TEXT PRIMARY KEY,
            pubkey TEXT NOT NULL,
            enc_secret TEXT NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    c.commit()
    return c


def register(x_handle: str) -> AgentAccount:
    """Create a new agent wallet for this X handle (or return existing)."""
    handle = x_handle.lstrip("@").lower()
    existing = get(handle)
    if existing:
        return existing

    from solders.keypair import Keypair

    kp = Keypair()
    pubkey = str(kp.pubkey())
    secret_bytes = bytes(kp)  # 64-byte secret
    enc = _fernet().encrypt(secret_bytes).decode()
    now = datetime.now(timezone.utc).isoformat()

    with _conn() as c:
        c.execute(
            "INSERT INTO agent_wallets (x_handle, pubkey, enc_secret, created_at, active) VALUES (?,?,?,?,1)",
            (handle, pubkey, enc, now),
        )
    return AgentAccount(x_handle=handle, pubkey=pubkey, created_at=datetime.fromisoformat(now))


def get(x_handle: str) -> AgentAccount | None:
    handle = x_handle.lstrip("@").lower()
    with _conn() as c:
        row = c.execute(
            "SELECT x_handle, pubkey, created_at, active FROM agent_wallets WHERE x_handle=? AND active=1",
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
    """Load Keypair for signing. Never log the result."""
    handle = x_handle.lstrip("@").lower()
    with _conn() as c:
        row = c.execute(
            "SELECT enc_secret FROM agent_wallets WHERE x_handle=? AND active=1",
            (handle,),
        ).fetchone()
    if not row:
        raise RuntimeError("no agent wallet — register first")
    from solders.keypair import Keypair

    raw = _fernet().decrypt(row[0].encode())
    return Keypair.from_bytes(raw)


def sign_tx_b64(x_handle: str, unsigned_b64: str) -> str:
    import base64 as b64

    from solders.message import to_bytes_versioned
    from solders.transaction import VersionedTransaction

    kp = load_keypair(x_handle)
    raw = b64.b64decode(unsigned_b64)
    try:
        tx = VersionedTransaction.from_bytes(raw)
        sig = kp.sign_message(to_bytes_versioned(tx.message))
        signed = VersionedTransaction.populate(tx.message, [sig])
        return b64.b64encode(bytes(signed)).decode()
    except Exception:
        from solana.transaction import Transaction

        tx = Transaction.deserialize(raw)
        tx.sign(kp)
        return b64.b64encode(tx.serialize()).decode()
