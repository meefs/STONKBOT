"""The SQLite→Postgres rewrite.

These are the only place the two backends can silently diverge, and one of the
statements they rewrite is the double-payment guard's claim. Everything here is
pure string work, so it runs without a Postgres.
"""

from __future__ import annotations

from stonkbot.dialect import Postgres, SQLite, table

pg = Postgres()


def test_sqlite_passes_statements_through_untouched():
    statement = "SELECT * FROM kv WHERE key=? AND value=?"

    assert SQLite().sql(statement) == statement


def test_placeholders_become_pyformat():
    assert (
        pg.sql("SELECT * FROM kv WHERE key=? AND value=?")
        == "SELECT * FROM kv WHERE key=%s AND value=%s"
    )


def test_question_marks_inside_string_literals_survive():
    """A literal '?' is data, not a placeholder."""
    assert pg.sql("SELECT * FROM t WHERE label='what?'") == (
        "SELECT * FROM t WHERE label='what?'"
    )


def test_insert_or_ignore_becomes_on_conflict_do_nothing():
    """The idempotency claim depends on this rewrite preserving rowcount
    semantics: 1 when this caller won the insert, 0 when it lost."""
    out = pg.sql("INSERT OR IGNORE INTO launch_keys (key) VALUES (?)")

    assert out == "INSERT INTO launch_keys (key) VALUES (%s) ON CONFLICT DO NOTHING"


def test_plain_insert_gets_no_conflict_clause():
    """A normal INSERT must still raise on a duplicate, not swallow it."""
    out = pg.sql("INSERT INTO fee_events (id) VALUES (?)")

    assert "ON CONFLICT" not in out


def test_insert_or_ignore_is_case_insensitive():
    assert "ON CONFLICT DO NOTHING" in pg.sql("insert or ignore into t (a) values (?)")


def test_trailing_semicolon_does_not_strand_the_conflict_clause():
    out = pg.sql("INSERT OR IGNORE INTO t (a) VALUES (?);")

    assert out.endswith("ON CONFLICT DO NOTHING")


def test_table_substitutes_dialect_types():
    sqlite_ddl = table(SQLite(), "t", "id {serial_pk}, name {text}, blob {blob}")
    pg_ddl = table(pg, "t", "id {serial_pk}, name {text}, blob {blob}")

    assert "INTEGER PRIMARY KEY AUTOINCREMENT" in sqlite_ddl
    assert "BLOB" in sqlite_ddl
    assert "BIGSERIAL PRIMARY KEY" in pg_ddl
    assert "BYTEA" in pg_ddl
    assert pg_ddl.startswith("CREATE TABLE IF NOT EXISTS t (")


def test_both_backends_store_timestamps_as_text():
    """The fee ledger compares ISO strings; a timestamptz column would change
    comparison semantics under it."""
    assert SQLite().timestamp == pg.timestamp == "TEXT"
