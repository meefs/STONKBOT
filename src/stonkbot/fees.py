"""Service fee tracking + referral split.

Total service fee (default 0.1 SOL) per successful launch:
  - referral_share (default 30%) → referrer agent wallet
  - remainder → operator fee_recipient
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
            created_at TEXT NOT NULL,
            role TEXT DEFAULT 'platform',
            ref_handle TEXT
        )
        """
    )
    # additive columns for older DBs
    cols = {r[1] for r in c.execute("PRAGMA table_info(fee_events)").fetchall()}
    if "role" not in cols:
        c.execute("ALTER TABLE fee_events ADD COLUMN role TEXT DEFAULT 'platform'")
    if "ref_handle" not in cols:
        c.execute("ALTER TABLE fee_events ADD COLUMN ref_handle TEXT")
    c.commit()
    return c


def split_amounts(ref_handle: str | None, launcher_handle: str) -> dict:
    """Return platform/ref amounts. Self-ref blocked."""
    s = get_settings()
    total = float(s.service_fee_sol)
    launcher = launcher_handle.lstrip("@").lower()
    ref = (ref_handle or "").lstrip("@").lower() or None
    if ref and ref == launcher:
        ref = None
    if ref:
        ref_amt = round(total * float(s.referral_share), 6)
        plat_amt = round(total - ref_amt, 6)
    else:
        ref_amt = 0.0
        plat_amt = total
    return {
        "total": total,
        "platform": plat_amt,
        "referrer": ref_amt,
        "ref_handle": ref,
        "platform_recipient": s.fee_recipient,
    }


def record_expected(
    x_handle: str,
    mint: str | None = None,
    ref_handle: str | None = None,
    platform_amount: float | None = None,
    ref_amount: float | None = None,
    ref_recipient: str | None = None,
) -> dict:
    s = get_settings()
    split = split_amounts(ref_handle, x_handle)
    plat = platform_amount if platform_amount is not None else split["platform"]
    ref_amt = ref_amount if ref_amount is not None else split["referrer"]
    ref = split["ref_handle"]
    now = datetime.now(timezone.utc).isoformat()
    handle = x_handle.lstrip("@").lower()

    with _conn() as c:
        c.execute(
            "INSERT INTO fee_events (x_handle, mint, amount_sol, recipient, status, created_at, role, ref_handle) VALUES (?,?,?,?,?,?,?,?)",
            (handle, mint, plat, s.fee_recipient, "expected", now, "platform", ref),
        )
        if ref and ref_amt > 0 and ref_recipient:
            c.execute(
                "INSERT INTO fee_events (x_handle, mint, amount_sol, recipient, status, created_at, role, ref_handle) VALUES (?,?,?,?,?,?,?,?)",
                (handle, mint, ref_amt, ref_recipient, "expected", now, "referrer", ref),
            )

    return {
        "amount_sol": s.service_fee_sol,
        "platform_sol": plat,
        "referrer_sol": ref_amt,
        "ref_handle": ref,
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
