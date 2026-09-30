# User intent: let the store-reading hooks (edit resurfacing, merge gate, merge
# recorder) answer from live native rows on a native-authority store, with the same
# stdlib-only, read-only, never-bootstrap discipline they keep on a legacy store.
"""Native read helpers for hooks, run inside the hook's own open read transaction.

Standard library plus `taskmaster.admission` and `taskmaster.native` only: hooks run
under the system interpreter, so nothing here may import yaml, the server or the
legacy store. The frozen legacy `entities`/`changes` tables in an activated database
are never read; `entity_paths` is maintained by the native core, and neighbours come
from the canonical neighbourhood rather than the `related` table.
"""
from __future__ import annotations

import sqlite3

from taskmaster.admission import assert_compatible

PATH_MATCH_SQL = (
    "SELECT e.kind, e.public_id, json_extract(e.status_json,'$'), e.archived, p.match_kind, p.path, p.source"
    " FROM entity_paths p JOIN entity_core e ON e.kind = p.kind AND e.public_id = p.id"
    " WHERE e.deleted = 0"
    "   AND ((p.match_kind='exact' AND p.path=?) OR p.match_kind='glob')"
)

LIVE_STATUS_SQL = (
    "SELECT json_extract(status_json,'$') FROM entity_core"
    " WHERE kind=? AND public_id=? AND deleted=0 AND archived=0"
)

_TASK_BY_BRANCH_SQL = (
    "SELECT c.public_id FROM task_operational t JOIN entity_core c ON c.entity_key = t.entity_key"
    " WHERE c.kind='task' AND c.deleted=0 AND c.archived=0 AND json_extract(t.branch_json,'$')=?"
    " ORDER BY c.public_id"
)


def is_native(connection: sqlite3.Connection) -> bool:
    """Whether this database carries the native authority marker (no admission)."""
    if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='meta'").fetchone():
        return False
    row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return row is not None and row[0] == "2"


def admit(connection: sqlite3.Connection):
    """The native manifest for a ready native store, else None after legacy admission.

    Raises `UnsupportedStoreError` for anything neither client may read, exactly as
    the legacy admission does, so each hook's quiet/fail-open handling is unchanged.
    """
    if is_native(connection):
        from taskmaster.native.db import assert_native
        return assert_native(connection)
    assert_compatible(connection)
    return None


def revision(connection: sqlite3.Connection) -> int:
    """The dedupe revision: the event high water plus the count of row-changing graph
    repairs. A repair rewrites `entity_paths` without an event; both terms only grow,
    so any commit or repair yields a larger value than every earlier one."""
    row = connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()
    repairs = connection.execute("SELECT value FROM native_manifest WHERE key='graph_repairs'").fetchone()
    return (int(row[0]) if row and row[0] is not None else 0) + (int(repairs[0]) if repairs else 0)


def neighbours(connection: sqlite3.Connection, kind: str, ident: str) -> set:
    """Distinct `(kind, id)` neighbours, the same set the `related` rows name."""
    from taskmaster.native.neighbourhood import neighbours as found
    return {(k, i) for k, i, _ in found(connection, kind, ident)}


def _snapshot(connection, identity):
    from taskmaster.native.queries import Snapshot
    return Snapshot(connection, identity)


def task_for_branch(connection: sqlite3.Connection, identity: dict, branch: str):
    """`(id, fields)` of the first live task on `branch`, or `(None, None)`."""
    row = connection.execute(_TASK_BY_BRANCH_SQL, (branch,)).fetchone()
    if row is None:
        return None, None
    return row[0], _snapshot(connection, identity).get("task", row[0])["fields"]


def project_fields(connection: sqlite3.Connection, identity: dict) -> dict | None:
    row = connection.execute(
        "SELECT public_id FROM entity_core WHERE kind='project' AND deleted=0 AND archived=0"
        " ORDER BY public_id LIMIT 1").fetchone()
    if row is None:
        return None
    return _snapshot(connection, identity).get("project", row[0])["fields"]
