# User intent: on a team server a teammate's branch, worktree and merge records describe their own checkout, so they
# live per person and per clone in a lazily created table instead of overwriting the one shared task field
# (team-server-011 D3-D5). Callers own the transaction; a missing table reads as empty and is never created by a read.
"""Per-person, per-clone machine-local task fields: `branch`, `worktree`, and `merge:<rung>` records."""
from __future__ import annotations

import json
import re
import time

CLONE_RE = re.compile(r"c-[0-9a-f]{16}")
FIELDS = ("branch", "worktree")
OPERATIONS = {"clone.restore"}
MAX_VALUE_BYTES = 4 * 1024
MAX_CLONES_PER_HANDLE = 64
MAX_ROWS_PER_CLONE = 1000
DDL = (
    "CREATE TABLE IF NOT EXISTS clone_fields("
    "handle TEXT NOT NULL, clone TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, field TEXT NOT NULL, "
    "value_json TEXT NOT NULL CHECK(json_valid(value_json)), updated_at TEXT NOT NULL, updated_seq INTEGER NOT NULL, "
    "PRIMARY KEY(handle, clone, kind, id, field))",
    "CREATE INDEX IF NOT EXISTS ix_clone_fields_branch ON clone_fields(field, json_extract(value_json,'$'), kind, id) "
    "WHERE field = 'branch'",
)


def valid_clone(value) -> bool:
    return value == "" or (isinstance(value, str) and CLONE_RE.fullmatch(value) is not None)


def ensure(connection) -> None:
    for statement in DDL:
        connection.execute(statement)


def exists(connection) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='clone_fields'").fetchone() \
        is not None


def put(connection, handle: str, clone: str, kind: str, ident: str, field: str, value, seq: int) -> str:
    """Store `value` (None deletes) for `(handle, clone)`; returns the clone the row landed under.

    A write for a 65th clone id of one handle goes to that person's default row (`clone = ""`) instead: nothing is
    refused or evicted, because evicting a branch row would quietly ungate that branch (D3)."""
    ensure(connection)
    if value is None:
        connection.execute("DELETE FROM clone_fields WHERE handle=? AND clone=? AND kind=? AND id=? AND field=?",
                           (handle, clone, kind, ident, field))
        return clone
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
        raise ValueError(f"a per-clone value is at most {MAX_VALUE_BYTES // 1024} KiB")
    if clone and connection.execute("SELECT 1 FROM clone_fields WHERE handle=? AND clone=? LIMIT 1",
                                    (handle, clone)).fetchone() is None:
        known = connection.execute("SELECT COUNT(DISTINCT clone) FROM clone_fields WHERE handle=? AND clone<>''",
                                   (handle,)).fetchone()[0]
        if known >= MAX_CLONES_PER_HANDLE:
            clone = ""
    present = connection.execute("SELECT 1 FROM clone_fields WHERE handle=? AND clone=? AND kind=? AND id=? AND field=?",
                                 (handle, clone, kind, ident, field)).fetchone()
    if present is None and connection.execute("SELECT COUNT(*) FROM clone_fields WHERE handle=? AND clone=?",
                                              (handle, clone)).fetchone()[0] >= MAX_ROWS_PER_CLONE:
        raise ValueError(f"a clone holds at most {MAX_ROWS_PER_CLONE} per-clone values")
    connection.execute(
        "INSERT INTO clone_fields(handle,clone,kind,id,field,value_json,updated_at,updated_seq) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(handle,clone,kind,id,field) DO UPDATE SET value_json=excluded.value_json,"
        "updated_at=excluded.updated_at,updated_seq=excluded.updated_seq",
        (handle, clone, kind, ident, field, encoded, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), seq))
    return clone


def rows_for(connection, kind: str, ident: str) -> list[dict]:
    if not exists(connection):
        return []
    return [{"handle": h, "clone": c, "field": f, "value": json.loads(v)} for h, c, f, v in connection.execute(
        "SELECT handle,clone,field,value_json FROM clone_fields WHERE kind=? AND id=? ORDER BY handle,clone,field",
        (kind, ident))]


def own(connection, handle: str, clone: str, kind: str, ident: str) -> dict:
    """`{field: value}` for the caller: the default row, overridden by the clone's row."""
    if not exists(connection):
        return {}
    found: dict = {}
    for wanted in ([""] if not clone else ["", clone]):
        for field, value in connection.execute(
                "SELECT field,value_json FROM clone_fields WHERE handle=? AND clone=? AND kind=? AND id=?",
                (handle, wanted, kind, ident)):
            found[field] = json.loads(value)
    return found


def branch_matches(connection, branch: str) -> list[tuple[str, str, str]]:
    """`(handle, clone, id)` of every task row mapping `branch`, from any person."""
    if not exists(connection):
        return []
    return [tuple(row) for row in connection.execute(
        "SELECT handle,clone,id FROM clone_fields WHERE field='branch' AND json_extract(value_json,'$')=? "
        "AND kind='task' ORDER BY id,handle,clone", (branch,))]


def branch_mark(connection) -> str:
    """A fingerprint of the branch rows the export covers, values included, so the exporter notices a write that moved
    no event: a fully diverted branch update leaves the shared store alone (review minor 2)."""
    import hashlib
    if not exists(connection):
        return "0:"
    digest, count = hashlib.sha256(), 0
    for row in connection.execute(
            "SELECT f.handle,f.clone,f.id,f.value_json FROM clone_fields f JOIN entity_core e ON e.kind=f.kind "
            "AND e.public_id=f.id AND e.deleted=0 WHERE f.field='branch' AND f.kind='task' "
            "ORDER BY f.handle,f.clone,f.id"):
        digest.update(json.dumps(row).encode("utf-8"))
        count += 1
    return f"{count}:{digest.hexdigest()}"


