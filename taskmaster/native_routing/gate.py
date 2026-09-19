# User intent: decide per call, with no memo and no projection read, whether a
# project's store is a ready native authority this runtime can serve; every legacy
# store (every real project today) must fall straight through to the legacy tools.
"""Project and runtime capability checks for native routing.

The project capability is the database's own authority marker: `meta.schema_version`
2 plus a ready `native` manifest. No runtime entry point writes those markers, so
only a test (or a later authorized cutover) can make a project native. The legacy
`Store` refuses such a database outright, which is why a native store has no
legacy fallback to route to: an unrouted tool refuses instead.

The runtime capability is the SQLite build (JSON, FTS5 porter, 3.44+), probed once
per process because it describes this interpreter, not the database.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import threading

from taskmaster.admission import UnsupportedStoreError

_RUNTIME_LOCK = threading.Lock()
_RUNTIME_ERROR: "str | None | bool" = False   # False: not probed yet


class NativeUnavailable(UnsupportedStoreError):
    """A native-authority store this runtime cannot serve; never a legacy fallback."""


def _runtime_error() -> str | None:
    global _RUNTIME_ERROR
    with _RUNTIME_LOCK:
        if _RUNTIME_ERROR is False:
            from taskmaster.native.db import probe_capabilities
            try:
                with closing(sqlite3.connect(":memory:", isolation_level=None)) as connection:
                    probe_capabilities(connection)
                _RUNTIME_ERROR = None
            except UnsupportedStoreError as exc:
                _RUNTIME_ERROR = str(exc)
        return _RUNTIME_ERROR


def database_path(backlog_path: Path) -> Path:
    """The store database this backlog resolves to, without reading any projection file."""
    from taskmaster import store
    return store.db_path(store.resolve_location(backlog_path).backlog_path)


def native_database(backlog_path: Path) -> Path | None:
    """The database path when this project is a native authority, else None.

    None is the legacy answer and must stay cheap: a missing file, a database
    without `meta`, or schema version 1 all return before any native import.
    A native marker that is not ready, or a runtime that cannot serve it, raises
    `NativeUnavailable` — handing that database to the legacy writer is never
    an option, and the legacy store would refuse it anyway.
    """
    from taskmaster import store
    try:
        path = database_path(backlog_path)
    except store.LegacyLayoutError:
        return None   # no store can live there; the legacy tool reports the layout
    if not path.is_file():
        return None
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, isolation_level=None, timeout=30)) as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None or row[0] != "2":
                return None
            reason = _runtime_error()
            if reason is not None:
                raise NativeUnavailable(f"native store at {path} cannot be served: {reason}")
            from taskmaster.native.db import assert_native
            connection.execute("BEGIN")
            try:
                assert_native(connection)
            except UnsupportedStoreError as exc:
                raise NativeUnavailable(str(exc)) from None
            finally:
                connection.rollback()
    except sqlite3.Error:
        # Not classifiable here (missing `meta`, a bootstrap in progress, damage):
        # the legacy store owns those cases and its own admission refuses schema 2.
        return None
    return path
