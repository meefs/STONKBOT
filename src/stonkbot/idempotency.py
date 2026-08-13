"""Durable single-execution guard for paid operations.

StonkFun's documentation is unambiguous: *"never pay twice — a second payment
creates a second token."* A crash-restart, an X timeline replaying a mention,
or a user tweeting the same command twice must therefore not be able to
produce a second paid launch.

This stores one row per launch key with a state machine:

    (absent) --claim--> running --resolve--> done
                          |
                          +----release----> (absent)

``claim`` is atomic: the INSERT itself is the lock, so two concurrent workers
cannot both enter ``running``. A ``done`` row carries the original result,
which is replayed instead of re-running the launch.

Stale ``running`` rows (a process killed mid-launch) are *not* auto-recovered
into a retry, because we cannot know whether the payment landed. They expire
only after ``STALE_SECONDS`` and are surfaced for operator review.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import UTC, datetime

from .db import connect, dialect
from .dialect import table

log = logging.getLogger("stonkbot.idempotency")

# A launch that has been 'running' longer than this is presumed abandoned.
# Generous on purpose: a real launch polls for up to ~60s, and expiring a row
# while its payment is in flight is exactly the mistake this module prevents.
STALE_SECONDS = 900


DB_NAME = "launches.db"

def _schema() -> str:
    return table(
        dialect(),
        "launch_keys",
        "key {text} PRIMARY KEY, "
        "state {text} NOT NULL, "
        "result_json {text}, "
        "created_at {timestamp} NOT NULL, "
        "updated_at {timestamp} NOT NULL",
    )


@contextmanager
def _conn():
    with connect(DB_NAME, _schema()) as c:
        yield c


class LaunchAlreadyRunning(Exception):
    """Another worker holds this key right now."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _age_seconds(timestamp: str) -> float:
    try:
        then = datetime.fromisoformat(timestamp)
    except ValueError:
        return 0.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (datetime.now(UTC) - then).total_seconds()


def claim(key: str) -> dict | None:
    """Take exclusive ownership of ``key``.

    Returns ``None`` when the caller may proceed with the launch, or the stored
    result dict when this key already completed (replay it, do not re-run).
    Raises :class:`LaunchAlreadyRunning` if another worker holds it.
    """
    now = _now()
    with _conn() as c:
        # INSERT OR IGNORE rather than catching a uniqueness violation: in
        # Postgres a failed statement poisons the whole transaction, so the
        # SELECT below would raise InFailedSqlTransaction instead of reading
        # the existing row. rowcount tells us who won the race on both backends.
        claimed = c.execute(
            "INSERT OR IGNORE INTO launch_keys (key, state, created_at, updated_at) "
            "VALUES (?, 'running', ?, ?)",
            (key, now, now),
        )
        if claimed.rowcount > 0:
            return None

        row = c.execute(
            "SELECT state, result_json, updated_at FROM launch_keys WHERE key=?",
            (key,),
        ).fetchone()

        if not row:
            raise LaunchAlreadyRunning(key)

        state, result_json, updated_at = row

        if state == "done":
            if result_json:
                try:
                    return json.loads(result_json)
                except json.JSONDecodeError:
                    log.warning("corrupt result for key=%s", key)
            return {"status": "completed", "message": "already launched"}

        if state == "running":
            if _age_seconds(updated_at) < STALE_SECONDS:
                raise LaunchAlreadyRunning(key)
            # Stale. Reclaim it, but only because enough time has passed that
            # any in-flight payment has long since settled one way or another.
            log.warning("reclaiming stale launch key=%s", key)
            c.execute(
                "UPDATE launch_keys SET updated_at=? WHERE key=? AND state='running'",
                (now, key),
            )
            return None

        raise LaunchAlreadyRunning(key)


def resolve(key: str, result: dict) -> None:
    """Mark ``key`` complete and store the result for future replays."""
    with _conn() as c:
        c.execute(
            "UPDATE launch_keys SET state='done', result_json=?, updated_at=? "
            "WHERE key=?",
            (json.dumps(result), _now(), key),
        )


def release(key: str) -> None:
    """Release a claim that did not result in a paid launch, so the user can
    retry. Only ever called on paths where nothing was charged."""
    with _conn() as c:
        c.execute("DELETE FROM launch_keys WHERE key=? AND state='running'", (key,))


def state_of(key: str) -> str | None:
    with _conn() as c:
        row = c.execute(
            "SELECT state FROM launch_keys WHERE key=?", (key,)
        ).fetchone()
    return row[0] if row else None


def stuck(limit: int = 20) -> list[dict]:
    """Launches left 'running' past the stale window — for operator review."""
    with _conn() as c:
        rows = c.execute(
            "SELECT key, created_at, updated_at FROM launch_keys "
            "WHERE state='running' ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"key": r[0], "created_at": r[1], "updated_at": r[2]}
        for r in rows
        if _age_seconds(r[2]) >= STALE_SECONDS
    ]
