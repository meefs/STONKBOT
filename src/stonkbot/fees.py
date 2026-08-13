"""Service fee tracking. 0.1 SOL per successful launch → operator wallet.

Actual on-chain transfer is operator/hot-wallet responsibility once live.
This module records expected fees and status.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings

DB_PATH = Path("data/fees.db")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS fee_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            x_handle TEXT,
            mint TEXT,
            amount_sol REAL NOT NULL,
            recipient TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    c.commit()
    return c


def record_expected(x_handle: str, mint: str | None = None) -> dict:
    s = get_settings()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO fee_events (x_handle, mint, amount_sol, recipient, status, created_at) VALUES (?,?,?,?,?,?)",
            (x_handle.lstrip("@").lower(), mint, s.service_fee_sol, s.fee_recipient, "expected", now),
        )
    return {
        "amount_sol": s.service_fee_sol,
        "recipient": s.fee_recipient,
        "status": "expected",
    }


def mark_paid(row_id: int) -> None:
    with _conn() as c:
        c.execute("UPDATE fee_events SET status='paid' WHERE id=?", (row_id,))


def pending_total() -> float:
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(amount_sol),0) FROM fee_events WHERE status='expected'"
        ).fetchone()
    return float(row[0] if row else 0)
