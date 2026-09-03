#!/usr/bin/env python3
# User intent: when the agent edits a file, tell it in ONE line which open bugs,
# issues, tasks and handovers already point at that file — so backlog context
# surfaces itself instead of waiting to be asked for. Read-only and advisory:
# it never builds the index and never blocks a tool call.
"""edit_resurface.py — PostToolUse hook for Edit|Write|MultiEdit.

Reads the derived index at `.taskmaster/local/index.db` (built by the MCP
server, never by this hook) and prints at most one `additionalContext` line.

Standard library only: hooks run under the system interpreter, not the uv
venv, so importing `yaml`, `fastmcp` or the `taskmaster` package would make
this hook dead on every machine that lacks the venv.

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
    __slots__ = ("listed", "closed", "prose")

    def __init__(self, listed: list, closed: int, prose: int) -> None:
        self.listed = listed
        self.closed = closed
        self.prose = prose


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


def resolve(db_path, rel: str) -> ResolveResult:
    """Classify every index entity that claims `rel`.

    Exposed as a module function so the timing and formatting tests can call it
    in-process without paying for interpreter startup.
    """
    uri = "file:" + str(Path(db_path).resolve()).replace("\\", "/").lstrip("/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=2.0)
    try:
        rows = con.execute(
            "SELECT e.id, e.kind, e.status, p.match_kind, p.path, p.source"
            " FROM entity_paths p JOIN entities e ON e.id = p.entity_id"
            " WHERE (p.match_kind='exact' AND p.path=?) OR p.match_kind='glob'",
            (rel,),
        ).fetchall()
    finally:
        con.close()

    matched: dict = {}
    for eid, kind, status, match_kind, path, source in rows:
        if match_kind == "glob" and not _glob_match(path, rel):
            continue
        entry = matched.get(eid)
        if entry is None:
            matched[eid] = entry = [kind, status, False]
        if source in STRUCTURAL_SOURCES:
            entry[2] = True

    listed = []
    closed = 0
    prose = 0
    for eid, (kind, status, structural) in matched.items():
        if kind not in KIND_ORDER:
            prose += 1
            continue
        # Handovers carry no anchors/location field at all — prose is the only
        # path signal they can ever have, so it is the one that counts for them.
        if not structural and kind != "handover":
            prose += 1
        elif (status or "") in OPEN_STATUS[kind]:
            listed.append(Entry(eid, kind, status or ""))
        else:
            closed += 1

    listed.sort(key=lambda e: (KIND_ORDER[e.kind], e.id))
    return ResolveResult(listed, closed, prose)


# ── Formatting ──────────────────────────────────────────────────


def format_line(rel: str, result: ResolveResult, stale: bool) -> str:
    parts = []
    for entry in result.listed[:MAX_IDS]:
        if entry.kind == "handover":
            eid = entry.id
            parts.append(eid if len(eid) <= HANDOVER_ID_CHARS
                         else eid[:HANDOVER_ID_CHARS] + "…")
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
    if counts:
        line += " (" + ", ".join(counts) + ")"
    if stale:
        line += " (index stale)"
    return line


# ── Project / path plumbing ─────────────────────────────────────


def find_root(start: Path):
    for candidate in [start, *start.parents]:
        if (candidate / ".taskmaster" / "backlog.yaml").is_file():
            return candidate
    return None


def relative_path(root: Path, target: Path):
    """Repo-relative, forward-slashed path, or None when out of scope."""
    try:
        rel = os.path.relpath(str(target), str(root)).replace("\\", "/")
    except ValueError:  # different drive on Windows
        return None
    if rel == ".." or rel.startswith("../"):
        return None
    # Worktree edits touch the same repo paths the backlog records.
    rel = _WORKTREE_RE.sub("", rel)
    if rel.startswith(".taskmaster/"):
        return None
    return rel


def is_stale(root: Path, db_file: Path) -> bool:
    """True when the index cannot be trusted to reflect the files on disk."""
    con = sqlite3.connect(str(db_file), timeout=2.0)
    try:
        meta = dict(con.execute("select key, value from meta").fetchall())
    finally:
        con.close()

    tm = root / ".taskmaster"
    source_max = meta.get("source_mtime_max")
    if source_max:
        try:
            if float(source_max) < (tm / "backlog.yaml").stat().st_mtime:
                return True
        except (OSError, ValueError):
            pass

    built_at = meta.get("built_at_epoch")
    if built_at:
        try:
            built = float(built_at)
        except ValueError:
            built = None
        if built is not None:
            # Directory mtimes catch entity files added or deleted since the
            # build — neither of which touches backlog.yaml.
            for name in ("bugs", "issues", "handovers"):
                d = tm / name
                try:
                    if d.is_dir() and d.stat().st_mtime > built:
                        return True
                except OSError:
                    pass

    # A budget-truncated build still stamps a fresh built_at, so the report's
    # own flag is the only signal that files were left unread.
    report = meta.get("last_report")
    if report:
        try:
            if json.loads(report).get("stale"):
                return True
        except (ValueError, AttributeError):
            pass
    return False


def seen_path(root: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id) or "nosession"
    return root / ".taskmaster" / "local" / "hook-seen" / f"{safe}.json"


def load_seen(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def record_seen(path: Path, seen: list, rel: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    seen.append(rel)
    path.write_text(json.dumps(seen), encoding="utf-8")
    cutoff = time.time() - SEEN_TTL_SECONDS
    for sibling in path.parent.glob("*.json"):
        try:
            if sibling.stat().st_mtime < cutoff:
                sibling.unlink()
        except OSError:
            pass


def log_error(root: Path, exc: BaseException) -> None:
    try:
        log = root / ".taskmaster" / "local" / "hook.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.time()} {exc!r}\n")
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
        root = find_root(start) or find_root(target.parent)
        if root is None:
            return 0

        rel = relative_path(root, target)
        if rel is None:
            return 0

        db_file = root / ".taskmaster" / "local" / "index.db"
        if not db_file.is_file():
            return 0

        seen_file = seen_path(root, session_id if isinstance(session_id, str) else "")
        seen = load_seen(seen_file)
        if rel in seen:
            return 0

        result = resolve(db_file, rel)
        record_seen(seen_file, seen, rel)
        if not result.listed:
            return 0

        line = format_line(rel, result, is_stale(root, db_file))
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": line}}))
    except Exception as exc:
        if root is not None:
            log_error(root, exc)
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
