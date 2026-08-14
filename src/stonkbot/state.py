"""Durable bot state: poll cursor and processed-mention set.

The previous poll loop kept ``since_id`` in a local variable, so every restart
replayed the whole mentions timeline — re-answering old commands and, before
idempotency existed, potentially re-launching them. Both the cursor and the set
of already-handled mention ids now survive a restart.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

from .db import connect, dialect
from .dialect import table

DB_NAME = "state.db"


def _schema() -> tuple[str, ...]:
    d = dialect()
    return (
        table(d, "kv", "key {text} PRIMARY KEY, value {text} NOT NULL"),
        table(
            d,
            "seen_mentions",
            "mention_id {text} PRIMARY KEY, seen_at {timestamp} NOT NULL",
        ),
    )


@contextmanager
def _conn():
    with connect(DB_NAME, _schema()) as c:
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


def is_seen(mention_id: str) -> bool:
    """Whether a mention has already been handled, without claiming it.

    :func:`mark_seen` is a claim — asking it a question changes the answer. The
    backlog guard needs to count what is still waiting *before* deciding
    whether to touch any of it, so it needs a read that leaves no trace.
    """
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM seen_mentions WHERE mention_id=?", (str(mention_id),)
        ).fetchone()
    return row is not None


def unset_since_id() -> None:
    """Clear the cursor so the next poll starts from the timeline head again.

    Separate from :func:`set_since_id` on purpose — that one refuses to move
    backwards, which is right for the loop and wrong for an operator who needs
    to undo a run that consumed mentions before anyone could read its log.
    """
    with _conn() as c:
        c.execute("DELETE FROM kv WHERE key='since_id'")


def seen_total() -> int:
    with _conn() as c:
        row = c.execute("SELECT COUNT(*) FROM seen_mentions").fetchone()
    return int(row[0] if row else 0)


def seen_recent(limit: int = 10) -> list[tuple[str, str]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT mention_id, seen_at FROM seen_mentions "
            "ORDER BY seen_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def clear_seen() -> int:
    """Forget every handled mention. Returns how many rows went.

    Destructive: the handled set is what stops a mention being answered twice.
    """
    with _conn() as c:
        cursor = c.execute("DELETE FROM seen_mentions")
        return int(cursor.rowcount or 0)


def prune_seen(keep: int = 5000) -> None:
    """Bound the table so it cannot grow without limit."""
    with _conn() as c:
        c.execute(
            "DELETE FROM seen_mentions WHERE mention_id NOT IN "
            "(SELECT mention_id FROM seen_mentions ORDER BY seen_at DESC LIMIT ?)",
            (keep,),
        )
