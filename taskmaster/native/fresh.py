# User intent: a project created by `backlog_init` starts on the native store, so the agent gets
# its speed from the first call; a fresh project has nothing to carry over, so no cutover is run.
"""Born-native initialization: make a just-scaffolded project a ready native authority.

`backlog_init` writes the projection files first, exactly as a legacy init does. This module
then turns that empty project into a native one with the same production pieces a cutover
commits with, minus everything that exists to protect existing data (fence, quiesce, backup,
drift absorption, carry-over oracle): there is no data yet.

    1. The legacy store opens once and adopts the scaffolded `backlog.yaml` (schema-1
       database, creation token, ID-reservation sidecar, `local/.gitignore`).
    2. `migrate.backfill` stages it into the native tables and verifies the staging.
    3. `cutover.activate` (the unfenced path) flips authority to native in one transaction.

Each step commits atomically. If anything fails, the project is left as a working legacy
project, which the operator cutover can still convert later; the caller reports that.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3


class FreshStoreRefused(RuntimeError):
    """The database is not the empty store this init just created; nothing was changed."""


def runtime_refusal() -> str | None:
    """Why this interpreter cannot serve a native store (its SQLite build), or None."""
    from taskmaster.native_routing import gate
    return gate._runtime_error()


def _assert_fresh(connection) -> None:
    """Only a store holding nothing but the scaffolded backlog row may be activated here."""
    meta = dict(connection.execute("SELECT key,value FROM meta"))
    if meta.get("schema_version") != "1" or meta.get("migration_state", "ready") != "ready":
        raise FreshStoreRefused(f"store is not a fresh legacy store (schema_version={meta.get('schema_version')!r})")
    kinds = [row[0] for row in connection.execute("SELECT DISTINCT kind FROM entities WHERE deleted=0")]
    if kinds not in ([], ["backlog"]):
        raise FreshStoreRefused(f"store already holds entities ({', '.join(sorted(kinds))})")


def activate_fresh(root: Path, backlog_path: Path) -> dict:
    """Create the store for a just-scaffolded project and activate it as native.

    `backlog_path` is the scaffolded `<root>/.taskmaster/backlog.yaml`. The caller has checked
    that no store existed before it scaffolded; the store may have been opened since (a cache
    write opens it), so freshness is judged by the database's content (`_assert_fresh`).
    Raises on any failure; the project then stays legacy (its store serves the legacy tools).
    """
    from taskmaster import backlog_server as server
    from taskmaster import store as legacy
    from . import cutover
    from .migrate import backfill

    root = Path(root)
    database = cutover.database_path(root)
    # Step 1: the legacy store's own bootstrap builds the schema-1 database from the files.
    instance = server._store_for(backlog_path)
    try:
        with instance.transaction(tool="backlog_init"):
            pass
    finally:
        legacy.close_thread_connection()
    # The bootstrap registered this process as a legacy session; a native store never
    # serves that session, so its rows would only read as live sessions in status reports.
    cutover._restore_flush_sessions(database, instance.session, {})
    ownership = cutover._ownership(root)
    try:
        with closing(sqlite3.connect(database, isolation_level=None, timeout=30)) as connection:
            connection.execute("PRAGMA busy_timeout=30000")
            _assert_fresh(connection)
            staged = backfill(connection)
            activated = cutover.activate(connection, root)
    finally:
        ownership.close()
    activated.pop("seeded_bases", None)
    return {"database": str(database), "backfill": staged, "activate": activated}
