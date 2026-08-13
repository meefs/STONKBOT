"""Shared SQLite access with restrictive on-disk permissions.

Four modules previously each hand-rolled this, with the same two bugs: the
connection was never closed (``with sqlite3.connect(...)`` commits but does not
close), and the database files were created with the process umask — typically
world-readable.

Everything STONKBOT persists is user data: encrypted wallet secrets, the
handle-to-wallet mapping, and the fee ledger. None of it should be readable by
other accounts on the host, so the data directory is created 0700 and every
database file is forced to 0600.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings

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


@contextmanager
def connect(name: str, schema: str | tuple[str, ...] = ()) -> Iterator[sqlite3.Connection]:
    """Open a database, apply ``schema``, commit on success, always close.

    ``schema`` statements must be idempotent (CREATE TABLE IF NOT EXISTS).
    """
    path = db_path(name)
    existed = path.exists()

    connection = sqlite3.connect(path, timeout=30.0)
    try:
        if not existed:
            # Created just now — lock it down before anything is written.
            _restrict(path, _FILE_MODE)

        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")

        statements = (schema,) if isinstance(schema, str) else schema
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
