"""Dependency-free schema/protocol admission shared by server and system hooks.

Bridge clients may read legacy databases without these optional markers. A
migrator must retain meta and publish these markers before changing the schema.
Pre-bridge processes cannot honor this fence and must be stopped at cutover.
"""
import sqlite3

LEGACY_SCHEMA_VERSION = 1
CLIENT_PROTOCOL = 1
BRIDGE_CAPABILITIES = "schema-refusal,protocol-refusal,migration-refusal"


class UnsupportedStoreError(RuntimeError):
    """An incompatible authority is neither missing nor corrupt; never recover it."""


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
        "('schema_version','minimum_client_protocol','migration_state')"))
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
    if state != "ready":
        raise UnsupportedStoreError(
            f"Taskmaster migration state is {state!r}; access refused until migration completes.")
