"""Taskmaster v3 — narrative-continuity layout helpers.

This module is intentionally framework-free (no fastmcp imports) so it can be
tested in isolation. It owns:

- Schema version constants and detection.
- Atomic file writes.
- (Future slices) Frontmatter parsing, per-task file I/O, v3 load/save, migration.

`backlog_server.py` re-exports the symbols it needs from here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

# Schema versions
# v2: single backlog.yaml with epics/tasks inline. (Legacy: missing version implies v2.)
# v3: slim backlog.yaml index + per-task files in tasks/ + handovers/ + lessons/ + issues/.
SCHEMA_V2 = 2
SCHEMA_V3 = 3
SCHEMA_DEFAULT = SCHEMA_V2  # what new backlogs get unless v3 is explicitly requested


def detect_schema_version(data: dict) -> int:
    """Return the schema version of a loaded backlog dict.

    Missing version implies v2 (legacy). v3 backlogs are required to declare
    `meta.schema_version: 3` explicitly.
    """
    return int(data.get("meta", {}).get("schema_version", SCHEMA_V2))


def atomic_write(path: Path, content: str) -> None:
    """Write file atomically: write to tmp + rename. Prevents corruption on crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


_legacy_warned: set[str] = set()


def warn_legacy_layout(detail: str) -> None:
    """Emit a one-shot deprecation warning for `.claude/`-layout projects.

    Each detail tag fires once per process so a long-running server doesn't
    spam stderr. The warning points at `backlog_canonicalize_layout`, the
    existing migrator that moves the layout into canonical `.taskmaster/`.
    """
    import sys
    if detail in _legacy_warned:
        return
    _legacy_warned.add(detail)
    sys.stderr.write(
        f"taskmaster: deprecated {detail} — run `backlog_canonicalize_layout` "
        f"to migrate; .claude/ support will be removed in a future release.\n"
    )


def _resolve_artifact_root() -> Path:
    """Resolve the parent directory of backlog.yaml (and its artifact subdirs)
    from CWD, using the same priority chain as `backlog_server._resolve_paths()`.

    Why this exists (ISS-004): the writer functions take `backlog_path` and use
    `bp.parent / "<artifact>"`. The CWD-flavor reader functions (`load_lesson`,
    `list_sessions`, `recap_path`, `load_issue`, etc.) used to hard-code
    `Path(".taskmaster") / "<artifact>"` — silently diverging from the writer
    on `.claude/`-layout and root-layout projects. This helper returns the same
    parent dir the writer's resolver would, so readers and writers agree.

    Resolution order: `.taskmaster/` → `.claude/` (legacy, with warning)
    → project root → fallback `.taskmaster/`.
    """
    cwd = Path.cwd()
    if (cwd / ".taskmaster" / "backlog.yaml").exists():
        return cwd / ".taskmaster"
    if (cwd / ".claude" / "backlog.yaml").exists():
        warn_legacy_layout("artifact root at .claude/")
        return cwd / ".claude"
    if (cwd / "backlog.yaml").exists():
        return cwd
    return cwd / ".taskmaster"


# ── Markdown + YAML frontmatter ─────────────────────────────────

_FRONTMATTER_FENCE = "---"


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown document into (frontmatter dict, body str).

    A document with frontmatter looks like:

        ---
        key: value
        ---
        body text

    The opening fence must be the very first line. The closing fence is the
    next line that consists of exactly `---`. Everything after the closing
    fence is the body (leading newline stripped).

    Documents without frontmatter return ({}, original_text). Empty or
    whitespace-only frontmatter returns ({}, body). CRLF line endings are
    normalized to LF on the body side.
    """
    if not text:
        return {}, ""

    # Normalize line endings so split is consistent on Windows.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith(_FRONTMATTER_FENCE + "\n") and normalized != _FRONTMATTER_FENCE:
        return {}, normalized

    # Find closing fence
    lines = normalized.split("\n")
    if lines[0] != _FRONTMATTER_FENCE:
        return {}, normalized
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i] == _FRONTMATTER_FENCE:
            close_idx = i
            break
    if close_idx is None:
        # No closing fence — treat whole thing as body.
        return {}, normalized

    fm_text = "\n".join(lines[1:close_idx])
    body = "\n".join(lines[close_idx + 1 :])
    # Drop a single leading newline that often follows the closing fence.
    if body.startswith("\n"):
        body = body[1:]

    if not fm_text.strip():
        return {}, body

    parsed = yaml.safe_load(fm_text) or {}
    if not isinstance(parsed, dict):
        # Frontmatter must be a mapping; anything else is malformed input.
        raise ValueError("Frontmatter must be a YAML mapping")
    return parsed, body


def render_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    """Render a (frontmatter, body) pair as a markdown document.

    Empty frontmatter dict produces a body-only document (no fences).
    Body is normalized to end with exactly one trailing newline.
    """
    body_norm = body.rstrip("\n") + "\n" if body else ""
    if not frontmatter:
        return body_norm
    fm_text = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False, allow_unicode=True)
    fm_text = fm_text.rstrip("\n")
    return f"{_FRONTMATTER_FENCE}\n{fm_text}\n{_FRONTMATTER_FENCE}\n{body_norm}"


# ── Task file I/O ──────────────────────────────────────────────


def read_task_file(path: Path) -> tuple[dict[str, Any], str]:
    """Read a v3 task file at `path`. Returns (frontmatter, body)."""
    return parse_frontmatter(path.read_text(encoding="utf-8"))


def write_task_file(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    """Write a v3 task file atomically."""
    atomic_write(path, render_frontmatter(frontmatter, body))


# ── v3 layout: load + save ─────────────────────────────────────

# Fields that move OUT of backlog.yaml's task entries into per-task files
# (frontmatter). Everything else stays in the slim index.
HEAVY_FIELDS: tuple[str, ...] = (
    "description",
    "notes",
    "docs",
    "review_instructions",
)

# Special key on in-memory task dicts holding the markdown body of the
# per-task file (the prose sections written by users / skills). Not persisted
# to backlog.yaml; survives load/save roundtrip.
BODY_KEY = "_body"


def task_file_path(backlog_path: Path, task_id: str) -> Path:
    """Resolve the per-task file path given the backlog.yaml path.

    Both live in the same parent directory (e.g. .taskmaster/), so a backlog
    at .taskmaster/backlog.yaml resolves to .taskmaster/tasks/<id>.md.
    """
    return backlog_path.parent / "tasks" / f"{task_id}.md"


def _split_task_for_v3(task: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Split an in-memory task dict into (slim, heavy_fm, body).

    - slim: stays in backlog.yaml. Always includes id + title.
    - heavy_fm: frontmatter fields for the per-task file (id mirrored for sanity).
    - body: markdown body for the per-task file.
    """
    slim: dict[str, Any] = {}
    heavy: dict[str, Any] = {}
    body = ""
    for key, value in task.items():
        if key == BODY_KEY:
            body = value or ""
        elif key in HEAVY_FIELDS:
            if value not in (None, "", [], {}):
                heavy[key] = value
        else:
            slim[key] = value
    # Always mirror id+title into frontmatter for human readability of the file.
    if "id" in slim:
        heavy.setdefault("id", slim["id"])
    if "title" in slim:
        heavy.setdefault("title", slim["title"])
    return slim, heavy, body


def _merge_task_from_v3(slim: dict[str, Any], heavy_fm: dict[str, Any], body: str) -> dict[str, Any]:
    """Reverse of _split_task_for_v3: combine slim + frontmatter + body into a task dict."""
    merged = dict(slim)
    for key in HEAVY_FIELDS:
        if key in heavy_fm:
            merged[key] = heavy_fm[key]
    if body:
        merged[BODY_KEY] = body
    return merged


def load_v3(backlog_path: Path) -> dict[str, Any]:
    """Load a v3 backlog: slim index + per-task files merged.

    Returns the same dict shape as the v2 loader (epics[].tasks[] with all
    fields present in-memory), so existing read code keeps working unchanged.

    Per-task files that don't exist yet are tolerated — that task simply has
    no heavy fields (it was created in v3 mode and hasn't been edited yet).
    """
    data = yaml.safe_load(backlog_path.read_text(encoding="utf-8")) or {}
    for epic in data.get("epics", []):
        new_tasks: list[dict[str, Any]] = []
        for slim_task in epic.get("tasks", []):
            tid = slim_task.get("id")
            if not tid:
                new_tasks.append(slim_task)
                continue
            tf = task_file_path(backlog_path, tid)
            if tf.exists():
                fm, body = read_task_file(tf)
                new_tasks.append(_merge_task_from_v3(slim_task, fm, body))
            else:
                new_tasks.append(slim_task)
        epic["tasks"] = new_tasks
    return data


def migrate_v2_to_v3(backlog_path: Path) -> dict[str, Any]:
    """Convert a v2 backlog at `backlog_path` to v3 in place.

    - Reads the v2 single-file backlog.
    - Sets `meta.schema_version = 3`.
    - Calls save_v3, which strips HEAVY_FIELDS into per-task files and
      writes the slim index back to backlog.yaml.

    Idempotent: re-running on a v3 backlog returns a 'no-op' summary.

    Returns:
        Summary dict with keys:
          - status: "migrated" | "already_v3"
          - tasks_total: int
          - task_files_written: list[str] (relative paths)
          - schema_before / schema_after
    """
    raw = yaml.safe_load(backlog_path.read_text(encoding="utf-8")) or {}
    before = detect_schema_version(raw)
    if before >= SCHEMA_V3:
        return {
            "status": "already_v3",
            "tasks_total": sum(len(e.get("tasks", [])) for e in raw.get("epics", [])),
            "task_files_written": [],
            "schema_before": before,
            "schema_after": before,
        }

    raw.setdefault("meta", {})["schema_version"] = SCHEMA_V3

    # Determine which task files will get written so we can report them.
    files_to_write: list[Path] = []
    for epic in raw.get("epics", []):
        for task in epic.get("tasks", []):
            tid = task.get("id")
            if not tid:
                continue
            _, heavy_fm, body = _split_task_for_v3(task)
            has_heavy = any(k in heavy_fm for k in HEAVY_FIELDS) or bool(body)
            if has_heavy:
                files_to_write.append(task_file_path(backlog_path, tid))

    save_v3(backlog_path, raw)

    return {
        "status": "migrated",
        "tasks_total": sum(len(e.get("tasks", [])) for e in raw.get("epics", [])),
        "task_files_written": [str(p.relative_to(backlog_path.parent)) for p in files_to_write],
        "schema_before": before,
        "schema_after": SCHEMA_V3,
    }


# ── v3 layout canonicalization (.claude/ or root → .taskmaster/) ─────────

# Items the canonicalizer moves. Anything outside this list is left alone —
# .claude/ in particular holds Claude Code's own files (settings.json, hooks/,
# etc.) which must never be touched.
_CANONICALIZE_ITEMS: tuple[str, ...] = (
    "backlog.yaml",
    "PROGRESS.md",
    "viewer.json",
    "tasks",
    "handovers",
    "lessons",
    "issues",
    "recaps",
    "snapshots",
    "auto",
)


def _detect_layout_sources(project_root: Path) -> list[tuple[str, Path]]:
    """Return all layouts that hold a backlog.yaml. Order: claude, canonical, root."""
    found: list[tuple[str, Path]] = []
    if (project_root / ".claude" / "backlog.yaml").exists():
        found.append(("claude", project_root / ".claude"))
    if (project_root / ".taskmaster" / "backlog.yaml").exists():
        found.append(("canonical", project_root / ".taskmaster"))
    if (project_root / "backlog.yaml").exists():
        found.append(("root", project_root))
    return found


def _enumerate_moves(
    source_dir: Path, canonical_dir: Path, project_root: Path
) -> list[tuple[Path, Path]]:
    """Walk every file under each known artifact item and pair with its canonical
    target. Directories aren't moved as wholes — we descend so that auto/ and
    other dirs that already exist at the destination merge cleanly.
    """
    moves: list[tuple[Path, Path]] = []
    for item in _CANONICALIZE_ITEMS:
        src = source_dir / item
        if not src.exists():
            continue
        if src.is_file():
            moves.append((src, canonical_dir / item))
            continue
        for sub in src.rglob("*"):
            if sub.is_file():
                rel = sub.relative_to(src)
                moves.append((sub, canonical_dir / item / rel))
    return moves


