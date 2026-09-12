"""Explicit snapshot admission for verified staging data; no bootstrap writes."""
from contextlib import contextmanager
import sqlite3

from taskmaster.admission import UnsupportedStoreError, assert_compatible
from . import schema


def probe_capabilities(connection: sqlite3.Connection) -> dict:
    """Probe the actual connection, cleaning up even on unsupported builds."""
    if sqlite3.sqlite_version_info < (3, 44, 0):
        raise UnsupportedStoreError("SQLite 3.44 or newer is required for reliable FTS integrity diagnostics")
    connection.execute("SAVEPOINT native_capability_probe")
    try:
        if connection.execute("SELECT json_valid('{\"v\":null}'),json_extract('{\"v\":2}','$.v')").fetchone()[:] != (1, 2):
            raise UnsupportedStoreError("SQLite JSON support is required")
        connection.execute("CREATE VIRTUAL TABLE temp.native_fts_probe USING fts5(body,tokenize='porter unicode61')")
        connection.execute("INSERT INTO temp.native_fts_probe VALUES('running')")
        if not connection.execute("SELECT rowid FROM temp.native_fts_probe WHERE native_fts_probe MATCH 'run'").fetchone():
            raise UnsupportedStoreError("SQLite FTS5 porter tokenizer is required")
        return {"json": True, "fts5": True, "sqlite_version": sqlite3.sqlite_version}
    except sqlite3.DatabaseError as exc:
        raise UnsupportedStoreError(f"SQLite native capability probe failed: {exc}") from exc
    finally:
        connection.execute("ROLLBACK TO native_capability_probe")
        connection.execute("RELEASE native_capability_probe")


def manifest(connection: sqlite3.Connection) -> dict:
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='native_manifest' AND type='table'").fetchone():
        raise UnsupportedStoreError("Native backfill has not been verified")
    result = dict(connection.execute("SELECT key,value FROM native_manifest"))
    if result.get("schema_version") != str(schema.VERSION) or result.get("protocol") != str(schema.PROTOCOL):
        raise UnsupportedStoreError("Unsupported native staging schema or protocol")
    if result.get("authority") != "legacy":
        raise UnsupportedStoreError("This staging client cannot access an activated native store")
    return result


@contextmanager
def verified_snapshot(connection: sqlite3.Connection):
    """Gate all staging reads and checks inside the same SQLite read snapshot."""
    if connection.in_transaction:
        raise RuntimeError("verified_snapshot requires its own transaction")
    connection.execute("BEGIN")
    try:
        assert_compatible(connection)
        state = manifest(connection)
        if state.get("state") != "verified":
            raise UnsupportedStoreError("Native staging data is stale or unverified; repeat backfill")
        yield state
    finally:
        connection.rollback()
