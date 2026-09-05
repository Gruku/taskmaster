#!/usr/bin/env python3
# User intent: when the agent edits a file, tell it in ONE line which open bugs,
# issues, tasks and handovers already point at that file — so backlog context
# surfaces itself instead of waiting to be asked for. Read-only and advisory:
# it never imports a backlog, never writes a row, and never blocks a tool call.
"""edit_resurface.py — PostToolUse hook for Edit|Write|MultiEdit.

Reads the SQLite store at `.taskmaster/local/store.db` (written by the MCP
server, never by this hook) and prints at most one `additionalContext` line.

The store is the runtime authority, so there is no such thing as a stale
read: what the hook must avoid instead is repeating a line the agent has
already seen. `MAX(changes.seq)` is recorded beside each memoised line in
`local/hook-seen/`; an unchanged seq means the answer cannot have changed
and the query is skipped entirely.

Imports are limited to the standard library plus `taskmaster.root`, which is
itself standard-library-only. Hooks run under the system interpreter, not the
uv venv, so importing `yaml`, `fastmcp` or `taskmaster.store` would make this
hook dead on every machine that lacks the venv.

Exit code is always 0. Failures are appended to `.taskmaster/local/hook.log`.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

# This script lives in hooks/; the taskmaster package is at the repo root one
# level up. Subprocess invocation puts hooks/ on sys.path, not the root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from taskmaster.root import (
        _git_checkout_root as git_checkout_root,
        db_path,
        resolve_root,
    )
except Exception:  # pragma: no cover — partially installed plugin
    resolve_root = None
    db_path = None
    git_checkout_root = None

EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}

# Kinds that can be named in the line, in output order.
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

MAX_IDS = 6
HANDOVER_ID_CHARS = 24
SEEN_TTL_SECONDS = 7 * 86400
BUSY_TIMEOUT_SECONDS = 2.0
LOG_MAX_BYTES = 1024 * 1024
LOG_KEEP_BYTES = 512 * 1024

_WORKTREE_RE = re.compile(r"^\.worktrees/[^/]+/")

_MATCH_SQL = (
    "SELECT e.kind, e.id, e.status, e.archived, p.match_kind, p.path, p.source"
    " FROM entity_paths p JOIN entities e ON e.kind = p.kind AND e.id = p.id"
    " WHERE e.deleted = 0"
    "   AND ((p.match_kind='exact' AND p.path=?) OR p.match_kind='glob')"
)

_RELATED_SQL = (
    "SELECT b_kind, b_id FROM related WHERE a_kind=? AND a_id=?"
    " UNION SELECT a_kind, a_id FROM related WHERE b_kind=? AND b_id=?"
)


class Entry:
    __slots__ = ("id", "kind", "status")

    def __init__(self, id: str, kind: str, status: str) -> None:
        self.id = id
        self.kind = kind
        self.status = status


class ResolveResult:
    __slots__ = ("listed", "closed", "prose", "related")

    def __init__(self, listed: list, closed: int, prose: int, related: int = 0) -> None:
        self.listed = listed
        self.closed = closed
        self.prose = prose
        self.related = related


# ── Matching ────────────────────────────────────────────────────


def _glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """Anchored regex for a path glob: `**` crosses `/`, `*` and `?` do not."""
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out) + r"\Z")


def _glob_match(pattern: str, rel: str) -> bool:
    return _glob_to_regex(pattern).match(rel) is not None


def _connect_ro(db_file) -> sqlite3.Connection:
    """Read-only handle. The hook must never open a write transaction, let
    alone write a row — the server owns every write to the store."""
    uri = Path(db_file).resolve().as_uri()
    try:
        return sqlite3.connect(uri + "?mode=ro", uri=True, timeout=BUSY_TIMEOUT_SECONDS)
    except sqlite3.OperationalError:
        # A WAL database cannot be opened `mode=ro` when its `-shm` is absent
        # and cannot be created (design spec 3.1). `query_only` keeps the
        # guarantee that matters: this connection can never write a row.
        con = sqlite3.connect(uri + "?mode=rw", uri=True, timeout=BUSY_TIMEOUT_SECONDS)
        con.execute("PRAGMA query_only=ON")
        return con


def max_change_seq(db_file) -> int:
    """`MAX(changes.seq)` — the store revision this answer belongs to."""
    con = _connect_ro(db_file)
    try:
        row = con.execute("SELECT MAX(seq) FROM changes").fetchone()
    finally:
        con.close()
    return int(row[0]) if row and row[0] is not None else 0


def resolve(db_file, rel: str) -> ResolveResult:
    """Classify every store entity that claims `rel`.

    Exposed as a module function so the timing and formatting tests can call it
    in-process without paying for interpreter startup.
    """
    con = _connect_ro(db_file)
    try:
        rows = con.execute(_MATCH_SQL, (rel,)).fetchall()

        matched: dict = {}
        for kind, eid, status, archived, match_kind, path, source in rows:
            if match_kind == "glob" and not _glob_match(path, rel):
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
            # Handovers carry no anchors/location field at all — prose is the
            # only path signal they can ever have, so it is the one that counts
            # for them.
            if not structural and kind != "handover":
                prose += 1
            elif not archived and (status or "") in OPEN_STATUS[kind]:
                listed.append(Entry(eid, kind, status or ""))
            else:
                closed += 1

        listed.sort(key=lambda e: (KIND_ORDER[e.kind], e.id))
        related = _count_related(con, listed, matched)
    finally:
        con.close()

    return ResolveResult(listed, closed, prose, related)


def _count_related(con, listed: list, matched: dict) -> int:
    """Open work that travels with the listed items but does not claim this file.

    The count is the invitation to run `backlog_query`; naming the ids would
    cost more line than the association is worth.
    """
    neighbours = set()
    for entry in listed:
        key = (entry.kind, entry.id)
        for kind, eid in con.execute(_RELATED_SQL, (key[0], key[1], key[0], key[1])):
            if (kind, eid) not in matched:
                neighbours.add((kind, eid))
    count = 0
    for kind, eid in neighbours:
        if kind not in OPEN_STATUS:
            continue
        row = con.execute(
            "SELECT status FROM entities"
            " WHERE kind=? AND id=? AND deleted=0 AND archived=0",
            (kind, eid),
        ).fetchone()
        if row and (row[0] or "") in OPEN_STATUS[kind]:
            count += 1
    return count


# ── Formatting ──────────────────────────────────────────────────


def format_line(rel: str, result: ResolveResult) -> str:
    """One line naming the open work, `HND `-labelled for handovers.

    Handover ids are dated slugs — long, and unrecognisable next to a `B-231`.
    The label says what the id is and the truncation keeps the line short; the
    status is redundant because only open handovers are ever listed.
    """
    parts = []
    for entry in result.listed[:MAX_IDS]:
        if entry.kind == "handover":
            eid = entry.id
            if len(eid) > HANDOVER_ID_CHARS:
                eid = eid[:HANDOVER_ID_CHARS] + "…"
            parts.append(f"HND {eid}")
        else:
            parts.append(f"{entry.id} {entry.status}")
    overflow = len(result.listed) - MAX_IDS
    if overflow > 0:
        parts.append(f"+{overflow} more")

    line = f"TM: {rel} → " + ", ".join(parts)
    counts = []
    if result.closed:
        counts.append(f"+{result.closed} closed")
    if result.prose:
        counts.append(f"+{result.prose} prose")
    if result.related:
        counts.append(f"+{result.related} related")
    if counts:
        line += " (" + ", ".join(counts) + ")"
    return line


# ── Project / path plumbing ─────────────────────────────────────


def find_root(start: Path):
    """Fallback root walk for a plugin whose `taskmaster` package is missing."""
    for candidate in [start, *start.parents]:
        if (candidate / ".taskmaster").is_dir():
            return candidate
    return None


def project_root(start: Path, target: Path):
    """`(root, reason)` — the checkout whose `.taskmaster/` owns this edit.

    `resolve_root` is the shared rule (`TASKMASTER_ROOT`, else the git common
    dir, else cwd), so an edit made inside a linked worktree resolves to the
    main checkout's store rather than to no store at all.
    """
    if resolve_root is not None:
        try:
            return resolve_root(start).root, None
        except Exception as exc:
            return find_root(start) or find_root(target.parent), f"resolve_root failed: {exc!r}"
    return (
        find_root(start) or find_root(target.parent),
        "taskmaster.root unavailable; walked up for .taskmaster/",
    )


def relative_path(root: Path, target: Path):
    """Repo-relative, forward-slashed path, or None when out of scope."""
    rel = _relative_to(root, target)
    if rel is None and git_checkout_root is not None:
        # A linked worktree can live outside the main checkout, and the backlog
        # records paths relative to the checkout the file actually sits in.
        checkout = git_checkout_root(target.parent)
        if checkout is not None:
            rel = _relative_to(checkout, target)
    if rel is None:
        return None
    # Worktree edits touch the same repo paths the backlog records.
    rel = _WORKTREE_RE.sub("", rel)
    if rel.startswith(".taskmaster/"):
        return None
    return rel


def _relative_to(root: Path, target: Path):
    try:
        rel = os.path.relpath(str(target), str(root)).replace("\\", "/")
    except ValueError:  # different drive on Windows
        return None
    if rel == ".." or rel.startswith("../"):
        return None
    return rel


def seen_path(root: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id) or "nosession"
    return root / ".taskmaster" / "local" / "hook-seen" / f"{safe}.json"


def load_seen(path: Path) -> dict:
    """`{"seq": int, "lines": {rel: line}}`, tolerating an older list file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"seq": -1, "lines": {}}
    if isinstance(data, dict):
        lines = data.get("lines")
        seq = data.get("seq")
        return {
            "seq": seq if isinstance(seq, int) else -1,
            "lines": lines if isinstance(lines, dict) else {},
        }
    return {"seq": -1, "lines": {}}


