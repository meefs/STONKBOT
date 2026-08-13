"""Shared database access, over SQLite on disk or Postgres over the network.

Four modules previously each hand-rolled this, with the same two bugs: the
connection was never closed (``with sqlite3.connect(...)`` commits but does not
close), and the database files were created with the process umask — typically
world-readable.

Everything STONKBOT persists is user data: encrypted wallet secrets, the
handle-to-wallet mapping, and the fee ledger. None of it should be readable by
other accounts on the host, so the data directory is created 0700 and every
database file is forced to 0600.

**Backend selection.** ``DATABASE_URL`` set → Postgres, for the serverless
deployment where there is no durable disk. Unset → SQLite under ``data/``, for
local runs and the test suite. Callers write SQLite-flavoured SQL either way;
:mod:`stonkbot.dialect` rewrites it. ``name`` identifies a SQLite file and is
ignored under Postgres, where every table shares one database — the table names
across modules are already distinct.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import get_settings
from .dialect import Dialect, Postgres, SQLite

# rwx for the owner only.
_DIR_MODE = 0o700
# rw for the owner only.
_FILE_MODE = 0o600

# SQLite writes alongside the main file in WAL mode; all of them need locking
# down, not just the database itself.
_SIDECARS = ("", "-wal", "-shm", "-journal")


def data_dir() -> Path:
    path = Path(get_settings().data_dir)
    path.mkdir(parents=True, exist_ok=True)
    _restrict(path, _DIR_MODE)
    return path


def _restrict(path: Path, mode: int) -> None:
    """Tighten permissions, ignoring platforms that don't support them."""
    try:
        current = stat.S_IMODE(path.stat().st_mode)
        if current != mode:
            os.chmod(path, mode)
    except (OSError, NotImplementedError):
        # Windows and some mounted volumes don't honour POSIX modes. Failing to
        # tighten permissions must not stop the bot from running.
        pass


def db_path(name: str) -> Path:
    return data_dir() / name


def database_url() -> str | None:
    """The Postgres URL, or None when this process should use SQLite."""
    return get_settings().database_url or None


def dialect() -> Dialect:
    return Postgres() if database_url() else SQLite()


class _PgConnection:
    """psycopg connection wearing the sqlite3 API the domain modules expect.

    Two gaps to bridge: ``execute`` returns a cursor on both, but psycopg needs
    ``%s`` placeholders, and Postgres has no ``lastrowid`` — inserts that need
    the new id go through :meth:`insert_returning_id` instead.
    """

    def __init__(self, raw: Any, dialect: Dialect) -> None:
        self._raw = raw
        self._dialect = dialect

    def execute(self, statement: str, params: tuple = ()) -> Any:
        cursor = self._raw.cursor()
        cursor.execute(self._dialect.sql(statement), params)
        return cursor

    def insert_returning_id(self, statement: str, params: tuple = ()) -> int:
        cursor = self.execute(f"{statement.rstrip().rstrip(';')} RETURNING id", params)
        row = cursor.fetchone()
        return int(row[0])

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


class _SqliteConnection:
    """The sqlite3 connection, plus the one method Postgres forced us to add."""

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw

    def __getattr__(self, item: str) -> Any:
        return getattr(self._raw, item)

    def execute(self, statement: str, params: tuple = ()) -> Any:
        return self._raw.execute(statement, params)

    def insert_returning_id(self, statement: str, params: tuple = ()) -> int:
        return int(self._raw.execute(statement, params).lastrowid)


@contextmanager
def _connect_postgres(schema: tuple[str, ...]) -> Iterator[_PgConnection]:
    try:
        import psycopg
    except ModuleNotFoundError as e:  # pragma: no cover - deployment-only path
        raise RuntimeError(
            "DATABASE_URL is set but psycopg is not installed — "
            "add 'psycopg[binary]' to requirements"
        ) from e

    raw = psycopg.connect(database_url(), autocommit=False)
    connection = _PgConnection(raw, Postgres())
    try:
        for statement in schema:
            connection.execute(statement)
        connection.commit()
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def connect(name: str, schema: str | tuple[str, ...] = ()) -> Iterator[Any]:
    """Open a database, apply ``schema``, commit on success, always close.

    ``schema`` statements must be idempotent (CREATE TABLE IF NOT EXISTS).
    """
    statements = (schema,) if isinstance(schema, str) else tuple(schema)

    if database_url():
        with _connect_postgres(statements) as connection:
            yield connection
        return

    path = db_path(name)
    existed = path.exists()

    raw = sqlite3.connect(path, timeout=30.0)
    connection = _SqliteConnection(raw)
    try:
        if not existed:
            # Created just now — lock it down before anything is written.
            _restrict(path, _FILE_MODE)

        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")

        for statement in statements:
            connection.execute(statement)

        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        # WAL sidecars appear after the first write; re-apply each time so a
        # freshly created -wal/-shm is never left world-readable.
        for suffix in _SIDECARS:
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                _restrict(sidecar, _FILE_MODE)


def permissions_report() -> list[dict]:
    """Owner-only check for `cli doctor`: flags anything group/world readable."""
    directory = Path(get_settings().data_dir)
    if not directory.exists():
        return []

    report: list[dict] = []
    targets = [directory, *sorted(directory.glob("*.db*"))]
    for target in targets:
        try:
            mode = stat.S_IMODE(target.stat().st_mode)
        except OSError:
            continue
        expected = _DIR_MODE if target.is_dir() else _FILE_MODE
        report.append(
            {
                "path": target.name or str(target),
                "mode": oct(mode),
                "ok": not (mode & 0o077),
                "expected": oct(expected),
            }
        )
    return report
