"""Durable bot state: poll cursor and processed-mention set.

The previous poll loop kept ``since_id`` in a local variable, so every restart
replayed the whole mentions timeline — re-answering old commands and, before
idempotency existed, potentially re-launching them. Both the cursor and the set
of already-handled mention ids now survive a restart.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

from .db import connect

DB_NAME = "state.db"

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """
    CREATE TABLE IF NOT EXISTS seen_mentions (
        mention_id TEXT PRIMARY KEY,
        seen_at TEXT NOT NULL
    )
    """,
)


@contextmanager
def _conn():
    with connect(DB_NAME, _SCHEMA) as c:
        yield c


def get_value(key: str) -> str | None:
    with _conn() as c:
        row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_value(key: str, value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO kv (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_since_id() -> int | None:
    raw = get_value("since_id")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def set_since_id(value: int) -> None:
    """Advance the cursor monotonically — never move it backwards."""
    current = get_since_id() or 0
    if value > current:
        set_value("since_id", str(value))


def mark_seen(mention_id: str) -> bool:
    """Record a mention as handled.

    Returns True if this call claimed it, False if it was already seen. The
    INSERT is the claim, so a duplicate delivery cannot be processed twice.
    """
    now = datetime.now(UTC).isoformat()
    with _conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO seen_mentions (mention_id, seen_at) VALUES (?,?)",
            (str(mention_id), now),
        )
        return cur.rowcount > 0


def prune_seen(keep: int = 5000) -> None:
    """Bound the table so it cannot grow without limit."""
    with _conn() as c:
        c.execute(
            "DELETE FROM seen_mentions WHERE mention_id NOT IN "
            "(SELECT mention_id FROM seen_mentions ORDER BY seen_at DESC LIMIT ?)",
            (keep,),
        )
