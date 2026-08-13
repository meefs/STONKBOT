"""SQL dialect seam between local SQLite and hosted Postgres.

STONKBOT runs in two shapes. Locally and in tests it is a long-lived process
with SQLite files under ``data/``. On Vercel it is a cron-invoked function with
no disk at all, so state lives in Postgres.

Rather than fork every module, the domain code writes one flavour of SQL —
SQLite's, with ``?`` placeholders — and this module rewrites it for Postgres.
The translation is deliberately mechanical and narrow: placeholders, the
``INSERT OR IGNORE`` idiom, and the autoincrement primary key. Anything cleverer
(a general SQL parser, an ORM) would hide exactly the semantics that the
double-payment guard depends on.

Schema DDL is *not* translated automatically — each module declares its tables
through :func:`table`, because column types diverge more than statements do.
"""

from __future__ import annotations

import re

# ``?`` placeholders, but not inside a string literal. Postgres wants ``%s``.
_PLACEHOLDER = re.compile(r"\?(?=(?:[^']*'[^']*')*[^']*$)")

_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)


class Dialect:
    """Column types and statement rewriting for one backend."""

    name: str
    #: Autoincrementing primary key column.
    serial_pk: str
    #: Timestamps are stored as ISO-8601 text in both backends. Postgres could
    #: use timestamptz, but every read path already parses strings, and a type
    #: change would silently alter comparison semantics in the fee ledger.
    timestamp: str
    #: Arbitrary-length text.
    text: str
    #: Raw bytes — encrypted wallet secrets.
    blob: str

    def sql(self, statement: str) -> str:
        return statement


class SQLite(Dialect):
    name = "sqlite"
    serial_pk = "INTEGER PRIMARY KEY AUTOINCREMENT"
    timestamp = "TEXT"
    text = "TEXT"
    blob = "BLOB"


class Postgres(Dialect):
    name = "postgres"
    serial_pk = "BIGSERIAL PRIMARY KEY"
    timestamp = "TEXT"
    text = "TEXT"
    blob = "BYTEA"

    def sql(self, statement: str) -> str:
        # SQLite's INSERT OR IGNORE and Postgres's ON CONFLICT DO NOTHING agree
        # on what matters here: the insert is the claim, and rowcount reports
        # whether this caller won it. Detect before rewriting — the marker is
        # gone afterwards.
        ignoring = bool(_INSERT_OR_IGNORE.search(statement))
        statement = _INSERT_OR_IGNORE.sub("INSERT INTO", statement)
        statement = _PLACEHOLDER.sub("%s", statement)
        if ignoring:
            statement = f"{statement.rstrip().rstrip(';')} ON CONFLICT DO NOTHING"
        return statement


def table(dialect: Dialect, name: str, columns: str) -> str:
    """A ``CREATE TABLE IF NOT EXISTS`` with dialect types substituted.

    ``columns`` uses the placeholders ``{serial_pk}``, ``{timestamp}``,
    ``{text}`` and ``{blob}``.
    """
    body = columns.format(
        serial_pk=dialect.serial_pk,
        timestamp=dialect.timestamp,
        text=dialect.text,
        blob=dialect.blob,
    )
    return f"CREATE TABLE IF NOT EXISTS {name} ({body})"
