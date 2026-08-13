"""STONKBOT service-fee accounting and the referral rebate.

This tracks STONKBOT's *own* fee only. It has nothing to do with StonkFun's
trading fees — those are settled on chain by StonkFun and claimed by the
creator from the token page.

Per successful launch, ``service_fee_sol`` is split:
  - ``referral_share`` → the referrer's agent wallet, when there is one
  - the remainder      → the operator's ``fee_recipient``

Every row records what was owed and, once the transfer confirms, the signature
that paid it. A row that never reaches ``paid`` is a real unpaid debt, visible
via :func:`outstanding`.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import UTC, datetime

from .config import get_settings
from .db import connect, dialect
from .dialect import table

log = logging.getLogger("stonkbot.fees")

DB_NAME = "fees.db"

def _schema() -> str:
    return table(
        dialect(),
        "fee_events",
        "id {serial_pk}, "
        "x_handle {text}, "
        "mint {text}, "
        "amount_sol REAL NOT NULL, "
        "recipient {text} NOT NULL, "
        "status {text} NOT NULL, "
        "created_at {timestamp} NOT NULL, "
        "role {text} NOT NULL DEFAULT 'platform', "
        "ref_handle {text}, "
        "signature {text}",
    )


_MIGRATIONS = (
    ("role", "ALTER TABLE fee_events ADD COLUMN role TEXT DEFAULT 'platform'"),
    ("ref_handle", "ALTER TABLE fee_events ADD COLUMN ref_handle TEXT"),
    ("signature", "ALTER TABLE fee_events ADD COLUMN signature TEXT"),
)


@contextmanager
def _conn():
    """Fee ledger. Stored under the same owner-only permissions as the vault:
    it links X handles to wallet addresses and amounts."""
    with connect(DB_NAME, _schema()) as c:
        # Additive migrations for databases created by earlier versions.
        # Postgres takes ADD COLUMN IF NOT EXISTS; SQLite has no such clause,
        # so it inspects the table first.
        if dialect().name == "postgres":
            for _, ddl in _MIGRATIONS:
                c.execute(ddl.replace("ADD COLUMN", "ADD COLUMN IF NOT EXISTS"))
        else:
            cols = {r[1] for r in c.execute("PRAGMA table_info(fee_events)").fetchall()}
            for column, ddl in _MIGRATIONS:
                if column not in cols:
                    c.execute(ddl)
        yield c


def split_amounts(ref_handle: str | None, launcher_handle: str) -> dict:
    """Split the service fee between the operator and a referrer.

    Self-referral is rejected: it would otherwise let a user rebate themselves
    30% of their own service fee on every launch.
    """
    s = get_settings()
    total = float(s.service_fee_sol)
    launcher = (launcher_handle or "").lstrip("@").lower()
    ref = (ref_handle or "").lstrip("@").lower() or None

    if ref and ref == launcher:
        ref = None

    if ref:
        ref_amount = round(total * float(s.referral_share), 6)
        platform_amount = round(total - ref_amount, 6)
    else:
        ref_amount = 0.0
        platform_amount = total

    return {
        "total": total,
        "platform": platform_amount,
        "referrer": ref_amount,
        "ref_handle": ref,
        "platform_recipient": s.fee_recipient,
    }


def record_expected(
    x_handle: str,
    mint: str | None = None,
    ref_handle: str | None = None,
    ref_recipient: str | None = None,
) -> dict:
    """Record what this launch owes, before any transfer is attempted.

    Returns the split plus the row ids, so the caller can mark each leg paid
    with the signature that settled it.
    """
    s = get_settings()
    split = split_amounts(ref_handle, x_handle)
    ref = split["ref_handle"]
    now = datetime.now(UTC).isoformat()
    handle = (x_handle or "").lstrip("@").lower()

    # A referral leg is only owed if we actually know where to send it.
    pay_referrer = bool(ref and split["referrer"] > 0 and ref_recipient)
    if ref and not ref_recipient:
        log.warning("referrer @%s has no agent wallet — rebate not owed", ref)

    platform_amount = split["platform"]
    referrer_amount = split["referrer"] if pay_referrer else 0.0
    if not pay_referrer:
        # No payable referrer: the whole fee is the operator's.
        platform_amount = split["total"]

    with _conn() as c:
        # insert_returning_id, not lastrowid: Postgres has no lastrowid.
        platform_id = c.insert_returning_id(
            "INSERT INTO fee_events "
            "(x_handle, mint, amount_sol, recipient, status, created_at, role, ref_handle) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (handle, mint, platform_amount, s.fee_recipient, "expected", now,
             "platform", ref),
        )
        referrer_id = None
        if pay_referrer:
            referrer_id = c.insert_returning_id(
                "INSERT INTO fee_events "
                "(x_handle, mint, amount_sol, recipient, status, created_at, role, ref_handle) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (handle, mint, referrer_amount, ref_recipient, "expected", now,
                 "referrer", ref),
            )

    return {
        "total_sol": split["total"],
        "platform_sol": platform_amount,
        "platform_recipient": s.fee_recipient,
        "platform_id": platform_id,
        "referrer_sol": referrer_amount,
        "referrer_recipient": ref_recipient if pay_referrer else None,
        "referrer_id": referrer_id,
        "ref_handle": ref if pay_referrer else None,
    }


def mark_paid(row_id: int, signature: str | None = None) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE fee_events SET status='paid', signature=? WHERE id=?",
            (signature, row_id),
        )


def mark_failed(row_id: int) -> None:
    with _conn() as c:
        c.execute("UPDATE fee_events SET status='failed' WHERE id=?", (row_id,))


def pending_total() -> float:
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(amount_sol),0) FROM fee_events WHERE status='expected'"
        ).fetchone()
    return float(row[0] if row else 0)


def outstanding(limit: int = 50) -> list[dict]:
    """Fee legs that were owed but never settled — an operator to-do list."""
    with _conn() as c:
        rows = c.execute(
            "SELECT id, x_handle, mint, amount_sol, recipient, role, status, created_at "
            "FROM fee_events WHERE status IN ('expected','failed') "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r[0],
            "x_handle": r[1],
            "mint": r[2],
            "amount_sol": r[3],
            "recipient": r[4],
            "role": r[5],
            "status": r[6],
            "created_at": r[7],
        }
        for r in rows
    ]


def referral_earnings(ref_handle: str) -> dict:
    """Confirmed and pending rebate totals for a referrer."""
    handle = (ref_handle or "").lstrip("@").lower()
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(CASE WHEN status='paid' THEN amount_sol END),0), "
            "       COALESCE(SUM(CASE WHEN status='expected' THEN amount_sol END),0), "
            "       COUNT(*) "
            "FROM fee_events WHERE role='referrer' AND ref_handle=?",
            (handle,),
        ).fetchone()
    return {
        "paid_sol": round(float(row[0]), 6),
        "pending_sol": round(float(row[1]), 6),
        "referred_launches": int(row[2]),
    }
