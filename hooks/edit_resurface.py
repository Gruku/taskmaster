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
`local/hook-seen/`; an unchanged seq for that path means the answer cannot
have changed and the query is skipped entirely.

Root resolution is the shared rule, so outside a git repository the hook
walks up to the nearest ancestor holding a backlog exactly as the server
does — an edit from a subdirectory still finds the project.

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
from taskmaster.native_routing import hook_reads
from taskmaster import server_marker

_STARTED = time.monotonic()      # the hook's own clock: a server read never outlives the host's 10 s kill
SERVER_BUDGET_SECONDS = 2.0

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

# The classification rules live with the read view, so a team server answers exactly as this hook does.
LISTED_KINDS = hook_reads.LISTED_KINDS
KIND_ORDER = hook_reads.KIND_ORDER
OPEN_STATUS = hook_reads.OPEN_STATUS
STRUCTURAL_SOURCES = hook_reads.STRUCTURAL_SOURCES

MAX_IDS = 6
HANDOVER_ID_CHARS = 24
SEEN_TTL_SECONDS = 7 * 86400
BUSY_TIMEOUT_SECONDS = 2.0
LOG_MAX_BYTES = 1024 * 1024
LOG_KEEP_BYTES = 512 * 1024

_WORKTREE_RE = re.compile(r"^\.worktrees/[^/]+/")


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

    @classmethod
    def from_view(cls, data: dict) -> "ResolveResult":
        """From a `paths` view, local or the team server's (v1 `data`)."""
        return cls([Entry(e["id"], e["kind"], e["status"]) for e in data["listed"]],
                   data["closed"], data["prose"], data["related"])


# ── Matching ────────────────────────────────────────────────────


def _connect_ro(db_file) -> sqlite3.Connection:
    """A read-only connection inside an open read transaction (`hook_reads.connect_ro`)."""
    return hook_reads.connect_ro(db_file, BUSY_TIMEOUT_SECONDS)


def max_change_seq(db_file) -> int:
    """The store revision this answer belongs to."""
    con = _connect_ro(db_file)
    try:
        return _max_change_seq(con)
    finally:
        con.close()


def _max_change_seq(con: sqlite3.Connection) -> int:
    return hook_reads.store_revision(con)


def resolve(db_file, rel: str) -> ResolveResult:
    """Classify every store entity that claims `rel`.

    Exposed as a module function so the timing and formatting tests can call it
    in-process without paying for interpreter startup.
    """
    con = _connect_ro(db_file)
    try:
        return _resolve(con, rel)
    finally:
        con.close()


def _resolve(con: sqlite3.Connection, rel: str) -> ResolveResult:
    return ResolveResult.from_view(hook_reads.paths_view(con, rel))


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


def seen_path(root: Path, session_id: str, base: Path | None = None) -> Path:
    """The dedupe file: under `.taskmaster/local/` in solo, under the server's hook state dir in server mode."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id) or "nosession"
    folder = base if base is not None else root / ".taskmaster" / "local"
    return folder / "hook-seen" / f"{safe}.json"


def load_seen(path: Path) -> dict:
    """`{rel: [seq, line]}` — the line last shown for a path, and when.

    The seq belongs to the path, not to the file: one seq for the whole session
    would let a print for one path mark every other path as already answered,
    and a genuinely changed line would then never be shown again. Older file
    shapes (a bare list of paths, or a single top-level seq) are read as empty
    rather than misread.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    seen = {}
    for rel, entry in data.items():
        if (
            isinstance(rel, str)
            and isinstance(entry, list)
            and len(entry) == 2
            and isinstance(entry[0], int)
            and isinstance(entry[1], str)
        ):
            seen[rel] = [entry[0], entry[1]]
    return seen


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
        if root is None:
            return 0
        try:
            marker = server_marker.server_mode(root)
        except server_marker.ServerConfigError:
            return 0          # a server clone whose marker can't be read: never fall back to a local store
        if marker is not None:
            return _server_mode(root, target, session_id if isinstance(session_id, str) else "")
        if not (root / ".taskmaster").is_dir():
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

        seen_file = seen_path(root, session_id if isinstance(session_id, str) else "")
        seen = load_seen(seen_file)
        entry = seen.get(rel)

        connection = _connect_ro(db_file)
        try:
            seq = _max_change_seq(connection)
            if entry is not None and entry[0] == seq:
                # This path was answered at this exact store revision; nothing
                # can have changed, so the query is skipped entirely.
                return 0
            result = _resolve(connection, rel)
        finally:
            connection.close()

        if not result.listed:
            return 0

        line = format_line(rel, result)
        previous = entry[1] if entry is not None else None
        # Recorded only now: dedupe suppresses a repeat of a line the agent has
        # already seen, so a silent edit must not burn the path. Otherwise the
        # first edit before a bug is filed would mute every later edit.
        seen[rel] = [seq, line]
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


def _server_mode(root: Path, target: Path, session_id: str) -> int:
    """A team-server clone (team-server-007): the same line, from the server's `paths` view."""
    from taskmaster.remote import hookside
    from taskmaster.remote.client import ReadUnavailable, hook_deadline

    state = None

    def log(text):
        hookside.append_log(state, "edit_resurface", text)
    try:
        server = hookside.server_of(root)
        state = hookside.state_dir(server)
        rel = relative_path(root, target)
        if rel is None:
            return 0
        session = hookside.connect(root, log=log)
        result = session.client.read("paths", {"path": rel},
                                     deadline=hook_deadline(SERVER_BUDGET_SECONDS, started=_STARTED))
    except ReadUnavailable as failure:
        log(f"{failure.kind}: {failure.message}; staying quiet")
        return 0
    resolved = ResolveResult.from_view(result.data)
    if not resolved.listed:
        return 0
    line = format_line(rel, resolved)
    if state is not None:
        seen_file = seen_path(root, session_id, base=state)
        seen = load_seen(seen_file)
        entry = seen.get(rel)
        seen[rel] = [result.revision, line]
        record_seen(seen_file, seen)
        if entry is not None and entry[1] == line:
            return 0
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": line}}))
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