def branch_rows(connection) -> list[dict]:
    """Every task `branch` row of a live task, sorted, for the state export (D10)."""
    if not exists(connection):
        return []
    rows = connection.execute(
        "SELECT f.handle,f.clone,f.id,f.value_json FROM clone_fields f JOIN entity_core e "
        "ON e.kind=f.kind AND e.public_id=f.id AND e.deleted=0 WHERE f.field='branch' AND f.kind='task' "
        "ORDER BY f.handle,f.clone,f.id").fetchall()
    return [{"handle": h, "clone": c, "kind": "task", "id": i, "branch": json.loads(v)} for h, c, i, v in rows]


# ── diverting a member's write (D4) ────────────────────────────────────────────────────────────


def divert(transaction, ident: str, before: dict, after: dict) -> dict:
    """Move a member's change to `branch`/`worktree` into their clone rows and leave the shared fields as they were.

    The operator, host-local and solo callers (no requester, or one that may force anything) keep shared writes."""
    requester = getattr(transaction, "requester", None)
    if requester is None or requester.may_force_any:
        return after
    clone = str(transaction.request.get("clone") or "")
    connection = transaction.connection
    # "Changed" is against what the caller sees: their own row over the shared field (review I1).
    seen = own(connection, requester.handle, clone, "task", ident)
    # A field the command named counts as changed against `seen`; any other field only when it moved off the shared value
    # (a batch's later command leaves `branch` at the shared value without meaning to write it).
    named = getattr(transaction, "named_field", None)
    changed = [f for f in FIELDS if (before.get(f) or "") != (after.get(f) or "")
               or (f == named and (seen.get(f, before.get(f)) or "") != (after.get(f) or ""))]
    if not changed:
        return after
    for field in changed:
        value = after.get(field) or None
        if (value or "") == (before.get(field) or ""):
            # Back to the shared value (or empty over an empty one): the caller's rows go, the shared field stays.
            value = None
            for stale in {clone, ""}:
                put(connection, requester.handle, stale, "task", ident, field, None, transaction.seq)
        used = put(connection, requester.handle, clone, "task", ident, field, value, transaction.seq)
        transaction.clone_writes.append({"id": ident, "field": field, "value": value or "", "clone": used,
                                         "requested_clone": clone})
        if field in before:
            after[field] = before[field]
        else:
            after.pop(field, None)
    rest = lambda doc: {k: v for k, v in doc.items() if k != "last_referenced"}  # noqa: E731
    if rest(after) == rest(before):
        # Fully diverted: the shared document, its revision and the event log stay as they were (D3).
        if "last_referenced" in before:
            after["last_referenced"] = before["last_referenced"]
        else:
            after.pop("last_referenced", None)
    return after


def record_merge(transaction, ident: str, rung: str, sha: str, merged_at: str) -> None:
    """Provenance: which clone recorded which commit (D7). Never fails the shared stamp."""
    requester = getattr(transaction, "requester", None)
    if requester is None or requester.may_force_any:
        return
    clone = str(transaction.request.get("clone") or "")
    try:
        put(transaction.connection, requester.handle, clone, "task", ident, f"merge:{rung}",
            {"merge_commit": sha, "merged_at": merged_at}, transaction.seq)
    except ValueError:
        import logging
        logging.getLogger("taskmaster.native").warning(
            "per-clone merge record for %s %s skipped: a bound was reached", ident, rung)


# ── restoring a backup's branch rows (D10) ─────────────────────────────────────────────────────


def validate(operation, arguments):
    """`clone.restore` takes exactly one `branch` row of a task, each part checked by the rule that wrote it."""
    from taskmaster.team_identity import HANDLE_RE
    from .contracts import _identifier
    if not isinstance(arguments, dict) or set(arguments) != {"handle", "clone", "kind", "id", "field", "value"}:
        raise ValueError("clone.restore takes handle, clone, kind, id, field and value")
    if not isinstance(arguments["handle"], str) or not HANDLE_RE.fullmatch(arguments["handle"]):
        raise ValueError("clone.restore handle is not a valid handle")
    if not valid_clone(arguments["clone"]):
        raise ValueError("clone.restore clone is not a clone id")
    if arguments["kind"] != "task" or arguments["field"] != "branch":
        raise ValueError("clone.restore restores task branch rows only")
    _identifier(arguments["id"], "id")
    from taskmaster.native_routing.hook_reads import ref_problem
    problem = ref_problem(arguments["value"])
    if problem is not None or not arguments["value"]:
        raise ValueError(f"clone.restore branch is not a branch name ({problem or 'empty'})")


def apply(transaction, operation, arguments):
    """Host-operator only: a served requester never reaches a restore (the allowlist also refuses it)."""
    if getattr(transaction, "requester", None) is not None:
        raise ValueError("clone.restore is a host operator action")
    ident = arguments["id"]
    if transaction.snapshot.get("task", ident, fields=["title"]) is None:
        raise ValueError(f"no live task {ident}")
    put(transaction.connection, arguments["handle"], arguments["clone"], "task", ident, "branch",
        arguments["value"], transaction.seq)


# ── reading: the caller's overlay (D5) ─────────────────────────────────────────────────────────


def overlay(connection, doc: dict, handle: str, clone: str, ident: str | None = None) -> dict:
    """`doc` with `branch` and `worktree` replaced by the caller's clone row, else their default row."""
    mine = own(connection, handle, clone, "task", ident or doc.get("id") or "")
    if not any(f in mine for f in FIELDS):
        return doc
    shown = dict(doc)
    for field in FIELDS:
        if field in mine:
            shown[field] = mine[field]
    return shown
