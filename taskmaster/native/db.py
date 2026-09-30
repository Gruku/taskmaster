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


def manifest(connection: sqlite3.Connection, *, authorities=("legacy",), allow_prior_staging=False) -> dict:
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='native_manifest' AND type='table'").fetchone():
        raise UnsupportedStoreError("Native backfill has not been verified")
    result = dict(connection.execute("SELECT key,value FROM native_manifest WHERE key IN "
                                    "('schema_version','protocol','authority','state','store_id','event_high_water',"
                                    "'source_digest','local_state_imported')"))
    versions = {str(schema.VERSION)}
    if allow_prior_staging and result.get("authority") == "legacy":
        versions.update({"1", "2"})
    if result.get("schema_version") not in versions or result.get("protocol") != str(schema.PROTOCOL):
        raise UnsupportedStoreError("Unsupported native staging schema or protocol")
    if result.get("authority") not in authorities:
        raise UnsupportedStoreError("This staging client cannot access an activated native store")
    return result


def assert_native(connection):
    """Cold/warm native admission. Never fall back to a legacy writer."""
    metadata = dict(connection.execute("SELECT key,value FROM meta WHERE key IN "
                                       "('schema_version','minimum_client_protocol','migration_state','creation_token')"))
    if metadata.get("schema_version") != "2" or metadata.get("minimum_client_protocol") != str(schema.PROTOCOL):
        raise UnsupportedStoreError("Unsupported native authority schema or protocol")
    if metadata.get("migration_state", "ready") != "ready":
        raise UnsupportedStoreError("Native authority migration is not ready")
    state = manifest(connection, authorities=("native",))
    if state.get("state") != "ready" or state.get("local_state_imported") != "1":
        raise UnsupportedStoreError("Native authority or local-state import is not ready")
    if state.get("store_id") != metadata.get("creation_token"):
        raise UnsupportedStoreError("Native store identity does not match authority")
    return state


@contextmanager
def verified_snapshot(connection: sqlite3.Connection):
    """Gate all staging reads and checks inside the same SQLite read snapshot."""
    if connection.in_transaction:
        raise RuntimeError("verified_snapshot requires its own transaction")
    connection.execute("BEGIN")
    try:
        state = manifest(connection, authorities=("legacy", "native"))
        if state["authority"] == "native":
            state = assert_native(connection)
        else:
            assert_compatible(connection)
            if state.get("state") != "verified":
                raise UnsupportedStoreError("Native staging data is stale or unverified; repeat backfill")
        yield state
    finally:
        connection.rollback()
