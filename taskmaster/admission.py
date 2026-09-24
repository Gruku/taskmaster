"""Dependency-free schema/protocol admission shared by server and system hooks.

Bridge clients may read legacy databases without these optional markers. A
migrator must retain meta and publish these markers before changing the schema.
Pre-bridge processes cannot honor this fence and must be stopped at cutover.
"""
from contextlib import contextmanager
import sqlite3
import threading

LEGACY_SCHEMA_VERSION = 1
CLIENT_PROTOCOL = 1
BRIDGE_CAPABILITIES = "schema-refusal,protocol-refusal,migration-refusal"


class UnsupportedStoreError(RuntimeError):
    """An incompatible authority is neither missing nor corrupt; never recover it."""


# The cutover command (N15) publishes `migration_state='migrating'` with a
# `migration_token`; only a thread that proves that token is admitted past the
# fence, so the command can reuse the admitted primitives (backfill) it fenced.
_OWNER = threading.local()


@contextmanager
def migration_owner(token: str):
    """Admit this thread past a `migrating` fence whose `migration_token` is `token`."""
    previous = getattr(_OWNER, "token", None)
    _OWNER.token = token
    try:
        yield
    finally:
        _OWNER.token = previous


def assert_compatible(connection: sqlite3.Connection) -> None:
    """Read-only admission; repeat after BEGIN IMMEDIATE before any write.

    No memo across operations: another process can publish a migration fence
    without changing SQLite's schema cookie or replacing the database file.
    """
    if not connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='meta'"
    ).fetchone():
        return  # Existing pre-schema/bootstrap handling owns this case.
    metadata = dict(connection.execute(
        "SELECT key,value FROM meta WHERE key IN "
        "('schema_version','minimum_client_protocol','migration_state','migration_token')"))
    for key, supported in (("schema_version", LEGACY_SCHEMA_VERSION),
                           ("minimum_client_protocol", CLIENT_PROTOCOL)):
        value = metadata.get(key, "0")
        try:
            version = int(value)
        except (ValueError, TypeError):
            raise UnsupportedStoreError(f"Unsupported Taskmaster {key}: {value!r}") from None
        if version < 0 or version > supported:
            raise UnsupportedStoreError(
                f"Unsupported Taskmaster {key}={version}; this client supports {supported}. "
                "Upgrade the client; the database has not been rebuilt.")
    state = metadata.get("migration_state", "ready")
    owner = getattr(_OWNER, "token", None)
    if state == "migrating" and owner is not None and metadata.get("migration_token") == owner:
        return
    if state != "ready":
        raise UnsupportedStoreError(
            f"Taskmaster migration state is {state!r}; access refused until migration completes.")
