# User intent: let the store-reading hooks (edit resurfacing, merge gate, merge
# recorder) answer from live native rows on a native-authority store, with the same
# stdlib-only, read-only, never-bootstrap discipline they keep on a legacy store.
# Since team-server-007 the hooks' read halves live here as views, so the team
# server answers a teammate's hook with exactly the code a solo hook runs.
"""Native read helpers and the hook read views, run inside the caller's open read transaction.

Standard library plus `taskmaster.admission` and `taskmaster.native` only: hooks run
under the system interpreter, so nothing here may import yaml, the server or the
legacy store. The frozen legacy `entities`/`changes` tables in an activated database
are never read; `entity_paths` is maintained by the native core, and neighbours come
from the canonical neighbourhood rather than the `related` table.

Each `*_view` returns the `data` dict of the team server's `/read/v1/<view>` reply
(design 2026-11-01-team-server-007 §5), so its keys are a wire contract: add, never
rename or remove.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

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

# The legacy store's spellings of the same reads.
LEGACY_MATCH_SQL = (
    "SELECT e.kind, e.id, e.status, e.archived, p.match_kind, p.path, p.source"
    " FROM entity_paths p JOIN entities e ON e.kind = p.kind AND e.id = p.id"
    " WHERE e.deleted = 0"
    "   AND ((p.match_kind='exact' AND p.path=?) OR p.match_kind='glob')"
)
LEGACY_RELATED_SQL = (
    "SELECT b_kind, b_id FROM related WHERE a_kind=? AND a_id=?"
    " UNION SELECT a_kind, a_id FROM related WHERE b_kind=? AND b_id=?"
)
LEGACY_LIVE_STATUS_SQL = "SELECT status FROM entities WHERE kind=? AND id=? AND deleted=0 AND archived=0"
LEGACY_TASK_BY_BRANCH_SQL = (
    "SELECT id, doc FROM entities"
    " WHERE kind='task' AND deleted=0 AND archived=0"
    "   AND json_extract(doc,'$.branch')=?"
    " ORDER BY id"
)

# Kinds that can be named in an edit's line, in output order.
LISTED_KINDS = ("bug", "issue", "task", "handover")
KIND_ORDER = {kind: i for i, kind in enumerate(LISTED_KINDS)}
OPEN_STATUS = {
    "task": {"todo", "in-progress", "blocked", "in-review"},
    "bug": {"open", "adopted"},
    "issue": {"open", "investigating"},
    "handover": {"open"},
}
# `anchors` and `location` are deliberate claims that an entity owns a path.
# A prose mention is not, so it is counted and never named.
STRUCTURAL_SOURCES = {"anchors", "location"}
MAX_CANDIDATES = 10


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


def connect_ro(db_file, busy_timeout: float) -> sqlite3.Connection:
    """A connection that can read the store and can never write to it, inside an open read transaction.

    `mode=ro` is deliberately not used: a read-only open cannot create the `-shm` a WAL
    database needs, and SQLite defers that failure to the first statement rather than to
    the open (design spec 3.1). `mode=rw` takes its write guard from `query_only` and still
    refuses to create a missing store. The probe statement moves an unusable database's
    failure here, where the caller can log it or fall back.
    """
    uri = Path(db_file).resolve().as_uri() + "?mode=rw"
    con = sqlite3.connect(uri, uri=True, timeout=busy_timeout)
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        admit(con)
    except BaseException:
        con.close()
        raise
    return con


def revision(connection: sqlite3.Connection) -> int:
    """The dedupe revision: the event high water plus the count of row-changing graph
    repairs. A repair rewrites `entity_paths` without an event; both terms only grow,
    so any commit or repair yields a larger value than every earlier one."""
    row = connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()
    repairs = connection.execute("SELECT value FROM native_manifest WHERE key='graph_repairs'").fetchone()
    return (int(row[0]) if row and row[0] is not None else 0) + (int(repairs[0]) if repairs else 0)


def store_revision(connection: sqlite3.Connection) -> int:
    """`revision` on a native store; `MAX(changes.seq)` on a legacy one."""
    if is_native(connection):
        return revision(connection)
    row = connection.execute("SELECT MAX(seq) FROM changes").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


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


# ── Paths (edit resurfacing) ────────────────────────────────────


class ViewTimeout(Exception):
    """A view ran past the deadline its caller set (the team server's read endpoint)."""


_LIT, _ONE, _STAR, _DSTAR = range(4)
MAX_GLOB_CHARS = 1024          # a longer glob never matches (the read endpoint's path cap; no real anchor is near it)
_WORK_EVERY = 4096             # state visits between deadline checks inside one match


def _glob_tokens(pattern: str) -> list:
    """`**` crosses `/`; `*` and `?` do not. Read left to right as the old regex did (`***` is `**` then `*`)."""
    tokens: list = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            tokens.append((_DSTAR, ""))
            i += 2
            continue
        ch = pattern[i]
        tokens.append((_STAR, "") if ch == "*" else (_ONE, "") if ch == "?" else (_LIT, ch))
        i += 1
    return tokens


def glob_match(pattern: str, rel: str, deadline=None) -> bool:
    """Whether the whole of `rel` matches the path glob `pattern`.

    A set-of-positions walk over the pattern's tokens: time is at most len(pattern) x len(rel), whatever the stars
    (a backtracking regex took seconds for `*a*a*a*a*a*a*b` on 70 characters; team-server-007 review I1). It keeps the
    old regex's results exactly, including that `**` (regex `.`) does not match a newline while `*` and `?` do.
    `deadline`: a callable that is truthy once time is up (query_guard.Deadline); then ViewTimeout is raised.
    """
    if len(pattern) > MAX_GLOB_CHARS:
        return False
    tokens = _glob_tokens(pattern)
    end = len(tokens)
    work = 0

    def tick(count):
        nonlocal work
        work += count
        if deadline is not None and work >= _WORK_EVERY:
            work = 0
            if deadline():
                raise ViewTimeout("path match ran past its deadline")

    def closure(positions):
        out = set()
        stack = list(positions)
        while stack:
            tick(1)
            i = stack.pop()
            if i in out:
                continue
            out.add(i)
            if i < end and tokens[i][0] in (_STAR, _DSTAR):
                stack.append(i + 1)
        return out

    if deadline is not None and deadline():
        raise ViewTimeout("path match ran past its deadline")
    states = closure((0,))
    for ch in rel:
        tick(len(states))
        nxt = set()
        for i in states:
            if i == end:
                continue
            kind, lit = tokens[i]
            if kind == _LIT:
                if ch == lit:
                    nxt.add(i + 1)
            elif kind == _ONE:
                if ch != "/":
                    nxt.add(i + 1)
            elif kind == _STAR:
                if ch != "/":
                    nxt.add(i)
            elif ch != "\n":
                nxt.add(i)
        if not nxt:
            return False
        states = closure(nxt)
    return end in states


def paths_view(connection: sqlite3.Connection, rel: str, deadline=None) -> dict:
    """Classify every store entity that claims `rel`: open listed work, closed, prose, related.

    `deadline` (the read endpoint's): checked per row and inside each glob match; ViewTimeout past it."""
    native = is_native(connection)
    rows = connection.execute(PATH_MATCH_SQL if native else LEGACY_MATCH_SQL, (rel,)).fetchall()

    matched: dict = {}
    for kind, eid, status, archived, match_kind, path, source in rows:
        if deadline is not None and deadline():
            raise ViewTimeout("paths view ran past its deadline")
        if match_kind == "glob" and not glob_match(path, rel, deadline):
            continue
        entry = matched.get((kind, eid))
        if entry is None:
            matched[(kind, eid)] = entry = [status, archived, False]
        if source in STRUCTURAL_SOURCES:
            entry[2] = True

    listed = []
    closed = 0
    prose = 0
    for (kind, eid), (status, archived, structural) in matched.items():
        if kind not in KIND_ORDER:
            prose += 1
            continue
        # Handovers carry no anchors/location field at all — prose is the only
        # path signal they can ever have, so it is the one that counts for them.
        if not structural and kind != "handover":
            prose += 1
        elif not archived and (status or "") in OPEN_STATUS[kind]:
            listed.append({"kind": kind, "id": eid, "status": status or ""})
        else:
            closed += 1

    listed.sort(key=lambda e: (KIND_ORDER[e["kind"]], e["id"]))
    return {"path": rel, "listed": listed, "closed": closed, "prose": prose,
            "related": _count_related(connection, listed, matched, native)}


def _count_related(connection, listed: list, matched: dict, native: bool) -> int:
    """Open work that travels with the listed items but does not claim this file."""
    found_neighbours = set()
    for entry in listed:
        key = (entry["kind"], entry["id"])
        found = (neighbours(connection, *key) if native else
                 connection.execute(LEGACY_RELATED_SQL, (key[0], key[1], key[0], key[1])))
        for kind, eid in found:
            if (kind, eid) not in matched:
                found_neighbours.add((kind, eid))
    count = 0
    for kind, eid in found_neighbours:
        if kind not in OPEN_STATUS:
            continue
        row = connection.execute(LIVE_STATUS_SQL if native else LEGACY_LIVE_STATUS_SQL, (kind, eid)).fetchone()
        if row and (row[0] or "") in OPEN_STATUS[kind]:
            count += 1
    return count


# ── Branches (merge gate, merge recorder) ───────────────────────

REF_RE = re.compile(r"[A-Za-z0-9._/@+-]{1,255}")
MAX_REF_CHARS = 255


def ref_problem(value) -> str | None:
    """Why `value` can't travel as a branch name to the team server, or None when it can.

    The one rule for both sides: the server's `valid_ref` and the client's check before it asks.
    """
    if not isinstance(value, str):
        return "not text"
    if len(value) > MAX_REF_CHARS:
        return f"longer than {MAX_REF_CHARS} characters"
    if not REF_RE.fullmatch(value):
        return "holds a character outside letters, digits and . _ / @ + -"
    if ".." in value:
        return "holds '..'"
    if value.startswith(("-", "/")):
        return "starts with '-' or '/'"
    if value.endswith("/"):
        return "ends with '/'"
    return None


def tasks_for_branch(connection: sqlite3.Connection, identity, branch: str) -> list[tuple[str, dict]]:
    """Every live, unarchived task on `branch` as `(id, fields)`, ordered by id.

    `identity` is the native manifest (`admit`), or None on a legacy store.
    """
    if identity is not None:
        ids = [row[0] for row in connection.execute(_TASK_BY_BRANCH_SQL, (branch,))]
        if not ids:
            return []
        snapshot = _snapshot(connection, identity)
        return [(ident, snapshot.get("task", ident)["fields"]) for ident in ids]
    try:
        rows = connection.execute(LEGACY_TASK_BY_BRANCH_SQL, (branch,)).fetchall()
    except sqlite3.OperationalError:
        # An interpreter whose SQLite lacks JSON1 still has to decide.
        rows = [(ident, doc) for ident, doc in connection.execute(
            "SELECT id, doc FROM entities WHERE kind='task' AND deleted=0 AND archived=0 ORDER BY id")
            if json.loads(doc).get("branch") == branch]
    return [(ident, json.loads(doc)) for ident, doc in rows]


def branch_candidates(connection: sqlite3.Connection, identity, branch: str, principal, clone) -> tuple[list, frozenset]:
    """`(candidates, own)` for a server's hooks (team-server-011 D6).

    The candidates are `tasks_for_branch`'s answer (the shared field) plus every live, unarchived task that any person
    maps to `branch` in `clone_fields`, ordered by id. `own` is the ids where the caller has a matching row for their
    `clone` or their default clone. Every person's rows are searched, so a teammate's task is still found and gated when
    the caller merges the teammate's branch."""
    shared = tasks_for_branch(connection, identity, branch)
    if identity is None or principal is None:
        return shared, frozenset()
    from taskmaster.native import clone_fields
    matches = clone_fields.branch_matches(connection, branch)
    if not matches:
        return shared, frozenset()
    wanted = {"", clone or ""}
    own = frozenset(ident for handle, mapped, ident in matches if handle == principal.handle and mapped in wanted)
    known = {ident for ident, _ in shared}
    extra = sorted({ident for _, _, ident in matches} - known)
    if extra:
        marks = ",".join("?" for _ in extra)
        live = [row[0] for row in connection.execute(
            f"SELECT public_id FROM entity_core WHERE kind='task' AND deleted=0 AND archived=0 AND public_id IN ({marks})",
            extra)]
        if live:
            snapshot = _snapshot(connection, identity)
            shared = sorted(shared + [(ident, snapshot.get("task", ident)["fields"]) for ident in live],
                            key=lambda item: item[0])
    return shared, own


def _is_callers(fields: dict, handle: str) -> bool:
    """The task is the caller's: its owner or assignee is the caller's handle, or its claim is
    held by one of the caller's sessions (team-server-003's `<handle>@<16hex>`)."""
    if fields.get("owner") == handle or fields.get("assignee") == handle:
        return True
    holder = fields.get("locked_by")
    return isinstance(holder, str) and holder.startswith(f"{handle}@")


def choose_task(candidates: list[tuple[str, dict]], principal=None, own=frozenset()) -> dict:
    """`{"task": (id, fields) | None, "mine": bool, "ambiguous": bool, "candidates": [ids]}` (design D3a).

    Without a principal (a solo hook) the first by id, today's rule. With one (a team server),
    the only candidate, else the caller's only one, else ambiguous: never someone else's by id order.
    """
    ids = [ident for ident, _ in candidates][:MAX_CANDIDATES]
    if not candidates:
        return {"task": None, "mine": False, "ambiguous": False, "candidates": []}
    if principal is None:
        return {"task": candidates[0], "mine": False, "ambiguous": False, "candidates": ids}
    handle = principal.handle
    # Exactly one candidate the caller maps to this branch in their own clone (011 D6): theirs, ahead of every other rule.
    mapped = [c for c in candidates if c[0] in own]
    if len(mapped) == 1:
        return {"task": mapped[0], "mine": True, "ambiguous": False, "candidates": ids}
    if len(candidates) == 1:
        return {"task": candidates[0], "mine": _is_callers(candidates[0][1], handle) or candidates[0][0] in own,
                "ambiguous": False, "candidates": ids}
    mine = [c for c in candidates if _is_callers(c[1], handle) or c[0] in own]
    if len(mine) == 1:
        return {"task": mine[0], "mine": True, "ambiguous": False, "candidates": ids}
    return {"task": None, "mine": False, "ambiguous": True, "candidates": ids}


def _policy_in(manifest: dict | None) -> bool:
    conventions = (manifest or {}).get("conventions") or {}
    policies = conventions.get("policies") or {}
    return bool(policies.get("review_gate_required_for_merge"))


def merge_policy_required(connection: sqlite3.Connection, identity) -> bool:
    if identity is not None:
        return _policy_in(project_fields(connection, identity))
    row = connection.execute(
        "SELECT doc FROM entities WHERE kind='project' AND deleted=0 AND archived=0 ORDER BY id LIMIT 1").fetchone()
    return _policy_in(json.loads(row[0])) if row else False


def _hex_sha(value) -> str:
    """027 D8: only a hex SHA reaches the hook views; anything else reads as unknown (the gate then blocks)."""
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{7,64}", value) else ""


# `taskmaster_v3.VALID_GATE_VERDICTS`, restated because hooks can't import yaml; a test keeps the two equal.
GATE_VERDICTS = ("pass", "warn", "fail")


def gate_fields(task: dict) -> dict:
    """The fields of a task the merge gate decides on, in the view's shape. Only enum values leave: the viewer and
    other unvalidated paths can store any verdict or freshness text (027 review Codex I1); anything else reads as
    unknown ("" verdict, so the gate blocks) or as the strict default."""
    gates = task.get("gates") or {}
    rg = gates.get("review-gate") if isinstance(gates, dict) else None
    rg = rg if isinstance(rg, dict) else None
    verdict = rg.get("verdict", "") if rg else ""
    return {"skip_merge_gate": bool(task.get("skip_merge_gate")),
            "merge_gate_freshness": "any" if task.get("merge_gate_freshness") == "any" else "strict",
            "review_gate": None if not rg else {"verdict": verdict if verdict in GATE_VERDICTS else "",
                                                "commit_sha": _hex_sha(rg.get("commit_sha", ""))}}


def _chosen(connection, identity, branch, principal, clone) -> dict:
    """The task a hook's `branch` means for this caller: the solo rule without a principal, else the clone-aware one."""
    if principal is None:
        return choose_task(tasks_for_branch(connection, identity, branch), None)
    candidates, own = branch_candidates(connection, identity, branch, principal, clone)
    return choose_task(candidates, principal, own)


def merge_gate_view(connection: sqlite3.Connection, branch: str, principal=None, clone=None) -> dict:
    """What the merge gate needs for `branch`; the verdict and the Git tip check stay with the caller.

    `clone` is the caller's clone id (008 section 11's wire parameter); with a principal the task is chosen clone-aware
    (`branch_candidates`, team-server-011 D6)."""
    identity = admit(connection)
    policy = merge_policy_required(connection, identity)
    if not policy:
        return {"policy_required": False, "ambiguous": False, "candidates": [], "task": None}
    chosen = _chosen(connection, identity, branch, principal, clone)
    task = None
    if chosen["task"] is not None:
        ident, fields = chosen["task"]
        task = {"id": ident, "mine": chosen["mine"], **gate_fields(fields)}
    return {"policy_required": True, "ambiguous": chosen["ambiguous"], "candidates": chosen["candidates"],
            "task": task}


def merge_ladder(connection: sqlite3.Connection, identity) -> list:
    """A native store's merge ladder from its committed project row."""
    from taskmaster.native_routing.tasks import _merge_targets
    return _merge_targets(_snapshot(connection, identity))


def merge_target_view(connection: sqlite3.Connection, branch: str, target: str, principal=None, clone=None) -> dict:
    """The task a merge of `branch` into `target` stamps, and the rung it reaches. Native stores only:
    a legacy store's ladder lives in project.yaml, which the solo recorder reads itself."""
    identity = admit(connection)
    if identity is None:
        raise ValueError("merge-target needs a native store")
    from taskmaster.taskmaster_v3 import rung_for_branch
    chosen = _chosen(connection, identity, branch, principal, clone)
    rung = rung_for_branch(target, merge_ladder(connection, identity)) or f"branch:{target}"
    return {"ambiguous": chosen["ambiguous"], "candidates": chosen["candidates"],
            "task_id": chosen["task"][0] if chosen["task"] is not None else None, "rung": rung}


def check_merge_gate_view(data) -> str | None:
    """Why a `merge-gate` reply's `data` can't be trusted, naming the field, or None when its shape is whole.

    The client runs this before it lets a merge through; the server's tests use it as their oracle."""
    if not isinstance(data, dict):
        return "data is not an object"
    for name in ("policy_required", "ambiguous"):
        if not isinstance(data.get(name), bool):
            return f"{name} is missing or not a boolean"
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or any(not isinstance(item, str) for item in candidates):
        return "candidates is not a list of strings"
    task = data.get("task")
    if task is None:
        return None
    if not isinstance(task, dict):
        return "task is neither null nor an object"
    if not isinstance(task.get("id"), str):
        return "task.id is not a string"
    for name in ("mine", "skip_merge_gate"):
        if not isinstance(task.get(name), bool):
            return f"task.{name} is missing or not a boolean"
    if task.get("merge_gate_freshness") not in ("strict", "any"):
        return "task.merge_gate_freshness is neither strict nor any"
    gate = task.get("review_gate")
    if gate is not None and (not isinstance(gate, dict) or not isinstance(gate.get("verdict"), str)
                             or not isinstance(gate.get("commit_sha"), str)):
        return "task.review_gate is neither null nor {verdict, commit_sha} strings"
    return None


def merge_stamp_plan(connection: sqlite3.Connection, branch: str, target: str, principal=None, clone=None) -> dict:
    """What a merge stamp for `branch` into `target` would touch: the chosen task, the rung the ladder gives
    `target` today, and the rung's current record (`{"merge_commit", "merged_at"}` or None). Native stores only."""
    identity = admit(connection)
    if identity is None:
        raise ValueError("merge-stamp needs a native store")
    from taskmaster.taskmaster_v3 import rung_for_branch
    chosen = _chosen(connection, identity, branch, principal, clone)
    rung = rung_for_branch(target, merge_ladder(connection, identity)) or f"branch:{target}"
    task_id, current = None, None
    if chosen["task"] is not None:
        task_id, fields = chosen["task"]
        record = (fields.get("merge_status") or {}).get(rung)
        if isinstance(record, dict) and record.get("merge_commit"):
            current = {"merge_commit": str(record["merge_commit"]), "merged_at": str(record.get("merged_at", ""))}
    return {"ambiguous": chosen["ambiguous"], "candidates": chosen["candidates"], "task_id": task_id,
            "rung": rung, "current": current}
