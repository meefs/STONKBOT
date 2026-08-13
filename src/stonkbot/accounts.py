"""X handle → Solana wallet linking (simple SQLite)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import LinkedAccount

DB_PATH = Path("data/accounts.db")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            x_handle TEXT PRIMARY KEY,
            solana_wallet TEXT NOT NULL,
            linked_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    c.commit()
    return c


def link(x_handle: str, solana_wallet: str) -> LinkedAccount:
    handle = x_handle.lstrip("@").lower()
    wallet = solana_wallet.strip()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO accounts (x_handle, solana_wallet, linked_at, active) VALUES (?,?,?,1)",
            (handle, wallet, now),
        )
    return LinkedAccount(x_handle=handle, solana_wallet=wallet, linked_at=datetime.fromisoformat(now))


def get(x_handle: str) -> LinkedAccount | None:
    handle = x_handle.lstrip("@").lower()
    with _conn() as c:
        row = c.execute(
            "SELECT x_handle, solana_wallet, linked_at, active FROM accounts WHERE x_handle=? AND active=1",
            (handle,),
        ).fetchone()
    if not row:
        return None
    return LinkedAccount(
        x_handle=row[0],
        solana_wallet=row[1],
        linked_at=datetime.fromisoformat(row[2]),
        active=bool(row[3]),
    )


def unlink(x_handle: str) -> bool:
    handle = x_handle.lstrip("@").lower()
    with _conn() as c:
        cur = c.execute("UPDATE accounts SET active=0 WHERE x_handle=?", (handle,))
        return cur.rowcount > 0