def record_seen(path: Path, seen: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen), encoding="utf-8")
    cutoff = time.time() - SEEN_TTL_SECONDS
    for sibling in path.parent.glob("*.json"):
        try:
            if sibling.stat().st_mtime < cutoff:
                sibling.unlink()
        except OSError:
            pass


def log_reason(root: Path, reason: str) -> None:
    """One line saying why the hook stayed quiet. Never raises."""
    try:
        log = root / ".taskmaster" / "local" / "hook.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.time()} edit_resurface: {reason}\n")
        if log.stat().st_size > LOG_MAX_BYTES:
            tail = log.read_bytes()[-LOG_KEEP_BYTES:]
            log.write_bytes(tail)
    except Exception:
        pass


# ── Entry point ─────────────────────────────────────────────────


def main() -> int:
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            return 0
        if data.get("tool_name") not in EDIT_TOOLS:
            return 0
        tool_input = data.get("tool_input")
        file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
        if not isinstance(file_path, str) or not file_path:
            return 0
        response = data.get("tool_response")
        if isinstance(response, dict) and response.get("success") is False:
            return 0
        cwd = data.get("cwd")
        session_id = data.get("session_id")
    except Exception:
        return 0

    root = None
    try:
        target = Path(file_path)
        start = Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()
        root, reason = project_root(start, target)
        if root is None or not (root / ".taskmaster").is_dir():
            return 0
        if reason:
            log_reason(root, reason)

        rel = relative_path(root, target)
        if rel is None:
            return 0

        db_file = db_path(root / ".taskmaster") if db_path is not None else (
            root / ".taskmaster" / "local" / "store.db")
        if not db_file.is_file():
            # Never import in a hook: an absent store is the server's job to
            # create, and building one here would cost seconds on an edit.
            log_reason(root, f"no store at {db_file}; staying quiet")
            return 0

        seq = max_change_seq(db_file)
        seen_file = seen_path(root, session_id if isinstance(session_id, str) else "")
        seen = load_seen(seen_file)
        previous = seen["lines"].get(rel)
        if previous is not None and seen["seq"] == seq:
            return 0

        result = resolve(db_file, rel)
        if not result.listed:
            return 0

        line = format_line(rel, result)
        seen["seq"] = seq
        # Recorded only now: dedupe suppresses a repeat of a line the agent has
        # already seen, so a silent edit must not burn the path. Otherwise the
        # first edit before a bug is filed would mute every later edit.
        seen["lines"][rel] = line
        record_seen(seen_file, seen)
        if line == previous:
            return 0
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": line}}))
    except Exception as exc:
        if root is not None:
            log_reason(root, repr(exc))
        return 0
    return 0


if __name__ == "__main__":
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # advisory hook — never blocks
