"""Taskmaster v3 — narrative-continuity layout helpers.

This module is intentionally framework-free (no fastmcp imports) so it can be
tested in isolation. It owns:

- Schema version constants and detection.
- Atomic file writes.
- (Future slices) Frontmatter parsing, per-task file I/O, v3 load/save, migration.

`backlog_server.py` re-exports the symbols it needs from here.
"""
from __future__ import annotations

import os
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