def canonicalize_layout(
    project_root: Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate a v3 backlog from `.claude/` or root layout into canonical `.taskmaster/`.

    Moves only the artifact items in `_CANONICALIZE_ITEMS` — other files in
    `.claude/` (Claude Code's own settings.json, hooks/, etc.) are untouched.

    Behavior:
      - Idempotent: re-running on an already-canonical layout is a no-op.
      - Refuses to clobber: a destination file with different content from the
        source aborts the migration with a `conflicts` summary; nothing moves.
      - When source and destination already hold the same bytes, the source
        copy is removed (cleanup of a partially-completed prior run).
      - `.taskmaster/auto/` may already exist (e.g. from before the migrator
        landed); enumeration is per-file so the merge is automatic.
      - `.claude/taskmaster.json` is deleted after a successful migration —
        the config it held (`backlog_path`, `progress_path`) is now redundant.

    Returns a summary dict with keys: status, source, destination, moved,
    skipped_already_at_dst, conflicts, deleted_config, removed_source_dir,
    dry_run. `status` is one of: no_backlog, already_canonical, ambiguous,
    conflicts, would_migrate (dry_run), migrated.
    """
    project_root = Path(project_root).resolve()
    canonical_dir = project_root / ".taskmaster"

    summary: dict[str, Any] = {
        "status": None,
        "source": None,
        "destination": str(canonical_dir),
        "moved": [],
        "skipped_already_at_dst": [],
        "conflicts": [],
        "deleted_config": None,
        "removed_source_dir": None,
        "dry_run": dry_run,
    }

    sources = _detect_layout_sources(project_root)
    if not sources:
        summary["status"] = "no_backlog"
        return summary

    legacy = [(kind, path) for kind, path in sources if kind != "canonical"]
    canonical_present = any(kind == "canonical" for kind, _ in sources)

    if canonical_present and not legacy:
        summary["status"] = "already_canonical"
        return summary

    if len(legacy) > 1:
        summary["status"] = "ambiguous"
        summary["sources_found"] = [k for k, _ in legacy]
        return summary

    if canonical_present and legacy:
        # Both layouts hold a backlog.yaml — refuse, the caller has to pick one.
        summary["status"] = "ambiguous"
        summary["sources_found"] = ["canonical"] + [k for k, _ in legacy]
        return summary

    source_kind, source_dir = legacy[0]
    summary["source"] = source_kind

    moves = _enumerate_moves(source_dir, canonical_dir, project_root)

    # Conflict + already-at-dst pass.
    plan: list[tuple[Path, Path]] = []
    for src, dst in moves:
        if dst.exists():
            try:
                same = src.read_bytes() == dst.read_bytes()
            except OSError:
                same = False
            rel_dst = str(dst.relative_to(project_root))
            if same:
                summary["skipped_already_at_dst"].append(rel_dst)
            else:
                summary["conflicts"].append({
                    "src": str(src.relative_to(project_root)),
                    "dst": rel_dst,
                })
        else:
            plan.append((src, dst))

    if summary["conflicts"]:
        summary["status"] = "conflicts"
        return summary

    if dry_run:
        summary["status"] = "would_migrate"
        summary["would_move"] = [
            {
                "src": str(s.relative_to(project_root)),
                "dst": str(d.relative_to(project_root)),
            }
            for s, d in plan
        ]
        return summary

    # Execute: move new files, delete duplicates already at dst.
    for src, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(src), str(dst))
        summary["moved"].append({
            "src": str(src.relative_to(project_root)),
            "dst": str(dst.relative_to(project_root)),
        })
    for rel_dst in summary["skipped_already_at_dst"]:
        src_path = source_dir / Path(rel_dst).relative_to(canonical_dir.relative_to(project_root))
        if src_path.exists() and src_path.is_file():
            src_path.unlink()

    # Cleanup: remove now-empty artifact subdirs in source, then the source dir
    # itself if it still has only foreign content (or none).
    for item in _CANONICALIZE_ITEMS:
        d = source_dir / item
        if d.exists() and d.is_dir():
            for child in sorted(d.rglob("*"), reverse=True):
                if child.is_dir():
                    try:
                        child.rmdir()
                    except OSError:
                        pass
            try:
                d.rmdir()
            except OSError:
                pass

    if source_kind == "claude":
        config = source_dir / "taskmaster.json"
        if config.exists():
            config.unlink()
            summary["deleted_config"] = str(config.relative_to(project_root))
        # Don't try to remove .claude/ itself — Claude Code keeps settings here.
    elif source_kind == "root":
        # Root layout had no wrapping dir; nothing more to clean up.
        pass

    summary["status"] = "migrated"
    return summary


# ── Snapshots (for recap diff) ─────────────────────────────────

# Snapshot fields tracked per task. Keep this slim — adding fields
# bloats every snapshot file. Issues join later in step 4.
_SNAPSHOT_TASK_FIELDS = ("status", "priority", "stage")


def snapshot_path(backlog_path: Path) -> Path:
    """Resolve the snapshot file path. Lives next to the backlog under snapshots/."""
    return backlog_path.parent / "snapshots" / "last.json"


def take_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    """Build a slim snapshot dict from in-memory backlog data.

    The snapshot captures only the fields needed for `recap` to show what
    changed since last session — not the full backlog content. The
    `structural_hash` is a quick check for 'did anything change at all'.
    """
    tasks: dict[str, dict[str, Any]] = {}
    for epic in data.get("epics", []):
        for t in epic.get("tasks", []):
            tid = t.get("id")
            if not tid:
                continue
            entry = {f: t.get(f) for f in _SNAPSHOT_TASK_FIELDS if t.get(f) is not None}
            # Track epic membership too — moves between epics show up in recap.
            entry["epic"] = epic.get("id")
            entry["title"] = t.get("title", "")
            tasks[tid] = entry

    phase_active = None
    for p in data.get("phases", []) or []:
        if p.get("status") == "active":
            phase_active = p.get("id")
            break

    payload_for_hash = json.dumps({"tasks": tasks, "phase_active": phase_active}, sort_keys=True)
    return {
        "taken_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": SCHEMA_V3,
        "structural_hash": "sha256:" + hashlib.sha256(payload_for_hash.encode("utf-8")).hexdigest(),
        "tasks": tasks,
        "phase_active": phase_active,
    }


def write_snapshot(backlog_path: Path, snapshot: dict[str, Any]) -> Path:
    """Persist a snapshot atomically to .taskmaster/snapshots/last.json."""
    sp = snapshot_path(backlog_path)
    atomic_write(sp, json.dumps(snapshot, indent=2) + "\n")
    return sp


def read_snapshot(backlog_path: Path) -> dict[str, Any] | None:
    """Read the latest snapshot if present, else None."""
    sp = snapshot_path(backlog_path)
    if not sp.exists():
        return None
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ── Recap (diff against last snapshot) ─────────────────────────


def diff_against_snapshot(
    current_data: dict[str, Any],
    prev_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute the recap diff between current backlog and the previous snapshot.

    Returns a structured dict with task add/remove/change groups and a phase
    change marker. Designed to be cheap to render as a few lines of text.

    With no prior snapshot (first run), returns the 'no_prior_snapshot' shape
    so the caller can render an appropriate first-time message rather than
    pretending nothing changed.
    """
    current_snap = take_snapshot(current_data)

    if prev_snapshot is None:
        return {
            "no_prior_snapshot": True,
            "current_taken_at": current_snap["taken_at"],
            "tasks_added": [],
            "tasks_removed": [],
            "tasks_changed": [],
            "phase_changed": None,
            "no_changes": False,
        }

    if prev_snapshot.get("structural_hash") == current_snap["structural_hash"]:
        return {
            "no_prior_snapshot": False,
            "no_changes": True,
            "prev_taken_at": prev_snapshot.get("taken_at"),
            "current_taken_at": current_snap["taken_at"],
            "tasks_added": [],
            "tasks_removed": [],
            "tasks_changed": [],
            "phase_changed": None,
        }

    prev_tasks: dict[str, dict[str, Any]] = prev_snapshot.get("tasks", {}) or {}
    cur_tasks: dict[str, dict[str, Any]] = current_snap.get("tasks", {}) or {}

    added_ids = sorted(set(cur_tasks) - set(prev_tasks))
    removed_ids = sorted(set(prev_tasks) - set(cur_tasks))
    common_ids = set(cur_tasks) & set(prev_tasks)

    tasks_added = [
        {
            "id": tid,
            "title": cur_tasks[tid].get("title", ""),
            "status": cur_tasks[tid].get("status"),
            "priority": cur_tasks[tid].get("priority"),
            "epic": cur_tasks[tid].get("epic"),
        }
        for tid in added_ids
    ]
    tasks_removed = [
        {
            "id": tid,
            "title": prev_tasks[tid].get("title", ""),
            "epic": prev_tasks[tid].get("epic"),
        }
        for tid in removed_ids
    ]

    tracked_fields = (*_SNAPSHOT_TASK_FIELDS, "epic")
    tasks_changed = []
    for tid in sorted(common_ids):
        before = prev_tasks[tid]
        after = cur_tasks[tid]
        changes = [
            {"field": f, "before": before.get(f), "after": after.get(f)}
            for f in tracked_fields
            if before.get(f) != after.get(f)
        ]
        if changes:
            tasks_changed.append(
                {
                    "id": tid,
                    "title": after.get("title", before.get("title", "")),
                    "changes": changes,
                }
            )

    phase_before = prev_snapshot.get("phase_active")
    phase_after = current_snap["phase_active"]
    phase_changed = (
        {"before": phase_before, "after": phase_after}
        if phase_before != phase_after
        else None
    )

    return {
        "no_prior_snapshot": False,
        "no_changes": False,
        "prev_taken_at": prev_snapshot.get("taken_at"),
        "current_taken_at": current_snap["taken_at"],
        "tasks_added": tasks_added,
        "tasks_removed": tasks_removed,
        "tasks_changed": tasks_changed,
        "phase_changed": phase_changed,
    }


# ── Handovers ──────────────────────────────────────────────────

# Canonical session_kind values (free-form string in storage; these are
# the well-known ones that may get special treatment elsewhere).
HANDOVER_KINDS = (
    "end-of-day",
    "context-handoff",
    "milestone-complete",
    "pivot",
    "exploration",
    "auto-stage",
)

# Three-state lifecycle for handovers — see specs/2026-05-09-handover-status-design.md
HANDOVER_STATUSES = ("todo", "in-progress", "done")


def _default_handover_status(session_kind: str) -> str:
    """auto-stage handovers are bookkeeping checkpoints — born done. All other
    kinds default to todo so the user explicitly clears their backlog."""
    return "done" if session_kind == "auto-stage" else "todo"


# Index cap — `handovers:` array in backlog.yaml is bounded.
HANDOVER_INDEX_CAP = 30

# ---- Recap ---------------------------------------------------------------

RECAP_SCHEMA_VERSION = 1

# Map storage-side handover kinds to viewer-side display kinds (spec §5).
# Storage kinds live in handover frontmatter (`session_kind`); the viewer renders
# them via this mapping for kind-pill colour, kind-filter chips, and right-rail header.
HANDOVER_KIND_TO_VIEWER_KIND = {
    "end-of-day":         "wrap",
    "context-handoff":    "mid-task",
    "milestone-complete": "checkpoint",
    "pivot":              "mid-task",
    "exploration":        "standalone",
    "auto-stage":         "standalone",
}

VIEWER_HANDOVER_KINDS = ("mid-task", "checkpoint", "wrap", "standalone")


def slugify(text: str, max_len: int = 40) -> str:
    """Reduce arbitrary text to a URL-safe slug.

    Lowercase, alnum + hyphens only, collapsed runs, length-capped. Empty
    input falls back to 'untitled' so the resulting handover id is always
    a valid filename.
    """
    if not text:
        return "untitled"
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    if not s:
        return "untitled"
    return s[:max_len].rstrip("-") or "untitled"


def make_handover_id(date_str: str, tldr: str) -> str:
    """Build a handover id from a date and tldr text."""
    return f"{date_str}-{slugify(tldr)}"


def handover_path(backlog_path: Path, handover_id: str) -> Path:
    """Resolve the file path for a given handover id."""
    return backlog_path.parent / "handovers" / f"{handover_id}.md"


def handover_dir(backlog_path: Path) -> Path:
    return backlog_path.parent / "handovers"


def write_handover(
    backlog_path: Path,
    *,
    tldr: str,
    next_action: str = "",
    body: str = "",
    task_ids: list[str] | None = None,
    session_kind: str = "end-of-day",
    when: str | None = None,
    context_size_at_write: str | None = None,
    supersedes: str | None = None,
    branch: str | None = None,
    tip_commit: str | None = None,
) -> tuple[str, Path]:
    """Write a new handover file.

    Returns (handover_id, file_path). The id is `<date>-<slug-of-tldr>`. If
    a handover with the same id already exists the slug gets a numeric
    suffix to avoid clobbering same-day handovers with similar tldrs.
    """
    if not tldr or not tldr.strip():
        raise ValueError("handover tldr is required")
    if session_kind not in HANDOVER_KINDS:
        raise ValueError(
            f"session_kind must be one of {HANDOVER_KINDS}, got {session_kind!r}"
        )
    when = when or date.today().isoformat()
    base_id = make_handover_id(when, tldr)
    target = handover_path(backlog_path, base_id)
    final_id = base_id
    suffix = 2
    while target.exists():
        final_id = f"{base_id}-{suffix}"
        target = handover_path(backlog_path, final_id)
        suffix += 1

    fm: dict[str, Any] = {
        "id": final_id,
        "date": when,
        # Microsecond precision so same-second writes order deterministically.
        "created": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "tldr": tldr.strip(),
        "next_action": (next_action or "").strip(),
        "task_ids": list(task_ids or []),
        "session_kind": session_kind,
    }
    fm["status"] = _default_handover_status(session_kind)
    fm["status_changed"] = fm["created"]
    fm["status_user_set"] = False
    if context_size_at_write:
        fm["context_size_at_write"] = context_size_at_write
    if supersedes:
        fm["supersedes"] = supersedes
    if branch:
        fm["branch"] = branch
    if tip_commit:
        fm["tip_commit"] = tip_commit

    write_task_file(target, fm, body)
    return final_id, target


def read_handover(backlog_path: Path, handover_id: str) -> tuple[dict[str, Any], str]:
    """Read a handover file by id. Raises FileNotFoundError if missing."""
    return read_task_file(handover_path(backlog_path, handover_id))


def list_handover_ids(backlog_path: Path) -> list[str]:
    """List handover ids on disk, newest-first.

    Sort key per file: (id date-prefix, `created` ISO timestamp, file mtime,
    id) — descending. The id's `YYYY-MM-DD` prefix is the authoritative
    user-supplied `when=` date and leads the sort so that batch writes
    sharing a `created` timestamp still order by their intended date
    rather than falling back to alphabetical-by-slug (ISS-010). `created`
    and mtime remain as tiebreakers for same-day writes; id alpha is the
    final fallback.
    """
    d = handover_dir(backlog_path)
    if not d.exists():
        return []
    entries: list[tuple[str, str, float, str]] = []
    for p in d.glob("*.md"):
        created = ""
        try:
            fm, _ = read_task_file(p)
            created = str(fm.get("created") or "")
        except (OSError, ValueError):
            pass
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        date_prefix = p.stem[:10]
        entries.append((date_prefix, created, mtime, p.stem))
    entries.sort(reverse=True)
    return [stem for _, _, _, stem in entries]


def latest_handover_id(backlog_path: Path) -> str | None:
    ids = list_handover_ids(backlog_path)
    return ids[0] if ids else None


_SUPERSESSION_CALLOUT_RE = re.compile(
    r"^> \*\*SUPERSEDED \d{4}-\d{2}-\d{2} by \["
)


def _strip_supersession_callout(body: str) -> str:
    """Return `body` with any leading SUPERSEDED callout block stripped.

    A callout block is one or more contiguous lines starting with `>` whose
    first line matches `_SUPERSESSION_CALLOUT_RE`, optionally followed by a
    single blank line. If no callout matches, the body is returned unchanged.
    """
    if not body:
        return body
    body_lines = body.splitlines(keepends=True)
    if not body_lines or not _SUPERSESSION_CALLOUT_RE.match(body_lines[0]):
        return body
    end = 0
    while end < len(body_lines) and body_lines[end].startswith(">"):
        end += 1
    if end < len(body_lines) and body_lines[end].strip() == "":
        end += 1
    return "".join(body_lines[end:])


def apply_supersession(backlog_path: Path, *, old_id: str, new_id: str) -> Path:
    """Mark `old_id` as superseded by `new_id`.

    Edits the old handover in place:
      1. Sets `superseded_by: new_id` in the frontmatter.
      2. Prepends a callout block at the top of the body, OR rewrites the
         existing callout if one is already present (idempotent for a
         single old → many-newer chain).

    Returns the old handover's path. Raises FileNotFoundError if either id
    is missing on disk.
    """
    new_path = handover_path(backlog_path, new_id)
    if not new_path.exists():
        raise FileNotFoundError(new_id)
    old_path = handover_path(backlog_path, old_id)
    if not old_path.exists():
        raise FileNotFoundError(old_id)

    fm, body = read_handover(backlog_path, old_id)
    fm["superseded_by"] = new_id

    if not fm.get("status_user_set"):
        fm["status"] = "done"
        fm["status_changed"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        fm["status_reason"] = f"superseded by {new_id}"

    today = date.today().isoformat()
    callout = (
        f"> **SUPERSEDED {today} by [{new_id}](./{new_id}.md).**\n"
        f"> The next session should read the newer handover instead. "
        f"This file kept as a checkpoint reference.\n\n"
    )

    write_task_file(old_path, fm, callout + _strip_supersession_callout(body))
    return old_path


def apply_handover_review_flag(
    backlog_path: Path,
    *,
    handover_id: str,
    review_reason: str,
) -> Path:
    """Stamp `flag_for_review: true` + `review_reason` onto an existing handover.

    Used by `taskmaster:lesson` when a `<lesson-candidate scope="session">` is
    promoted: the active handover for the session gets flagged so a future
    invocation can retro-extract lessons against it. Idempotent — re-applying
    overwrites the `review_reason` and leaves the body untouched. Raises
    FileNotFoundError if the handover doesn't exist on disk.
    """
    target = handover_path(backlog_path, handover_id)
    if not target.exists():
        raise FileNotFoundError(handover_id)
    fm, body = read_handover(backlog_path, handover_id)
    fm["flag_for_review"] = True
    fm["review_reason"] = review_reason or ""
    write_task_file(target, fm, body)
    return target


def update_handover_status(
    backlog_path: Path,
    *,
    handover_id: str,
    status: str,
    reason: str = "",
) -> tuple[dict[str, Any], Path]:
    """Explicit user-driven status change. Sets status_user_set: true so
    subsequent auto-transitions skip this handover.

    Passing an empty `reason` preserves any existing `status_reason` rather
    than clearing it. Pass an explicit non-empty value to overwrite.

    Raises ValueError on bad enum, FileNotFoundError if missing.
    """
    if status not in HANDOVER_STATUSES:
        raise ValueError(f"status must be one of {HANDOVER_STATUSES}, got {status!r}")
    target = handover_path(backlog_path, handover_id)
    if not target.exists():
        raise FileNotFoundError(handover_id)
    fm, body = read_handover(backlog_path, handover_id)
    fm["status"] = status
    fm["status_changed"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    fm["status_user_set"] = True
    if reason:
        fm["status_reason"] = reason
    write_task_file(target, fm, body)
    return fm, target


def mark_task_handovers_complete(backlog_path: Path, task_id: str) -> list[str]:
    """Flip every todo handover whose primary task (`task_ids[0]`) is `task_id`
    to `done`. Skips user-set, already-done, and handovers where task_id is
    only a secondary reference. Returns the list of handover ids modified."""
    if not task_id:
        return []
    flipped: list[str] = []
    for hid in list_handover_ids(backlog_path):
        try:
            fm, body = read_handover(backlog_path, hid)
        except (OSError, ValueError):
            continue
        ids = fm.get("task_ids") or []
        if not ids or ids[0] != task_id:
            continue
        if fm.get("status_user_set"):
            continue
        if fm.get("status") == "done":
            continue
        fm["status"] = "done"
        fm["status_changed"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        fm["status_reason"] = f"task {task_id} completed"
        write_task_file(handover_path(backlog_path, hid), fm, body)
        flipped.append(hid)
    return flipped


def mark_task_handovers_resumed(backlog_path: Path, task_id: str) -> list[str]:
    """Flip todo handovers whose primary task is `task_id` to `in-progress`.
    Conservative: only the latest todo handover for that task is touched, so
    incidental views don't churn unrelated history. Skips user-set and any
    already-non-todo entries. Returns the list of ids modified."""
    if not task_id:
        return []
    for hid in list_handover_ids(backlog_path):  # newest-first
        try:
            fm, body = read_handover(backlog_path, hid)
        except (OSError, ValueError):
            continue
        ids = fm.get("task_ids") or []
        if not ids or ids[0] != task_id:
            continue
        if fm.get("status_user_set"):
            return []
        if fm.get("status") != "todo":
            return []  # latest is already in-progress or done — leave history alone
        fm["status"] = "in-progress"
        fm["status_changed"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        fm["status_reason"] = "resumed in session"
        write_task_file(handover_path(backlog_path, hid), fm, body)
        return [hid]
    return []


def backfill_handover_status(backlog_data: dict[str, Any], backlog_path: Path) -> list[str]:
    """One-time pass: stamp `status: done` on every handover lacking the field,
    plus archived handovers, then mark the backlog as backfilled.

    No-op if `handover_status_backfilled` is already truthy. Returns the list
    of handover ids that were modified.
    """
    if backlog_data.get("handover_status_backfilled"):
        return []
    flipped: list[str] = []
    handovers_root = handover_dir(backlog_path)
    archive_root = handovers_root / "_archive"
    candidates: list[Path] = []
    if handovers_root.exists():
        candidates.extend(p for p in handovers_root.glob("*.md"))
    if archive_root.exists():
        candidates.extend(archive_root.rglob("*.md"))
    for path in candidates:
        try:
            fm, body = read_task_file(path)
        except (OSError, ValueError):
            continue
        if "status" in fm:
            continue
        try:
            mtime_iso = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(
                timespec="microseconds"
            )
        except OSError:
            mtime_iso = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        fm["status"] = "done"
        fm["status_changed"] = mtime_iso
        fm["status_reason"] = "backfilled by handover-status migration"
        fm["status_user_set"] = False
        write_task_file(path, fm, body)
        flipped.append(path.stem)
    backlog_data["handover_status_backfilled"] = True
    return flipped


# Fields kept in the backlog.yaml `handovers:` index entry.
_HANDOVER_INDEX_FIELDS = (
    "id", "date", "created", "tldr", "next_action",
    "task_ids", "session_kind", "status",
)


def _handover_index_entry(fm: dict[str, Any]) -> dict[str, Any]:
    """Project a handover frontmatter dict to its slim index entry."""
    return {f: fm.get(f) for f in _HANDOVER_INDEX_FIELDS if fm.get(f) is not None}


def archive_handover(backlog_path: Path, handover_id: str) -> Path:
    """Move a handover file from handovers/ to handovers/_archive/<year>/.

    The year is parsed from the id prefix (YYYY-...). If the id doesn't
    follow that pattern, archive under handovers/_archive/unknown/.
    Returns the new path.
    """
    src = handover_path(backlog_path, handover_id)
    if not src.exists():
        raise FileNotFoundError(handover_id)
    year = handover_id[:4] if re.match(r"^\d{4}", handover_id) else "unknown"
    dest_dir = handover_dir(backlog_path) / "_archive" / year
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    os.replace(src, dest)
    return dest


def sync_handover_index(
    backlog_data: dict[str, Any],
    backlog_path: Path,
    cap: int = HANDOVER_INDEX_CAP,
) -> dict[str, Any]:
    """Populate backlog_data['handovers'] from disk; archive overflow.

    Reads all handover files (excluding _archive/), sorts newest-first,
    keeps the first `cap` as index entries in backlog_data, and archives
    the rest. Mutates backlog_data in place and returns it for chaining.
    """
    ids = list_handover_ids(backlog_path)
    keep_ids = ids[:cap]
    overflow_ids = ids[cap:]

    entries: list[dict[str, Any]] = []
    for hid in keep_ids:
        try:
            fm, _ = read_handover(backlog_path, hid)
        except (OSError, ValueError):
            continue
        entries.append(_handover_index_entry(fm))

    backlog_data["handovers"] = entries

    for hid in overflow_ids:
        try:
            archive_handover(backlog_path, hid)
        except (OSError, FileNotFoundError):
            continue

    return backlog_data


# ── Issues ─────────────────────────────────────────────────────

ISSUE_STATUSES = ("open", "investigating", "fixed", "wontfix", "duplicate")
ISSUE_SEVERITIES = ("P0", "P1", "P2", "P3")
_SEVERITY_RANK = {s: i for i, s in enumerate(ISSUE_SEVERITIES)}  # P0=0 most-severe

# Index entry slim metadata kept in backlog.yaml for fast dashboard render.
_ISSUE_INDEX_FIELDS = (
    "id",
    "title",
    "status",
    "severity",
    "components",
    "related_tasks",
)


def issue_path(backlog_path: Path, issue_id: str) -> Path:
    return backlog_path.parent / "issues" / f"{issue_id}.md"


def issue_dir(backlog_path: Path) -> Path:
    return backlog_path.parent / "issues"


def list_issue_ids(backlog_path: Path) -> list[str]:
    """List issue ids on disk, sorted numerically by the trailing number."""
    d = issue_dir(backlog_path)
    if not d.exists():
        return []

    def _rank(p: Path) -> int:
        m = re.search(r"(\d+)$", p.stem)
        return int(m.group(1)) if m else -1

    files = sorted(d.glob("ISS-*.md"), key=_rank)
    return [p.stem for p in files]


def next_issue_id(backlog_path: Path) -> str:
    """Allocate the next ISS-NNN id (zero-padded, 3+ digits)."""
    existing = list_issue_ids(backlog_path)
    nums = []
    for ident in existing:
        m = re.search(r"(\d+)$", ident)
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return f"ISS-{n:03d}"


DECISION_STATUSES = ("open", "resolved", "dropped")


def decision_dir(backlog_path: Path) -> Path:
    """Return the `decisions/` dir alongside backlog.yaml."""
    return backlog_path.parent / "decisions"


def list_decision_ids(backlog_path: Path) -> list[str]:
    """List decision ids on disk, sorted numerically by trailing number."""
    d = decision_dir(backlog_path)
    if not d.exists():
        return []

    def _rank(p: Path) -> int:
        m = re.search(r"(\d+)$", p.stem)
        return int(m.group(1)) if m else -1

    files = sorted(d.glob("DEC-*.md"), key=_rank)
    return [p.stem for p in files]


def next_decision_id(backlog_path: Path) -> str:
    """Allocate the next DEC-NNN id (zero-padded, 3+ digits)."""
    existing = list_decision_ids(backlog_path)
    nums = [int(re.search(r"(\d+)$", x).group(1)) for x in existing
            if re.search(r"(\d+)$", x)]
    n = (max(nums) + 1) if nums else 1
    return f"DEC-{n:03d}"


def decision_path(backlog_path: Path, decision_id: str) -> Path:
    return decision_dir(backlog_path) / f"{decision_id}.md"


def _validate_issue(fm: dict[str, Any]) -> None:
    """Raise ValueError if frontmatter violates the issue invariants."""
    status = fm.get("status")
    if status not in ISSUE_STATUSES:
        raise ValueError(f"status must be one of {ISSUE_STATUSES}, got {status!r}")
    sev = fm.get("severity")
    if sev not in ISSUE_SEVERITIES:
        raise ValueError(f"severity must be one of {ISSUE_SEVERITIES}, got {sev!r}")
    if status == "fixed" and not fm.get("fixed_in_task"):
        raise ValueError("status=fixed requires fixed_in_task to be set")
    if status == "duplicate" and not fm.get("duplicate_of"):
        raise ValueError("status=duplicate requires duplicate_of to be set")


def write_issue(
    backlog_path: Path,
    *,
    title: str,
    severity: str,
    impact: str = "",
    components: list[str] | None = None,
    location: list[str] | None = None,
    related_tasks: list[str] | None = None,
    discovered: str | None = None,
    discovered_by: str = "",
    body: str = "",
    issue_id: str | None = None,
    status: str = "open",
) -> tuple[str, Path]:
    """Create a new issue file. Returns (id, path)."""
    if not title or not title.strip():
        raise ValueError("issue title is required")
    iid = issue_id or next_issue_id(backlog_path)
    from datetime import datetime, timezone
    default_discovered = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fm: dict[str, Any] = {
        "id": iid,
        "title": title.strip(),
        "status": status,
        "severity": severity,
        "components": list(components or []),
        "impact": impact.strip(),
        "location": list(location or []),
        "discovered": discovered or default_discovered,
        "discovered_by": discovered_by,
        "resolved": None,
        "related_tasks": list(related_tasks or []),
        "fixed_in_task": None,
        "duplicate_of": None,
    }
    _validate_issue(fm)
    write_task_file(issue_path(backlog_path, iid), fm, body)
    return iid, issue_path(backlog_path, iid)


def read_issue(backlog_path: Path, issue_id: str) -> tuple[dict[str, Any], str]:
    return read_task_file(issue_path(backlog_path, issue_id))


def update_issue(
    backlog_path: Path,
    issue_id: str,
    **updates: Any,
) -> tuple[dict[str, Any], str]:
    """Apply partial updates to an issue's frontmatter, validate, and rewrite.

    Body is preserved unchanged unless `body=` is passed.
    """
    fm, body = read_issue(backlog_path, issue_id)
    new_body = updates.pop("body", body)
    fm.update({k: v for k, v in updates.items() if v is not None})
    if fm.get("status") == "fixed" and not fm.get("resolved"):
        fm["resolved"] = date.today().isoformat()
    _validate_issue(fm)
    write_task_file(issue_path(backlog_path, issue_id), fm, new_body)
    return fm, new_body


def _issue_index_entry(fm: dict[str, Any]) -> dict[str, Any]:
    return {f: fm.get(f) for f in _ISSUE_INDEX_FIELDS if fm.get(f) is not None}


def sync_issue_index(
    backlog_data: dict[str, Any],
    backlog_path: Path,
) -> dict[str, Any]:
    """Rebuild backlog_data['issues'] from disk.

    Sorted by (severity asc, id asc) so P0s float to the top of the list.
    No archive/cap — issues are bounded by reality, not policy.
    """
    entries: list[dict[str, Any]] = []
    for iid in list_issue_ids(backlog_path):
        try:
            fm, _ = read_issue(backlog_path, iid)
        except (OSError, ValueError):
            continue
        entries.append(_issue_index_entry(fm))
    entries.sort(key=lambda e: (_SEVERITY_RANK.get(e.get("severity", "P3"), 99), e.get("id", "")))
    backlog_data["issues"] = entries
    return backlog_data


# ── Lessons ────────────────────────────────────────────────────

LESSON_KINDS = ("pattern", "anti-pattern", "gotcha")
LESSON_TIERS = ("active", "core", "retired")

# Caps from the v3 spec.
LESSON_DIGEST_CAP = 30          # max 'active' tier lessons in digest
LESSON_CORE_CAP = 5             # max 'core' tier lessons (always loaded full)
LESSON_TRIGGER_MATCH_CAP = 3    # max trigger-matched lessons per pick-task

# Auto-promotion / decay thresholds.
LESSON_PROMOTE_REINFORCE = 5    # reinforce_count >= this → suggest core promotion
LESSON_DECAY_DAYS = 180         # if last_reinforced older + reinforce_count < 2 → retire
LESSON_DECAY_REINFORCE = 2      # threshold for auto-decay

_LESSON_INDEX_FIELDS = ("id", "title", "kind", "tier", "reinforce_count")


def lesson_path(backlog_path: Path, lesson_id: str) -> Path:
    return backlog_path.parent / "lessons" / f"{lesson_id}.md"


def lesson_dir(backlog_path: Path) -> Path:
    return backlog_path.parent / "lessons"


def list_lesson_ids(backlog_path: Path) -> list[str]:
    """List lesson ids on disk, sorted by trailing number."""
    d = lesson_dir(backlog_path)
    if not d.exists():
        return []

    def _rank(p: Path) -> int:
        m = re.search(r"(\d+)$", p.stem)
        return int(m.group(1)) if m else -1

    return [p.stem for p in sorted(d.glob("L-*.md"), key=_rank)]


def next_lesson_id(backlog_path: Path) -> str:
    existing = list_lesson_ids(backlog_path)
    nums = []
    for ident in existing:
        m = re.search(r"(\d+)$", ident)
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return f"L-{n:03d}"


def _validate_lesson(fm: dict[str, Any]) -> None:
    if fm.get("kind") not in LESSON_KINDS:
        raise ValueError(f"kind must be one of {LESSON_KINDS}, got {fm.get('kind')!r}")
    tier = fm.get("tier", "active")
    if tier not in LESSON_TIERS:
        raise ValueError(f"tier must be one of {LESSON_TIERS}, got {tier!r}")


def write_lesson(
    backlog_path: Path,
    *,
    title: str,
    kind: str,
    triggers: dict[str, Any] | None = None,
    body: str = "",
    tier: str = "active",
    related_tasks: list[str] | None = None,
    related_issues: list[str] | None = None,
    lesson_id: str | None = None,
) -> tuple[str, Path]:
    """Create a new lesson. Returns (id, path)."""
    if not title or not title.strip():
        raise ValueError("lesson title is required")
    lid = lesson_id or next_lesson_id(backlog_path)
    fm: dict[str, Any] = {
        "id": lid,
        "title": title.strip(),
        "kind": kind,
        "triggers": triggers or {"files": [], "task_titles_match": [], "task_kinds": []},
        "tier": tier,
        "reinforce_count": 0,
        "last_reinforced": None,
        "created": date.today().isoformat(),
        "related_tasks": list(related_tasks or []),
        "related_issues": list(related_issues or []),
    }
    _validate_lesson(fm)
    write_task_file(lesson_path(backlog_path, lid), fm, body)
    return lid, lesson_path(backlog_path, lid)


def read_lesson(backlog_path: Path, lesson_id: str) -> tuple[dict[str, Any], str]:
    return read_task_file(lesson_path(backlog_path, lesson_id))


# ── CWD-relative lesson helpers (used by MCP tools and HTTP endpoints) ────────


def _ensure_reinforce_events(lesson: dict) -> dict:
    """One-time migration: legacy lesson files don't carry reinforce_events.
    Backfill an empty list so downstream code can append unconditionally.
    Idempotent: if the key is already present, it's left untouched.
    """
    if "reinforce_events" not in lesson or lesson["reinforce_events"] is None:
        lesson["reinforce_events"] = []
    return lesson


def load_lesson(lesson_id: str) -> dict:
    """Load a lesson by id from <backlog-parent>/lessons/<id>.md in CWD.

    Returns a dict with frontmatter fields plus '_body'. Backfills
    reinforce_events if not present (migration shim).
    """
    p = _resolve_artifact_root() / "lessons" / f"{lesson_id}.md"
    fm, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    fm["_body"] = body
    _ensure_reinforce_events(fm)
    return fm


def save_lesson(lesson: dict) -> None:
    """Persist a lesson dict (with '_body') back to <backlog-parent>/lessons/<id>.md."""
    lesson_id = lesson["id"]
    p = _resolve_artifact_root() / "lessons" / f"{lesson_id}.md"
    body = lesson.pop("_body", "")
    atomic_write(p, render_frontmatter(lesson, body))
    lesson["_body"] = body


LESSON_REINFORCE_SOURCES = {"user", "claude", "skill"}


def lesson_reinforce(lesson_id: str, source: str = "user", note: str = "") -> dict:
    """Append a reinforcement event to a lesson and persist.

    Returns the updated lesson summary (frontmatter dict, including
    reinforce_count, last_reinforced, and the appended reinforce_events list).

    Raises:
        FileNotFoundError: if the lesson file doesn't exist.
        ValueError: if `source` is not in LESSON_REINFORCE_SOURCES.
    """
    if source not in LESSON_REINFORCE_SOURCES:
        raise ValueError(
            f"source must be one of {sorted(LESSON_REINFORCE_SOURCES)}, got {source!r}"
        )

    from datetime import datetime, timezone

    p = _resolve_artifact_root() / "lessons" / f"{lesson_id}.md"
    if not p.exists():
        raise FileNotFoundError(p)

    lesson = load_lesson(lesson_id)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = {"at": now_iso, "source": source, "note": note or ""}
    lesson.setdefault("reinforce_events", []).append(event)
    lesson["reinforce_count"] = int(lesson.get("reinforce_count") or 0) + 1
    lesson["last_reinforced"] = now_iso

    save_lesson(lesson)
    # Strip body from the returned summary
    summary = {k: v for k, v in lesson.items() if k != "_body"}
    return summary


def update_lesson(
    backlog_path: Path,
    lesson_id: str,
    **updates: Any,
) -> tuple[dict[str, Any], str]:
    fm, body = read_lesson(backlog_path, lesson_id)
    new_body = updates.pop("body", body)
    fm.update({k: v for k, v in updates.items() if v is not None})
    _validate_lesson(fm)
    write_task_file(lesson_path(backlog_path, lesson_id), fm, new_body)
    return fm, new_body


def reinforce_lesson(backlog_path: Path, lesson_id: str) -> dict[str, Any]:
    """Bump reinforce_count and set last_reinforced=today. Returns updated fm."""
    fm, body = read_lesson(backlog_path, lesson_id)
    fm["reinforce_count"] = int(fm.get("reinforce_count") or 0) + 1
    fm["last_reinforced"] = date.today().isoformat()
    write_task_file(lesson_path(backlog_path, lesson_id), fm, body)
    return fm


def lesson_eligible_for_promotion(fm: dict[str, Any]) -> bool:
    """Eligible for core tier? reinforce_count >= threshold, kind is gotcha/anti-pattern."""
    return (
        fm.get("tier") == "active"
        and fm.get("kind") in ("gotcha", "anti-pattern")
        and int(fm.get("reinforce_count") or 0) >= LESSON_PROMOTE_REINFORCE
    )


def lesson_eligible_for_decay(fm: dict[str, Any], today: date | None = None) -> bool:
    """Should this lesson auto-retire?

    Active tier + last_reinforced older than LESSON_DECAY_DAYS + reinforce_count below threshold.
    """
    if fm.get("tier") != "active":
        return False
    if int(fm.get("reinforce_count") or 0) >= LESSON_DECAY_REINFORCE:
        return False
    last = fm.get("last_reinforced")
    if not last:
        # Never reinforced — judge by `created` date as a proxy.
        last = fm.get("created")
    if not last:
        return False
    try:
        last_d = datetime.strptime(str(last), "%Y-%m-%d").date()
    except ValueError:
        return False
    today = today or date.today()
    return (today - last_d).days >= LESSON_DECAY_DAYS


def _glob_match(patterns: list[str], path: str) -> bool:
    """Simple glob match — supports * and **. Path-separator agnostic."""
    if not patterns:
        return False
    norm = path.replace("\\", "/")
    import fnmatch
    for pat in patterns:
        pat_norm = pat.replace("\\", "/")
        # Handle ** by translating to fnmatch-friendly: ** = match anything including /.
        if "**" in pat_norm:
            # Build regex: ** -> .*, * -> [^/]*
            regex = (
                "^"
                + re.escape(pat_norm)
                .replace(r"\*\*", ".*")
                .replace(r"\*", "[^/]*")
                .replace(r"\?", ".")
                + "$"
            )
            if re.match(regex, norm):
                return True
        elif fnmatch.fnmatch(norm, pat_norm):
            return True
    return False


def match_lessons_for_task(
    backlog_path: Path,
    task: dict[str, Any],
    touched_files: list[str] | None = None,
    cap: int = LESSON_TRIGGER_MATCH_CAP,
) -> list[tuple[dict[str, Any], str]]:
    """Find lessons whose triggers match a task's title or touched files.

    Returns up to `cap` (frontmatter, body) pairs, sorted by reinforce_count desc.
    Only `active` and `core` tier lessons are considered — retired never matches.
    """
    title = (task.get("title") or "").lower()
    files = touched_files or []
    matches: list[tuple[dict[str, Any], str, int]] = []
    for lid in list_lesson_ids(backlog_path):
        try:
            fm, body = read_lesson(backlog_path, lid)
        except (OSError, ValueError):
            continue
        if fm.get("tier") not in ("active", "core"):
            continue
        triggers = fm.get("triggers") or {}
        title_pats = triggers.get("task_titles_match") or []
        if any(p and p.lower() in title for p in title_pats):
            matches.append((fm, body, int(fm.get("reinforce_count") or 0)))
            continue
        file_pats = triggers.get("files") or []
        if any(_glob_match(file_pats, f) for f in files):
            matches.append((fm, body, int(fm.get("reinforce_count") or 0)))
    matches.sort(key=lambda m: -m[2])
    return [(fm, body) for fm, body, _ in matches[:cap]]


def lesson_digest(
    backlog_path: Path,
    cap: int = LESSON_DIGEST_CAP,
) -> list[dict[str, Any]]:
    """Return the slim digest of active-tier lessons (id+title+kind only).

    Excludes core tier (those load in full separately) and retired tier.
    Sorted by reinforce_count desc so the most-applied are first.
    """
    items: list[tuple[dict[str, Any], int]] = []
    for lid in list_lesson_ids(backlog_path):
        try:
            fm, _ = read_lesson(backlog_path, lid)
        except (OSError, ValueError):
            continue
        if fm.get("tier") != "active":
            continue
        items.append(
            (
                {"id": fm.get("id"), "title": fm.get("title"), "kind": fm.get("kind")},
                int(fm.get("reinforce_count") or 0),
            )
        )
    items.sort(key=lambda i: -i[1])
    return [d for d, _ in items[:cap]]


def core_lessons(backlog_path: Path, cap: int = LESSON_CORE_CAP) -> list[tuple[dict[str, Any], str]]:
    """Return up to `cap` core-tier lessons in full (frontmatter + body)."""
    out: list[tuple[dict[str, Any], str, int]] = []
    for lid in list_lesson_ids(backlog_path):
        try:
            fm, body = read_lesson(backlog_path, lid)
        except (OSError, ValueError):
            continue
        if fm.get("tier") == "core":
            out.append((fm, body, int(fm.get("reinforce_count") or 0)))
    out.sort(key=lambda m: -m[2])
    return [(fm, body) for fm, body, _ in out[:cap]]


def _lesson_index_entry(fm: dict[str, Any]) -> dict[str, Any]:
    return {f: fm.get(f) for f in _LESSON_INDEX_FIELDS if fm.get(f) is not None}


def sync_lesson_index(
    backlog_data: dict[str, Any],
    backlog_path: Path,
) -> dict[str, Any]:
    """Rebuild backlog_data['lessons_meta'] from disk."""
    entries: list[dict[str, Any]] = []
    for lid in list_lesson_ids(backlog_path):
        try:
            fm, _ = read_lesson(backlog_path, lid)
        except (OSError, ValueError):
            continue
        entries.append(_lesson_index_entry(fm))
    backlog_data["lessons_meta"] = entries
    return backlog_data


# ── Ideas ────────────────────────────────────────────────────

def idea_path(backlog_path: Path, idea_id: str) -> Path:
    return backlog_path.parent / "ideas" / f"{idea_id}.md"


def idea_dir(backlog_path: Path) -> Path:
    return backlog_path.parent / "ideas"


def ideas_index_path(backlog_path: Path) -> Path:
    return backlog_path.parent / "ideas" / "IDEAS.md"


def list_idea_ids(backlog_path: Path) -> list[str]:
    """List idea ids on disk, sorted numerically by trailing number."""
    d = idea_dir(backlog_path)
    if not d.exists():
        return []

    def _rank(p: Path) -> int:
        m = re.search(r"(\d+)$", p.stem)
        return int(m.group(1)) if m else -1

    files = sorted(d.glob("IDEA-*.md"), key=_rank)
    return [p.stem for p in files]


def next_idea_id(backlog_path: Path) -> str:
    """Allocate the next IDEA-NNN id (zero-padded, 3+ digits)."""
    existing = list_idea_ids(backlog_path)
    nums: list[int] = []
    for ident in existing:
        m = re.search(r"(\d+)$", ident)
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return f"IDEA-{n:03d}"


# Required frontmatter fields validated on every write.
_IDEA_REQUIRED_FIELDS = ("id", "title", "created", "created_by")


def _validate_idea(fm: dict[str, Any]) -> None:
    """Raise ValueError if frontmatter violates idea invariants.

    Idea schema is intentionally minimal — only id/title/created/created_by
    are required. Everything else is optional, freeform passthrough.
    """
    for key in _IDEA_REQUIRED_FIELDS:
        if key not in fm or fm[key] in (None, ""):
            raise ValueError(f"idea field {key!r} is required")
    if not isinstance(fm.get("title"), str) or not fm["title"].strip():
        raise ValueError("idea title must be a non-empty string")


def _idea_index_line(fm: dict[str, Any]) -> str:
    """Render one IDEAS.md line for an idea record."""
    iid = fm["id"]
    title = fm["title"]
    created = fm.get("created", "")
    # "2026-05-09T14:30:00Z" → "2026-05-09 14:30"
    short = created[:16].replace("T", " ") if isinstance(created, str) else ""
    if fm.get("archived"):
        return f"- {short} — [{iid}]({iid}.md) — ~~{title}~~ _(archived)_"
    status = fm.get("status") or ""
    suffix = f" _({status})_" if status else ""
    return f"- {short} — [{iid}]({iid}.md) — {title}{suffix}"


def _read_ideas_index(backlog_path: Path) -> list[str]:
    """Return the data lines (non-header) of IDEAS.md, newest-first preserved."""
    p = ideas_index_path(backlog_path)
    if not p.exists():
        return []
    return [line for line in p.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]


def _write_ideas_index(backlog_path: Path, lines: list[str]) -> None:
    """Write IDEAS.md with the canonical header + the supplied data lines."""
    p = ideas_index_path(backlog_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "# Ideas\n\n" + "\n".join(lines) + ("\n" if lines else "")
    atomic_write(p, body)


def _index_upsert_line(lines: list[str], idea_id: str, new_line: str) -> list[str]:
    """Replace the line for `idea_id` if present; otherwise prepend (newest first)."""
    out: list[str] = []
    found = False
    for line in lines:
        if f"[{idea_id}](" in line:
            out.append(new_line)
            found = True
        else:
            out.append(line)
    if not found:
        out.insert(0, new_line)
    return out


def write_idea(
    backlog_path: Path,
    *,
    title: str,
    body: str = "",
    tags: list[str] | None = None,
    status: str = "",
    related_tasks: list[str] | None = None,
    related_issues: list[str] | None = None,
    related_lessons: list[str] | None = None,
    created_by: str = "Claude",
    idea_id: str | None = None,
) -> tuple[str, Path]:
    """Create a new idea file. Returns (id, path).

    All fields beyond `title` are optional. `created` is auto-stamped as
    ISO-8601 UTC. Side effect: appends/updates the IDEAS.md index line.
    """
    if not title or not title.strip():
        raise ValueError("idea title is required")
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if idea_id:
        # Caller-supplied id — overwrite-friendly, single attempt.
        iid = idea_id
        target = idea_path(backlog_path, iid)
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        # Race-safe id allocation: bump-and-retry until exclusive create
        # succeeds. Two concurrent writers can't both grab the same IDEA-NNN.
        idea_dir(backlog_path).mkdir(parents=True, exist_ok=True)
        for _ in range(64):
            candidate = next_idea_id(backlog_path)
            candidate_target = idea_path(backlog_path, candidate)
            try:
                candidate_target.touch(exist_ok=False)
                iid, target = candidate, candidate_target
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("could not allocate IDEA-NNN id after 64 attempts")
    fm: dict[str, Any] = {
        "id": iid,
        "title": title.strip(),
        "created": created,
        "created_by": created_by or "Claude",
        "status": status or "",
        "tags": list(tags or []),
        "related_tasks": list(related_tasks or []),
        "related_issues": list(related_issues or []),
        "related_lessons": list(related_lessons or []),
        "promoted_to": None,
        "archived": False,
    }
    _validate_idea(fm)
    write_task_file(target, fm, body)
    lines = _index_upsert_line(_read_ideas_index(backlog_path), iid, _idea_index_line(fm))
    _write_ideas_index(backlog_path, lines)
    return iid, target


def read_idea(backlog_path: Path, idea_id: str) -> tuple[dict[str, Any], str]:
    fm, body = read_task_file(idea_path(backlog_path, idea_id))
    return fm, body.rstrip("\n")


def update_idea(
    backlog_path: Path,
    idea_id: str,
    **updates: Any,
) -> tuple[dict[str, Any], str]:
    """Patch an idea's frontmatter and/or body. Returns (fm, body) post-write.

    Body is preserved unchanged unless `body=` is passed. The IDEAS.md line
    for this idea is rewritten in place to reflect the new title / status /
    archived flag.
    """
    target = idea_path(backlog_path, idea_id)
    if not target.exists():
        raise FileNotFoundError(f"Idea not found: {idea_id}")
    fm, body = read_idea(backlog_path, idea_id)
    new_body = updates.pop("body", body)
    # Pass-through merge — accepts None values for promoted_to (un-promote).
    for k, v in updates.items():
        fm[k] = v
    _validate_idea(fm)
    write_task_file(target, fm, new_body)
    lines = _index_upsert_line(_read_ideas_index(backlog_path), idea_id, _idea_index_line(fm))
    _write_ideas_index(backlog_path, lines)
    return fm, new_body


def list_ideas(
    backlog_path: Path,
    *,
    idea_id: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    archived: bool = False,
    related_task: str | None = None,
    related_issue: str | None = None,
    related_lesson: str | None = None,
    limit: int | None = None,
    summary: bool = True,
) -> list[dict[str, Any]]:
    """List ideas with optional filters.

    Default sort is newest-first by `created`. Pass `idea_id` to fetch one
    record (body always included). Without `idea_id`, `summary=True` (the
    default) omits the body to keep list payloads small; pass `summary=False`
    to include body on every entry — useful for callers that want to render
    detail without a second fetch (e.g. the viewer).

    Filters compose as AND. `archived` defaults to False; pass True to
    include archived ideas in the result set.
    """
    if idea_id:
        target = idea_path(backlog_path, idea_id)
        if not target.exists():
            return []
        fm, body = read_idea(backlog_path, idea_id)
        return [{**fm, "body": body}]

    out: list[dict[str, Any]] = []
    for iid in list_idea_ids(backlog_path):
        try:
            fm, body = read_idea(backlog_path, iid)
        except (OSError, ValueError):
            continue
        if not archived and fm.get("archived"):
            continue
        if status is not None and (fm.get("status") or "") != status:
            continue
        if tag is not None and tag not in (fm.get("tags") or []):
            continue
        if related_task is not None and related_task not in (fm.get("related_tasks") or []):
            continue
        if related_issue is not None and related_issue not in (fm.get("related_issues") or []):
            continue
        if related_lesson is not None and related_lesson not in (fm.get("related_lessons") or []):
            continue
        if summary:
            out.append(fm)
        else:
            out.append({**fm, "body": body})

    def _sort_key(e: dict[str, Any]) -> tuple[str, int]:
        iid = e.get("id", "")
        m = re.search(r"(\d+)$", iid)
        num = int(m.group(1)) if m else 0
        return (e.get("created", ""), num)

    out.sort(key=_sort_key, reverse=True)
    if limit is not None:
        out = out[: max(0, limit)]
    return out


# ── Lesson candidates (deferred + scanning) ───────────────────

LESSON_CANDIDATE_KINDS = ("pattern", "anti-pattern", "gotcha")
LESSON_CANDIDATE_SCOPES = ("point", "session")

_LESSON_CANDIDATES_HEADER = (
    "# Lesson Candidates (deferred)\n\n"
    "> Auto-managed by `taskmaster:lesson`. "
    "Edit by hand only if the file is corrupt.\n\n"
)
_LESSON_CANDIDATES_FENCE_OPEN = "```yaml\n"
_LESSON_CANDIDATES_FENCE_CLOSE = "```\n"


def lesson_candidates_path(backlog_path: Path) -> Path:
    """Path to the `_candidates.md` file under the lessons directory."""
    return backlog_path.parent / "lessons" / "_candidates.md"


def lesson_candidates_read(backlog_path: Path) -> list[dict[str, Any]]:
    """Return the deferred candidates list, or [] if the file is missing/empty.

    The file format is a markdown header followed by a fenced YAML block with
    a `candidates:` list. Anything outside the fenced block is ignored.
    Tolerates a missing or malformed file by returning [].
    """
    p = lesson_candidates_path(backlog_path)
    if not p.exists():
        return []
    raw = p.read_text(encoding="utf-8")
    m = re.search(r"```yaml\n(.*?)```", raw, re.DOTALL)
    if not m:
        return []
    try:
        doc = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return []
    items = doc.get("candidates") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def _write_lesson_candidates(backlog_path: Path, items: list[dict[str, Any]]) -> None:
    """Render the candidates list back to disk as the canonical markdown+YAML file."""
    p = lesson_candidates_path(backlog_path)
    if not items:
        if p.exists():
            p.unlink()
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    yaml_text = yaml.safe_dump(
        {"candidates": items}, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
    body = (
        _LESSON_CANDIDATES_HEADER
        + _LESSON_CANDIDATES_FENCE_OPEN
        + yaml_text
        + _LESSON_CANDIDATES_FENCE_CLOSE
    )
    atomic_write(p, body)


def lesson_candidates_defer(
    backlog_path: Path,
    *,
    title: str,
    kind: str = "",
    topic: str = "",
    scope: str = "point",
    context: str = "",
) -> int:
    """Append a new candidate. Returns the new entry's 0-based list index.

    Validates kind (if provided) and scope. Empty `kind` is allowed — it lets
    the user defer a candidate before classifying it.
    """
    if not title or not title.strip():
        raise ValueError("candidate title is required")
    if kind and kind not in LESSON_CANDIDATE_KINDS:
        raise ValueError(
            f"kind must be one of {LESSON_CANDIDATE_KINDS}, got {kind!r}"
        )
    if scope not in LESSON_CANDIDATE_SCOPES:
        raise ValueError(
            f"scope must be one of {LESSON_CANDIDATE_SCOPES}, got {scope!r}"
        )

    items = lesson_candidates_read(backlog_path)
    entry: dict[str, Any] = {
        "title": title.strip(),
        "kind": kind,
        "topic": topic,
        "scope": scope,
        "context": context,
        "deferred_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
    }
    items.append(entry)
    _write_lesson_candidates(backlog_path, items)
    return len(items) - 1


def lesson_candidates_clear(backlog_path: Path, *, indices: list[int]) -> int:
    """Drop the entries at `indices` (0-based). Returns the number actually removed.

    Out-of-range indices are silently ignored. Re-writes the file with the
    remaining entries; deletes the file if the list is now empty.
    """
    items = lesson_candidates_read(backlog_path)
    if not items:
        return 0
    drop = {i for i in indices if 0 <= i < len(items)}
    if not drop:
        return 0
    remaining = [it for idx, it in enumerate(items) if idx not in drop]
    _write_lesson_candidates(backlog_path, remaining)
    return len(drop)


# Matches <lesson-candidate> and <lesson-candidate key="val" ...> forms.
# Attrless form is supported (test coverage requires it). Attrs must use
# double-quoted values per spec §3.2 grep convention.
_LESSON_CANDIDATE_RE = re.compile(
    r"<lesson-candidate"                    # opening tag
    r"(?P<attrs>(?:\s+\w+=\"[^\"]*\")*)"   # 0+ key="value" attribute pairs
    r"\s*>"                                  # > terminator (allow attrless)
    r"(?P<body>.*?)"                         # body, non-greedy
    r"</lesson-candidate>",                  # closing tag
    re.DOTALL,
)

_LESSON_CANDIDATE_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def _parse_candidate_attrs(attr_text: str) -> dict[str, str]:
    return {k: v for k, v in _LESSON_CANDIDATE_ATTR_RE.findall(attr_text or "")}


def _extract_jsonl_text(obj: Any) -> str:
    """Pull text from a Claude Code JSONL line (real format) or a flat
    `{"content": "..."}` test fixture. Returns "" when there's no text to scan.
    """
    message = obj.get("message") if isinstance(obj, dict) else None
    if isinstance(message, dict) and "content" in message:
        content = message["content"]
    elif isinstance(obj, dict):
        content = obj.get("content", "")
    else:
        return ""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    if isinstance(content, str):
        return content
    return ""


def scan_transcripts_for_candidates(
    project_dir: Path,
    *,
    days: int = 7,
    kind_filter: str = "",
) -> list[dict[str, Any]]:
    """Grep all `*.jsonl` files in `project_dir` for `<lesson-candidate>` tags.

    Each line of a JSONL file is decoded as JSON; the `content` field (a str)
    is searched with `_LESSON_CANDIDATE_RE`. Malformed lines are skipped.

    Filters:
      - `days`: only files whose mtime is within the last N days.
      - `kind_filter`: only matches whose `kind` attr equals this string.

    Returns a list of dicts with keys: `kind`, `topic`, `scope`, `body`,
    `source_file`, `source_line`. `source_line` is the 1-based line number
    in the `.jsonl` file where the match was found.
    """
    if not project_dir.exists() or not project_dir.is_dir():
        return []
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    out: list[dict[str, Any]] = []
    for jsonl in sorted(project_dir.glob("*.jsonl")):
        try:
            if jsonl.stat().st_mtime < cutoff_ts:
                continue
            raw_lines = jsonl.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, raw in enumerate(raw_lines, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            content = _extract_jsonl_text(obj)
            if not content:
                continue
            for m in _LESSON_CANDIDATE_RE.finditer(content):
                attrs = _parse_candidate_attrs(m.group("attrs"))
                kind = attrs.get("kind", "")
                if kind_filter and kind != kind_filter:
                    continue
                out.append({
                    "kind": kind,
                    "topic": attrs.get("topic", ""),
                    "scope": attrs.get("scope", "point"),
                    "body": m.group("body").strip(),
                    "source_file": str(jsonl),
                    "source_line": line_no,
                })
    return out


# ── Auto mode (state machine) ──────────────────────────────────

AUTO_MODES = ("task", "epic", "phase")
AUTO_STAGES = (
    "PICK",
    "SPEC_REVIEW",
    "WRITE_TESTS",
    "IMPLEMENT",
    "TEST",
    "REVIEW_GATE",
    "HANDOVER_STUB",
    "END_SESSION",
    "COMPLETE",
)
AUTO_TASK_STATUSES = ("done", "failed", "blocked")
AUTO_FAIL_REASONS = ("tests-failed", "spec-rejected", "blocked", "crashed", "user-aborted")
AUTO_MODELS = ("sonnet", "opus")

# ---- Auto-mode storage layout -------------------------------------------

AUTO_DIR = Path(".taskmaster") / "auto"
AUTO_SESSIONS_DIR = AUTO_DIR / "sessions"
AUTO_HOOKS_LOG = AUTO_DIR / "hooks.jsonl"
AUTO_LEGACY_STATE = AUTO_DIR / "state.json"  # pre-Plan-6 single-session file


def auto_session_path(sid: str) -> Path:
    return AUTO_SESSIONS_DIR / f"{sid}.json"


def auto_events_path(sid: str) -> Path:
    return AUTO_SESSIONS_DIR / f"{sid}.events.jsonl"


def save_auto_session(sid: str, state: dict) -> None:
    import json
    p = auto_session_path(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(state, indent=2))


def load_auto_session(sid: str) -> "dict | None":
    import json
    p = auto_session_path(sid)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def list_auto_sessions() -> "list[dict]":
    """Return all sessions, newest started_at first. Skips malformed files."""
    import json
    out = []
    if not AUTO_SESSIONS_DIR.exists():
        return out
    for p in AUTO_SESSIONS_DIR.glob("*.json"):
        if p.name.endswith(".events.jsonl"):
            continue
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    out.sort(key=lambda s: s.get("started_at", ""), reverse=True)
    return out


def append_auto_event(sid: str, event: dict) -> None:
    """Append a single event to <sid>.events.jsonl. Creates parent dirs."""
    import json
    p = auto_events_path(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, separators=(",", ":")) + "\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(line)


def read_auto_events(sid: str, since: "str | None" = None) -> "list[dict]":
    """Return events for sid, optionally filtered to ts >= since (ISO 8601 strings).
    Lex order on ISO 8601 UTC matches chronological order, so a string compare suffices.
    """
    import json
    p = auto_events_path(sid)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if since is not None and ev.get("ts", "") < since:
            continue
        out.append(ev)
    return out


def compute_budget(state: dict) -> dict:
    """Compute pct + tier for each meter. Tier: ok < 0.6 <= warn < 0.9 <= crit."""
    raw = state.get("budget") or {}
    out = {}
    for key, meter in raw.items():
        used = meter.get("used", 0)
        limit = meter.get("limit", 0) or 1
        pct = used / limit if limit else 0
        if pct >= 0.9:
            tier = "crit"
        elif pct >= 0.6:
            tier = "warn"
        else:
            tier = "ok"
        out[key] = {"used": used, "limit": limit, "pct": pct, "tier": tier}
    return out


def read_hook_events(sid: str) -> "dict[str, int]":
    """Return {hook_name: count} for events tagged with the given session_id.

    Tolerates malformed lines silently (skip). Missing file → {}.
    """
    import json
    if not AUTO_HOOKS_LOG.exists():
        return {}
    counts: "dict[str, int]" = {}
    for line in AUTO_HOOKS_LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("session_id") != sid:
            continue
        h = ev.get("hook")
        if not h:
            continue
        counts[h] = counts.get(h, 0) + 1
    return counts


def migrate_auto_state_to_sessions() -> bool:
    """One-time migration: wrap pre-Plan-6 single state.json into sessions/<sid>.json.

    Returns True if a migration ran, False if nothing to do. Idempotent.
    Synthesizes a session_id from `target` + compact `started_at` for run-level
    states that predate Plan 6 (mode=epic|phase, no top-level task_id).
    """
    import json
    if not AUTO_LEGACY_STATE.exists():
        return False
    raw = json.loads(AUTO_LEGACY_STATE.read_text())
    sid = raw.get("session_id") or raw.get("task_id")
    if not sid:
        target = raw.get("target") or "legacy"
        compact = (raw.get("started_at") or "").replace(":", "").replace("-", "")
        sid = f"{target}-{compact[:13]}" if compact else target
    state = dict(raw)
    state.setdefault("session_id", sid)
    cur = state.get("cursor") or {}
    state.setdefault("task_id", cur.get("task_id") or sid)
    save_auto_session(sid, state)
    audit = AUTO_LEGACY_STATE.with_name("state.legacy.json")
    AUTO_LEGACY_STATE.rename(audit)
    return True


# ── ViewerPrefs ────────────────────────────────────────────────

VIEWER_PREFS_SCHEMA_VERSION = 1

VIEWER_PREFS_DEFAULTS = {
    "schema_version": VIEWER_PREFS_SCHEMA_VERSION,
    "use_v3": False,          # serve v3 viewer shell at root when True
    "theme": "dark",          # dark | light
    "card_density": "full",   # full | minimal
    "zoom": 1.0,              # 1.5x baked into source CSS as of T2.24α
    "screens": {
        # Per-screen view toggles (Variant A / B). Default A everywhere except dashboard which has no B.
        "task_detail": {"view": "A"},
        "kanban":      {"view": "A"},
        "sessions":    {"view": "A"},   # diary | lanes | by_task; "A" maps to diary
        "lessons":     {"view": "A"},   # shelves | flat | by_anchor
        "issues":      {"view": "A"},   # hybrid | kanban | list
        "auto_mode":   {"view": "A"},   # spine | log
    },
    "dashboard": {
        # Widget catalog. Each entry: {id, type, size: small|medium|wide, rail: left|right|bottom, index: int}
        "layout": [],
    },
    "ui": {
        "sidebar_collapsed": False,   # icons-only sidebar when True
    },
    "kanban": {
        "filters": {           # last applied; restored on viewer open
            "priorities": [],
            "epics": [],
            "phase": None,
            "group_by": "status",
            "sort": {"by": "priority", "dir": "desc"},
            "search": "",
        },
        "collapsed_columns": [],   # column keys (status / phase id / epic id) currently collapsed
    },
    "lessons": {
        "thresholds": {
            "core_count": 7,
            "core_window_days": 60,
            "core_recency_days": 14,
            "retired_after_days": 30,
        },
    },
    "issues": {
        "aging": {             # base period in days, severity-tiered
            "Critical": 14,
            "High": 30,
            "Medium": 60,
            "Low": 120,
        },
    },
}


def viewer_prefs_path() -> Path:
    return _resolve_artifact_root() / "viewer.json"

def load_viewer_prefs() -> dict:
    """Load viewer prefs, creating the file with defaults on first call.
    Unknown top-level keys are preserved across reads (forward-compat).
    Missing keys are filled from VIEWER_PREFS_DEFAULTS (deep-merged).
    """
    import json
    from copy import deepcopy
    p = viewer_prefs_path()
    if not p.exists():
        prefs = deepcopy(VIEWER_PREFS_DEFAULTS)
        atomic_write(p, json.dumps(prefs, indent=2))
        return prefs
    raw = json.loads(p.read_text())

    # Deep-merge defaults under the loaded data so missing nested keys appear.
    def _merge(default, loaded):
        if isinstance(default, dict) and isinstance(loaded, dict):
            out = dict(loaded)  # preserve unknown keys
            for k, v in default.items():
                if k not in out:
                    out[k] = deepcopy(v)
                else:
                    out[k] = _merge(v, out[k])
            return out
        return loaded

    return _merge(VIEWER_PREFS_DEFAULTS, raw)

def save_viewer_prefs(prefs: dict) -> None:
    import json
    p = viewer_prefs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(prefs, indent=2))


def auto_sessions_dir(backlog_path: Path) -> Path:
    """Sessions directory anchored on the project's backlog path.
    Used by the bp-aware writers/readers in this module so the MCP-driven
    auto-run state lands where the HTTP server's /api/auto/state and
    /api/auto/sessions endpoints already look.
    """
    return backlog_path.parent / "auto" / "sessions"


def auto_session_path_bp(backlog_path: Path, sid: str) -> Path:
    return auto_sessions_dir(backlog_path) / f"{sid}.json"


def auto_events_path_bp(backlog_path: Path, sid: str) -> Path:
    return auto_sessions_dir(backlog_path) / f"{sid}.events.jsonl"


def append_auto_event_bp(backlog_path: Path, sid: str, event: dict) -> None:
    p = auto_events_path_bp(backlog_path, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, separators=(",", ":")) + "\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(line)


def _migrate_legacy_state_for_bp(backlog_path: Path) -> bool:
    """Move <bp.parent>/auto/state.json into the sessions/ layout.

    Idempotent: returns False when no legacy file exists. Synthesizes a
    session_id when the legacy state predates Plan 6 (run-level cursor
    only, no top-level session_id/task_id).
    """
    legacy = backlog_path.parent / "auto" / "state.json"
    if not legacy.exists():
        return False
    try:
        raw = json.loads(legacy.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    sid = raw.get("session_id") or raw.get("task_id")
    if not sid:
        target = raw.get("target") or "legacy"
        # 2026-05-07T20:38:15Z → 20260507T2038
        compact = (raw.get("started_at") or "").replace(":", "").replace("-", "")
        sid = f"{target}-{compact[:13]}" if compact else target
    state = dict(raw)
    state.setdefault("session_id", sid)
    cur = state.get("cursor") or {}
    state.setdefault("task_id", cur.get("task_id") or sid)
    p = auto_session_path_bp(backlog_path, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(state, indent=2) + "\n")
    legacy.rename(legacy.with_name("state.legacy.json"))
    return True


def auto_state_path(backlog_path: Path) -> Path:
    """Path to the active auto-run session file. Kept for back-compat.
    Returns a sessions/ path even when no run is active (callers that need
    existence should check `.exists()` after this call).
    """
    state = read_auto_state(backlog_path)
    sid = state["session_id"] if state else "_no_active"
    return auto_session_path_bp(backlog_path, sid)


def read_auto_state(backlog_path: Path) -> dict[str, Any] | None:
    """Return the active auto-run session (newest with cursor != None and
    not stopped). Auto-migrates the legacy single-state file on first call.
    """
    _migrate_legacy_state_for_bp(backlog_path)
    sd = auto_sessions_dir(backlog_path)
    if not sd.exists():
        return None
    candidates: list[dict[str, Any]] = []
    for p in sd.glob("*.json"):
        if p.name.endswith(".events.jsonl"):
            continue
        try:
            candidates.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    candidates.sort(key=lambda s: s.get("started_at", ""), reverse=True)
    for s in candidates:
        if s.get("cursor") is not None and not s.get("stopped"):
            return s
    return None


def write_auto_state(backlog_path: Path, state: dict[str, Any]) -> Path:
    sid = state.get("session_id")
    if not sid:
        raise ValueError("auto state missing session_id; call init_auto_run first")
    p = auto_session_path_bp(backlog_path, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(state, indent=2) + "\n")
    return p


def clear_auto_state(backlog_path: Path) -> bool:
    """Mark the active session ended (cursor=None, stopped=True, ended_at).
    Keeps the file so the UI retains run history. Returns True if a session
    was active, else False.
    """
    state = read_auto_state(backlog_path)
    if not state:
        return False
    ended_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["cursor"] = None
    state["stopped"] = True
    state["ended_at"] = ended_at
    write_auto_state(backlog_path, state)
    append_auto_event_bp(backlog_path, state["session_id"], {
        "ts": ended_at,
        "session_id": state["session_id"],
        "kind": "run_ended",
    })
    return True


def init_auto_run(
    backlog_path: Path,
    *,
    mode: str,
    target: str,
    pending_task_ids: list[str],
    model_for_task: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Initialize a fresh auto run, write state, and return the state dict.

    `model_for_task` maps task_id → "sonnet"|"opus"; missing entries default
    to sonnet. The first pending task becomes the cursor (stage=PICK).
    """
    if mode not in AUTO_MODES:
        raise ValueError(f"mode must be one of {AUTO_MODES}, got {mode!r}")
    if not pending_task_ids:
        raise ValueError("pending_task_ids must not be empty")
    model_for_task = model_for_task or {}
    first = pending_task_ids[0]
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 2026-05-07T20:38:15Z → 20260507T2038 — collision-free for runs >1min apart.
    sid_compact = started_at.replace(":", "").replace("-", "")[:13]
    sid = f"{target}-{sid_compact}"
    state: dict[str, Any] = {
        "session_id": sid,
        "task_id": first,
        "mode": mode,
        "target": target,
        "started_at": started_at,
        "cursor": {
            "task_id": first,
            "stage": "PICK",
            "model": model_for_task.get(first, "sonnet"),
        },
        "completed": [],
        "pending": list(pending_task_ids[1:]),
        "failed": [],
        "models": dict(model_for_task),
        "config": {
            "continue_on_fail": False,
            "no_gate": False,
            **(config or {}),
        },
        "subagents": [],
        "tool_log": [],
    }
    write_auto_state(backlog_path, state)
    append_auto_event_bp(backlog_path, sid, {
        "ts": started_at,
        "session_id": sid,
        "kind": "run_started",
        "mode": mode,
        "target": target,
        "pending": list(pending_task_ids),
    })
    return state


def advance_stage(state: dict[str, Any], new_stage: str) -> dict[str, Any]:
    """Move the cursor to a new stage. Returns the same state dict (mutated)."""
    if new_stage not in AUTO_STAGES:
        raise ValueError(f"stage must be one of {AUTO_STAGES}, got {new_stage!r}")
    cursor = state.get("cursor")
    if not cursor:
        raise ValueError("no active cursor — auto run is complete or not started")
    cursor["stage"] = new_stage
    return state


def complete_current_task(
    state: dict[str, Any],
    *,
    status: str,
    summary: str = "",
    commits: list[str] | None = None,
    fail_reason: str = "",
    handover_id: str = "",
) -> dict[str, Any]:
    """Finalize the current cursor task and advance to the next pending one.

    On success: appends to `completed`, pops next from `pending` as new cursor.
    On failure: appends to `failed`. If config.continue_on_fail, advance;
    otherwise leave cursor in place (caller decides next step) but stage = COMPLETE.

    When `pending` is empty after success, cursor becomes None and overall
    state effectively reaches the COMPLETE phase for the orchestrator.
    """
    if status not in AUTO_TASK_STATUSES:
        raise ValueError(f"status must be one of {AUTO_TASK_STATUSES}")
    cursor = state.get("cursor")
    if not cursor:
        raise ValueError("no active cursor")
    tid = cursor["task_id"]
    record: dict[str, Any] = {
        "task_id": tid,
        "status": status,
        "summary": summary,
        "commits": list(commits or []),
    }
    if handover_id:
        record["handover_id"] = handover_id

    if status == "done":
        state["completed"].append(record)
        _advance_to_next(state)
    else:
        if fail_reason and fail_reason not in AUTO_FAIL_REASONS:
            raise ValueError(f"fail_reason must be one of {AUTO_FAIL_REASONS}")
        record["reason"] = fail_reason or status
        state["failed"].append(record)
        if state["config"].get("continue_on_fail"):
            _advance_to_next(state)
        else:
            # Halt: keep cursor on the failed task at HANDOVER_STUB so the
            # orchestrator can write a recovery handover and pause for user.
            cursor["stage"] = "HANDOVER_STUB"
    return state


def _advance_to_next(state: dict[str, Any]) -> None:
    pending = state.get("pending") or []
    if not pending:
        state["cursor"] = None
        return
    next_id = pending.pop(0)
    state["pending"] = pending
    state["cursor"] = {
        "task_id": next_id,
        "stage": "PICK",
        "model": state.get("models", {}).get(next_id, "sonnet"),
    }


def auto_run_summary(state: dict[str, Any]) -> str:
    """Compact human-readable summary of an auto run for status checks."""
    if not state:
        return "No auto run in progress."
    lines = [
        f"Auto run: mode={state['mode']}, target={state['target']}",
        f"Started: {state.get('started_at', '?')}",
    ]
    cur = state.get("cursor")
    if cur:
        lines.append(
            f"Current: {cur['task_id']} @ {cur['stage']} (model={cur.get('model','sonnet')})"
        )
    else:
        lines.append("Current: (none — run complete)")
    lines.append(f"Completed: {len(state.get('completed') or [])}")
    lines.append(f"Pending:   {len(state.get('pending') or [])}")
    failed = state.get("failed") or []
    if failed:
        lines.append(f"Failed:    {len(failed)} ({', '.join(f['task_id'] for f in failed)})")
    return "\n".join(lines)


def format_recap(diff: dict[str, Any]) -> str:
    """Render a recap diff as a compact human-readable string.

    Format roughly mirrors the spec example:
        + T features-009 "Add SSO" (high, todo)
        ~ T features-002 status: todo → in-progress
        - T infra-005 "Old thing"
        Phase: setup → development
    """
    if diff.get("no_prior_snapshot"):
        return "No prior snapshot — recap will populate after the next snapshot."
    if diff.get("no_changes"):
        return f"No changes since {diff.get('prev_taken_at', 'last snapshot')}."

    lines: list[str] = []
    for t in diff["tasks_added"]:
        prio = t.get("priority") or "?"
        status = t.get("status") or "?"
        lines.append(f"+ T {t['id']} \"{t['title']}\" ({prio}, {status})")
    for t in diff["tasks_changed"]:
        for c in t["changes"]:
            lines.append(f"~ T {t['id']} {c['field']}: {c['before']} → {c['after']}")
    for t in diff["tasks_removed"]:
        lines.append(f"- T {t['id']} \"{t['title']}\"")
    if diff["phase_changed"]:
        pc = diff["phase_changed"]
        lines.append(f"Phase: {pc['before']} → {pc['after']}")
    return "\n".join(lines)


def save_v3(backlog_path: Path, data: dict[str, Any]) -> None:
    """Save a v3 backlog: slim index → backlog.yaml; heavy fields → per-task files.

    Per-task files are written only when there is heavy content or a body.
    A task with all-empty heavy fields gets no file (keeps directory tidy).
    Existing per-task files for tasks that no longer have heavy content are
    left alone — explicit task deletion handles cleanup.
    """
    slim_data: dict[str, Any] = {**data}
    slim_data["epics"] = []
    for epic in data.get("epics", []):
        slim_epic = {**epic, "tasks": []}
        for task in epic.get("tasks", []):
            slim_task, heavy_fm, body = _split_task_for_v3(task)
            slim_epic["tasks"].append(slim_task)
            tid = slim_task.get("id")
            if not tid:
                continue
            has_heavy = any(k in heavy_fm for k in HEAVY_FIELDS) or bool(body)
            if has_heavy:
                write_task_file(task_file_path(backlog_path, tid), heavy_fm, body)
        slim_data["epics"].append(slim_epic)
    atomic_write(
        backlog_path,
        yaml.dump(slim_data, default_flow_style=False, sort_keys=False, allow_unicode=True),
    )


# ---- Recap helpers -------------------------------------------------------


def recap_path(session_id: str) -> Path:
    """Path on disk for the recap file of a given session."""
    return _resolve_artifact_root() / "recaps" / f"{session_id}.md"


def _format_recap_markdown(
    *,
    frontmatter: dict,
    title: str,
    what_happened: str,
    what_landed: str,
    whats_next: str,
) -> str:
    """Render a recap file: YAML frontmatter + H1 title + three H2 sections.
    Section order is fixed per spec §3.16: What happened / What landed / What's next.
    """
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False).rstrip()
    return (
        f"---\n{fm_text}\n---\n\n"
        f"# {title}\n\n"
        f"## What happened\n\n{what_happened.rstrip()}\n\n"
        f"## What landed\n\n{what_landed.rstrip()}\n\n"
        f"## What's next\n\n{whats_next.rstrip()}\n"
    )


_RECAP_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_recap_markdown(text: str) -> dict:
    """Inverse of `_format_recap_markdown`. Returns
    `{frontmatter, title, what_happened, what_landed, whats_next}`.
    Missing sections come back as empty strings.
    """
    m = _RECAP_FM_RE.match(text)
    if not m:
        raise ValueError("recap is missing YAML frontmatter")
    fm = yaml.safe_load(m.group(1)) or {}
    body = text[m.end():]

    title_m = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else ""

    def _grab(label: str) -> str:
        pat = re.compile(
            rf"^##\s+{re.escape(label)}\s*\n+(.*?)(?=^##\s+|\Z)",
            re.DOTALL | re.MULTILINE,
        )
        m2 = pat.search(body)
        return m2.group(1).strip() if m2 else ""

    return {
        "frontmatter": fm,
        "title": title,
        "what_happened": _grab("What happened"),
        "what_landed":   _grab("What landed"),
        "whats_next":    _grab("What's next"),
    }


def save_recap(
    *,
    session_id: str,
    frontmatter: dict,
    title: str,
    what_happened: str,
    what_landed: str,
    whats_next: str,
) -> Path:
    """Write `.taskmaster/recaps/<session-id>.md`. `session_id` and
    `schema_version` are auto-injected into the frontmatter; the rest is
    passed through verbatim.
    """
    fm = dict(frontmatter)
    fm["session_id"] = session_id
    fm["schema_version"] = RECAP_SCHEMA_VERSION
    md = _format_recap_markdown(
        frontmatter=fm,
        title=title,
        what_happened=what_happened,
        what_landed=what_landed,
        whats_next=whats_next,
    )
    p = recap_path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, md)
    return p


def load_recap(session_id: str) -> dict | None:
    """Load and parse a recap file. Returns None when missing.
    Returned dict: {frontmatter, title, what_happened, what_landed, whats_next}.
    """
    p = recap_path(session_id)
    if not p.exists():
        return None
    return _parse_recap_markdown(p.read_text(encoding="utf-8"))


def list_recaps() -> list[str]:
    """Return session ids of all on-disk recaps, sorted descending (newest first)."""
    d = _resolve_artifact_root() / "recaps"
    if not d.exists():
        return []
    ids = [p.stem for p in d.glob("*.md") if not p.name.startswith("_")]
    ids.sort(reverse=True)
    return ids


# ---- Session snapshots ---------------------------------------------------


def save_session_snapshot(snapshot_id: str, payload: dict) -> Path:
    """Write `<backlog-parent>/snapshots/<snapshot-id>.json` (atomic). The rolling
    `last.json` is unaffected; per-session files coexist alongside it.
    """
    import json as _json
    p = _resolve_artifact_root() / "snapshots" / f"{snapshot_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, _json.dumps(payload, indent=2))
    return p


def load_session_snapshot(snapshot_id: str) -> dict | None:
    """Load `<backlog-parent>/snapshots/<snapshot-id>.json`. None when missing."""
    import json as _json
    p = _resolve_artifact_root() / "snapshots" / f"{snapshot_id}.json"
    if not p.exists():
        return None
    return _json.loads(p.read_text(encoding="utf-8"))


def snapshot_diff(a: dict, b: dict) -> dict:
    """Compute a structured diff from snapshot `a` (before) to `b` (after).

    Returned shape (mirrors the client-side helper):
      {
        tasks_added:        [{id, ...task}],
        tasks_removed:      [{id, ...task}],
        tasks_changed:      [{id, from, to}],   # whole-task before/after
        lessons_fired:      [{id, fires, first_time}],
        issues_opened:      [{id, ...issue}],
        issues_transitioned:[{id, from, to}],
        files_touched:      [path, ...],
      }
    """
    a_tasks = (a or {}).get("tasks", {}) or {}
    b_tasks = (b or {}).get("tasks", {}) or {}

    added   = [{"id": tid, **b_tasks[tid]} for tid in b_tasks if tid not in a_tasks]
    removed = [{"id": tid, **a_tasks[tid]} for tid in a_tasks if tid not in b_tasks]
    changed = [
        {"id": tid, "from": a_tasks[tid], "to": b_tasks[tid]}
        for tid in a_tasks if tid in b_tasks and a_tasks[tid] != b_tasks[tid]
    ]

    a_iss = (a or {}).get("issues", {}) or {}
    b_iss = (b or {}).get("issues", {}) or {}
    issues_opened = [
        {"id": iid, **b_iss[iid]} for iid in b_iss if iid not in a_iss
    ]
    issues_transitioned = [
        {"id": iid, "from": a_iss[iid].get("status"),
                    "to":   b_iss[iid].get("status")}
        for iid in a_iss if iid in b_iss
        and a_iss[iid].get("status") != b_iss[iid].get("status")
    ]

    return {
        "tasks_added":         added,
        "tasks_removed":       removed,
        "tasks_changed":       changed,
        "lessons_fired":       list((b or {}).get("lessons_fired", []) or []),
        "issues_opened":       issues_opened,
        "issues_transitioned": issues_transitioned,
        "files_touched":       list((b or {}).get("files_touched", []) or []),
    }


# ---- Sessions ------------------------------------------------------------


def _parse_iso8601(s) -> "datetime":
    from datetime import datetime, timezone
    if isinstance(s, datetime):
        if s.tzinfo is None:
            return s.replace(tzinfo=timezone.utc)
        return s
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def list_sessions() -> list[dict]:
    """Synthesise sessions from on-disk handover files.

    Algorithm: load every handover, sort by date asc, then greedily group
    consecutive handovers that share at least one task_id AND occur within
    SESSION_GAP_MINUTES (default 30). Each group becomes one session.

    Returns: list of dicts (newest first):
      {id, start, end, duration, handover_ids[], recap_id, task_ids[], parallel_with[]}
    """
    from datetime import timedelta
    SESSION_GAP_MINUTES = 30
    handovers_dir = _resolve_artifact_root() / "handovers"
    if not handovers_dir.exists():
        return []
    raw: list[dict] = []
    for p in sorted(handovers_dir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
            m = _RECAP_FM_RE.match(text)
            if not m:
                continue
            fm = yaml.safe_load(m.group(1)) or {}
            if "id" not in fm or "date" not in fm:
                continue
            raw.append(fm)
        except Exception:
            continue
    raw.sort(key=lambda h: _parse_iso8601(h["date"]))

    groups: list[list[dict]] = []
    for h in raw:
        h_t = _parse_iso8601(h["date"])
        h_tids = set(h.get("task_ids") or [])
        attached = False
        if groups:
            tail = groups[-1][-1]
            tail_t = _parse_iso8601(tail["date"])
            tail_tids = set(tail.get("task_ids") or [])
            within_gap = (h_t - tail_t) <= timedelta(minutes=SESSION_GAP_MINUTES)
            shared_tasks = bool(h_tids & tail_tids)
            if within_gap and shared_tasks:
                groups[-1].append(h)
                attached = True
        if not attached:
            groups.append([h])

    sessions: list[dict] = []
    recap_ids = set(list_recaps())
    for idx, group in enumerate(groups, start=1):
        sid = f"SES-{idx:04d}"
        start = _parse_iso8601(group[0]["date"])
        end = _parse_iso8601(group[-1]["date"])
        tids: list[str] = []
        for h in group:
            for t in (h.get("task_ids") or []):
                if t not in tids:
                    tids.append(t)
        sessions.append({
            "id": sid,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "duration": int((end - start).total_seconds()),
            "handover_ids": [h["id"] for h in group],
            "handovers": [
                {
                    "id": h["id"],
                    "status": h.get("status", "todo"),
                    "viewer_kind": HANDOVER_KIND_TO_VIEWER_KIND.get(h.get("session_kind"), "standalone"),
                    "tldr": h.get("tldr", ""),
                }
                for h in group
            ],
            "recap_id": sid if sid in recap_ids else None,
            "task_ids": tids,
            "parallel_with": [],   # filled below
        })

    # Mark parallel sessions: any pair with overlapping [start,end] windows.
    # Windows are expanded by SESSION_GAP_MINUTES so that two single-handover
    # sessions that were close in time but different in task scope are flagged.
    from datetime import timedelta as _td
    _gap = _td(minutes=SESSION_GAP_MINUTES)
    for i, s in enumerate(sessions):
        s_start = _parse_iso8601(s["start"])
        s_end = _parse_iso8601(s["end"]) + _gap
        for j, o in enumerate(sessions):
            if i == j:
                continue
            o_start = _parse_iso8601(o["start"])
            o_end = _parse_iso8601(o["end"]) + _gap
            if s_start <= o_end and o_start <= s_end:
                s["parallel_with"].append(o["id"])

    sessions.sort(key=lambda s: s["start"], reverse=True)
    return sessions


def _load_handover_full(handover_id: str) -> dict | None:
    """Load a handover's frontmatter + body_md by id."""
    p = _resolve_artifact_root() / "handovers" / f"{handover_id}.md"
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    m = _RECAP_FM_RE.match(text)
    if not m:
        return None
    fm = yaml.safe_load(m.group(1)) or {}
    body = text[m.end():].strip()
    fm["resume_prompt"] = body          # body is the resume prompt artifact
    fm["viewer_kind"] = HANDOVER_KIND_TO_VIEWER_KIND.get(
        fm.get("session_kind"), "standalone"
    )
    return fm


def get_session_detail(session_id: str) -> dict | None:
    """Bundle one session with its handovers, recap, and task ids."""
    sessions = list_sessions()
    target = next((s for s in sessions if s["id"] == session_id), None)
    if target is None:
        return None
    handovers = []
    for hid in target["handover_ids"]:
        h = _load_handover_full(hid)
        if h is not None:
            handovers.append(h)
    recap = load_recap(session_id)
    return {
        "session": target,
        "handovers": handovers,
        "recap": recap,
        "task_ids": target["task_ids"],
    }


def list_lesson_ids_cwd() -> list[str]:
    """List lesson ids from <backlog-parent>/lessons/ in the current working directory."""
    d = _resolve_artifact_root() / "lessons"
    if not d.exists():
        return []

    def _rank(p: Path) -> int:
        import re as _re
        m = _re.search(r"(\d+)$", p.stem)
        return int(m.group(1)) if m else -1

    return [p.stem for p in sorted(d.glob("*.md"), key=_rank)]


def load_issue(issue_id: str) -> dict:
    """Load an issue by id from <backlog-parent>/issues/<id>.md in CWD.

    Returns a dict with frontmatter fields plus '_body'.
    """
    p = _resolve_artifact_root() / "issues" / f"{issue_id}.md"
    fm, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    fm["_body"] = body
    return fm


def list_issue_ids_cwd() -> list[str]:
    """List issue ids from <backlog-parent>/issues/ in the current working directory."""
    import re as _re
    d = _resolve_artifact_root() / "issues"
    if not d.exists():
        return []

    def _rank(p: Path) -> int:
        m = _re.search(r"(\d+)$", p.stem)
        return int(m.group(1)) if m else -1

    return [p.stem for p in sorted(d.glob("ISS-*.md"), key=_rank)]


SEVERITY_LABEL = {"P0": "Critical", "P1": "High", "P2": "Medium", "P3": "Low"}


def severity_label(p: str) -> str:
    """Map raw severity code to user-facing word."""
    return SEVERITY_LABEL.get(p, p)


def compute_issue_aging(issue: dict, aging_cfg: dict, now=None) -> dict:
    """Return {'percent': float, 'tier': 'Fresh'|'Aging'|'Stale'} given issue + cfg.

    Tier rules (per spec §3.14):
        Fresh:  0 <= pct < 25
        Aging: 25 <= pct < 60
        Stale: pct >= 60

    `percent` may exceed 100 for very stale issues; clamp at 200 for display.
    """
    from datetime import datetime, timezone

    if now is None:
        now = datetime.now(timezone.utc)

    label = severity_label(issue.get("severity", "P2"))
    base_days = float(aging_cfg.get(label, 60))
    discovered_raw = issue.get("discovered")
    if not discovered_raw:
        return {"percent": 0.0, "tier": "Fresh"}
    # Accept both ISO datetime ("YYYY-MM-DDTHH:MM:SSZ") and date-only
    # ("YYYY-MM-DD"); the writer's default emits date-only, so we must tolerate
    # it here. ISS-005.
    discovered = None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            discovered = datetime.strptime(discovered_raw, fmt).replace(tzinfo=timezone.utc)
            break
        except (ValueError, TypeError):
            continue
    if discovered is None:
        return {"percent": 0.0, "tier": "Fresh"}
    age_days = (now - discovered).total_seconds() / 86400.0

    pct = (age_days / base_days) * 100.0 if base_days > 0 else 0.0
    pct = max(0.0, min(pct, 200.0))
    if pct < 25:
        tier = "Fresh"
    elif pct < 60:
        tier = "Aging"
    else:
        tier = "Stale"
    return {"percent": pct, "tier": tier}


def compute_lesson_shelf(lesson: dict, thresholds: dict, now=None) -> str:
    """Compute shelf placement: 'core' | 'active' | 'retired'.

    Rules (driven by reinforce_events only — passive anchor matches are ignored):
        core      — count(events within core_window_days) >= core_count
                    AND at least one event within core_recency_days
        retired   — no events within retired_after_days
        active    — otherwise (any event within retired_after_days that
                    doesn't qualify as core)
    """
    from datetime import datetime, timedelta, timezone

    if now is None:
        now = datetime.now(timezone.utc)

    events = lesson.get("reinforce_events") or []

    def _parse(e):
        return datetime.strptime(e["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    parsed = [_parse(e) for e in events]

    core_count = int(thresholds.get("core_count", 7))
    core_window = timedelta(days=int(thresholds.get("core_window_days", 60)))
    core_recency = timedelta(days=int(thresholds.get("core_recency_days", 14)))
    retired_after = timedelta(days=int(thresholds.get("retired_after_days", 30)))

    in_window = [t for t in parsed if (now - t) <= core_window]
    in_recency = [t for t in parsed if (now - t) <= core_recency]
    in_active = [t for t in parsed if (now - t) <= retired_after]

    if len(in_window) >= core_count and len(in_recency) >= 1:
        return "core"
    if not in_active:
        return "retired"
    return "active"


# ── Edit-in-UI write primitives (v3-edit Phase A) ──────────────────

import contextlib

_threadlocal_locks: dict[str, "threading.Lock"] = {}


@contextlib.contextmanager
def with_file_lock(path: Path):
    """Per-file mutex for write paths.

    Uses a `.lock` sidecar adjacent to the target file. Falls back to a
    threading-local lock if the `filelock` package isn't available — local
    use is single-process so this is acceptable; future cross-process
    safety lands when filelock becomes a hard dep.
    """
    try:
        from filelock import FileLock
        lock = FileLock(str(path) + ".lock", timeout=5)
        with lock:
            yield
    except ImportError:
        import threading
        lock = _threadlocal_locks.setdefault(str(path), threading.Lock())
        with lock:
            yield


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_task_in_yaml(data: dict, task_id: str) -> tuple[dict, dict] | None:
    """Return (epic_dict, task_dict) for a v2-nested layout, or None."""
    for epic in data.get("epics") or []:
        for t in epic.get("tasks") or []:
            if t.get("id") == task_id:
                return epic, t
    return None


def update_task(task_id: str, patch: dict, backlog_path: Path | None = None) -> dict:
    """Apply a partial update to a task. Returns the new task dict.

    - Auto-stamps `started` on first transition into `in-progress` (or any
      non-todo state from `todo`).
    - Auto-stamps `completed` on transition into `done`.
    - Never overwrites `started`/`completed` once set.
    """
    bp = backlog_path or _resolve_backlog_path()
    with with_file_lock(bp):
        data = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
        found = _find_task_in_yaml(data, task_id)
        if found is None:
            raise KeyError(f"task {task_id} not found")
        epic, task = found
        before_status = task.get("status")
        for k, v in patch.items():
            task[k] = v
        after_status = task.get("status")
        if after_status != before_status:
            if after_status == "in-progress" and not task.get("started"):
                task["started"] = _now_iso()
            if after_status == "done" and not task.get("completed"):
                task["completed"] = _now_iso()
        task["last_referenced"] = _now_iso()
        atomic_write(bp, yaml.safe_dump(data, sort_keys=False))
        return dict(task)


def create_task(payload: dict, backlog_path: Path | None = None) -> str:
    """Create a new task under the given epic. Returns assigned id."""
    bp = backlog_path or _resolve_backlog_path()
    epic_id = payload.get("epic")
    if not epic_id:
        raise ValueError("epic is required")
    with with_file_lock(bp):
        data = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
        epic = next((e for e in (data.get("epics") or []) if e.get("id") == epic_id), None)
        if epic is None:
            raise KeyError(f"epic {epic_id} not found")
        existing_ids = {t.get("id") for t in (epic.get("tasks") or [])}
        # Generate next id like e1-002.
        n = 1
        while f"{epic_id}-{n:03d}" in existing_ids:
            n += 1
        new_id = f"{epic_id}-{n:03d}"
        new_task = {
            "id": new_id,
            "title": payload.get("title", ""),
            "status": payload.get("status", "todo"),
            "priority": payload.get("priority", "medium"),
            "created": _now_iso(),
            "last_referenced": _now_iso(),
        }
        # Pass through other supplied fields (phase, anchors, depends_on, etc).
        for k, v in payload.items():
            if k not in ("epic", "id"):
                new_task[k] = v
        epic.setdefault("tasks", []).append(new_task)
        atomic_write(bp, yaml.safe_dump(data, sort_keys=False))
        return new_id


def archive_task(task_id: str, backlog_path: Path | None = None) -> None:
    """Soft-delete: set status to 'archived'. The existing
    backlog_archive_task MCP tool already does this for v2 backlogs;
    we mirror the behavior here so HTTP shares the code path."""
    update_task(task_id, {"status": "archived"}, backlog_path=backlog_path)


def _resolve_backlog_path() -> Path:
    """Lazy import of backlog_server's resolver to avoid circular import."""
    from backlog_server import _backlog_path
    return _backlog_path()


def validate_task_write(task_id: str, patch: dict, backlog_path: Path | None = None) -> dict[str, str]:
    """Run cross-entity validation for a proposed task write.

    Returns a dict { field: error_message }. Empty dict means valid.
    Pure function — does not persist.
    """
    bp = backlog_path or _resolve_backlog_path()
    data = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
    errors: dict[str, str] = {}

    # Build helper maps.
    epic_ids = {e.get("id") for e in (data.get("epics") or []) if e.get("id")}
    phase_ids = {p.get("id") for p in (data.get("phases") or []) if p.get("id")}
    all_tasks: list[dict] = []
    for e in data.get("epics") or []:
        for t in e.get("tasks") or []:
            all_tasks.append(t)
    task_ids = {t.get("id") for t in all_tasks if t.get("id")}

    # Locate the task being patched.
    me = next((t for t in all_tasks if t.get("id") == task_id), None)
    if me is None and task_id != "<new>":
        errors["_task"] = f"task {task_id} not found"
        return errors

    # Compose the proposed state.
    proposed = {**(me or {}), **patch}  # noqa: F841 — kept for future cross-field rules

    # Epic must exist.
    if "epic" in patch and patch["epic"] and patch["epic"] not in epic_ids:
        errors["epic"] = f"unknown epic: {patch['epic']}"

    # Phase must exist if set.
    if "phase" in patch and patch["phase"] and patch["phase"] not in phase_ids:
        errors["phase"] = f"unknown phase: {patch['phase']}"

    # Deps: each must exist; no self-dep; no cycle.
    if "depends_on" in patch:
        deps = patch["depends_on"] or []
        for d in deps:
            if d == task_id:
                errors["depends_on"] = "cannot depend on itself"
                break
            if d not in task_ids:
                errors["depends_on"] = f"unknown task in depends_on: {d}"
                break
        if "depends_on" not in errors:
            # Cycle detection: BFS from each dep — if any path reaches task_id, cycle.
            adj = {t.get("id"): list(t.get("depends_on") or []) for t in all_tasks if t.get("id")}
            adj[task_id] = list(deps)  # simulate the proposed state
            if _has_cycle_to(adj, task_id):
                errors["depends_on"] = "introduces a dependency cycle"

    return errors


def _has_cycle_to(adj: dict, target: str) -> bool:
    """True if `target` is reachable from any of its own deps under `adj`."""
    seen = set()
    stack = list(adj.get(target, []))
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj.get(cur, []))
    return False


def compute_etag(path: Path) -> str:
    """Stable, cheap ETag derived from file mtime + content hash.

    Returns an 16-hex-char string suitable for HTTP ETag headers.
    """
    if not path.exists():
        return ""
    st = path.stat()
    h = hashlib.sha1()
    h.update(str(st.st_mtime_ns).encode())
    # Also hash content so two writes with identical content (e.g. same byte
    # body) collapse to the same etag — desirable for cache stability.
    h.update(path.read_bytes())
    return h.hexdigest()[:16]
