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
from datetime import date, datetime, timezone
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


# ── Handovers ──────────────────────────────────────────────────

# Canonical session_kind values (free-form string in storage; these are
# the well-known ones that may get special treatment elsewhere).
HANDOVER_KINDS = ("end-of-day", "context-handoff", "crash-recovery", "auto-stage")

# Index cap — `handovers:` array in backlog.yaml is bounded.
HANDOVER_INDEX_CAP = 30


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
) -> tuple[str, Path]:
    """Write a new handover file.

    Returns (handover_id, file_path). The id is `<date>-<slug-of-tldr>`. If
    a handover with the same id already exists the slug gets a numeric
    suffix to avoid clobbering same-day handovers with similar tldrs.
    """
    if not tldr or not tldr.strip():
        raise ValueError("handover tldr is required")
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
        "tldr": tldr.strip(),
        "next_action": (next_action or "").strip(),
        "task_ids": list(task_ids or []),
        "session_kind": session_kind,
    }
    if context_size_at_write:
        fm["context_size_at_write"] = context_size_at_write

    write_task_file(target, fm, body)
    return final_id, target


def read_handover(backlog_path: Path, handover_id: str) -> tuple[dict[str, Any], str]:
    """Read a handover file by id. Raises FileNotFoundError if missing."""
    return read_task_file(handover_path(backlog_path, handover_id))


def list_handover_ids(backlog_path: Path) -> list[str]:
    """List handover ids on disk, sorted newest-first by id (date-prefixed)."""
    d = handover_dir(backlog_path)
    if not d.exists():
        return []
    ids = [p.stem for p in d.glob("*.md")]
    ids.sort(reverse=True)
    return ids


def latest_handover_id(backlog_path: Path) -> str | None:
    ids = list_handover_ids(backlog_path)
    return ids[0] if ids else None


# Fields kept in the backlog.yaml `handovers:` index entry.
_HANDOVER_INDEX_FIELDS = ("id", "date", "tldr", "next_action", "task_ids", "session_kind")


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
    fm: dict[str, Any] = {
        "id": iid,
        "title": title.strip(),
        "status": status,
        "severity": severity,
        "components": list(components or []),
        "impact": impact.strip(),
        "location": list(location or []),
        "discovered": discovered or date.today().isoformat(),
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


def auto_state_path(backlog_path: Path) -> Path:
    """Path to the auto-mode cursor file (gitignored)."""
    return backlog_path.parent / "auto" / "state.json"


def read_auto_state(backlog_path: Path) -> dict[str, Any] | None:
    sp = auto_state_path(backlog_path)
    if not sp.exists():
        return None
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_auto_state(backlog_path: Path, state: dict[str, Any]) -> Path:
    sp = auto_state_path(backlog_path)
    atomic_write(sp, json.dumps(state, indent=2) + "\n")
    return sp


def clear_auto_state(backlog_path: Path) -> bool:
    """Delete the state file. Returns True if a file was removed."""
    sp = auto_state_path(backlog_path)
    if sp.exists():
        sp.unlink()
        return True
    return False


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
    state: dict[str, Any] = {
        "mode": mode,
        "target": target,
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    }
    write_auto_state(backlog_path, state)
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
