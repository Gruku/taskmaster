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
from datetime import datetime, timezone
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
