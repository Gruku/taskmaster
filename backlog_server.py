# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp", "pyyaml"]
# ///

import asyncio
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.request
import uuid
import webbrowser
from datetime import date, datetime
from functools import partial
from http import HTTPStatus
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml
from fastmcp import FastMCP
from blast_radius import (
    BlastRadiusConfig,
    load_config,
    analyze_predictive,
    analyze_evidence,
)

mcp = FastMCP("taskmaster")

SCRIPT_DIR = Path(__file__).parent
ROOT = Path(os.environ.get("TASKMASTER_ROOT", Path.cwd()))
VIEWER_PATH = SCRIPT_DIR / "backlog-viewer.html"
CONFIG_PATH = ROOT / ".taskmaster" / "taskmaster.json"
# Legacy location read-only fallback. Pre-consolidation projects wrote config
# to `.claude/taskmaster.json`; the resolver still honors it so those servers
# keep working until the user runs `backlog_canonicalize_layout`.
LEGACY_CONFIG_PATH = ROOT / ".claude" / "taskmaster.json"

# Version from plugin.json
_plugin_json = SCRIPT_DIR / ".claude-plugin" / "plugin.json"
VERSION = json.loads(_plugin_json.read_text(encoding="utf-8"))["version"] if _plugin_json.exists() else "0.0.0"

# Priority mapping: canonical names ↔ legacy P-codes
PRIORITY_NAMES = ("critical", "high", "medium", "low")
_LEGACY_TO_NAME = {"P0": "critical", "P1": "high", "P2": "medium", "P3": "low"}
_NAME_TO_LEGACY = {v: k for k, v in _LEGACY_TO_NAME.items()}

# v3 layout primitives (schema versions, atomic writes, etc.) live in taskmaster_v3.
# Re-imported here for in-module reference.
from taskmaster_v3 import (
    SCHEMA_V2,
    SCHEMA_V3,
    SCHEMA_DEFAULT,
    TLDR_MAX_CHARS,
    extract_tldr,
    VALID_LANES as _VALID_LANES,
    VALID_GATES as _VALID_GATES,
    VERDICT_GATES as _VERDICT_GATES,
    VALID_GATE_VERDICTS as _VALID_GATE_VERDICTS,
    required_gates as _required_gates,
    blocking_gates as _blocking_gates,
    outstanding_required_gates as _outstanding_required_gates,
    gate_satisfied as _gate_satisfied,
    default_lane as _default_lane,
    compute_gate_state as _compute_gate_state,
    HANDOVER_KINDS,
    HEAVY_FIELDS as _HEAVY_FIELDS,
    detect_schema_version as _detect_schema_version,
    atomic_write as _atomic_write,
    load_v3 as _load_v3,
    save_v3 as _save_v3,
    migrate_v2_to_v3 as _migrate_v2_to_v3,
    take_snapshot as _take_snapshot,
    write_snapshot as _write_snapshot,
    read_snapshot as _read_snapshot,
    diff_against_snapshot as _diff_against_snapshot,
    format_recap as _format_recap,
    write_handover as _write_handover,
    read_handover as _read_handover,
    apply_supersession as _apply_supersession,
    apply_handover_review_flag as _apply_handover_review_flag,
    update_handover_status as _update_handover_status,
    list_handover_ids as _list_handover_ids,
    latest_handover_id as _latest_handover_id,
    sync_handover_index as _sync_handover_index,
    ISSUE_STATUSES,
    ISSUE_SEVERITIES,
    write_issue as _write_issue,
    read_issue as _read_issue,
    update_issue as _update_issue,
    list_issue_ids as _list_issue_ids,
    sync_issue_index as _sync_issue_index,
    EXTERNAL_SYSTEMS,
    write_tracker as _write_tracker,
    read_tracker as _read_tracker,
    update_tracker as _update_tracker,
    list_tracker_ids as _list_tracker_ids,
    sync_tracker_index as _sync_tracker_index,
    make_tracker_id as _make_tracker_id,
    tracker_path as _tracker_path,
    tracker_dir as _tracker_dir,
    linked_tasks_for_tracker as _linked_tasks_for_tracker,
    linked_issues_for_tracker as _linked_issues_for_tracker,
    _validate_tracker as _validate_tracker_fm,
    load_linear_config as _load_linear_config,
    LESSON_KINDS,
    LESSON_TIERS,
    write_lesson as _write_lesson,
    read_lesson as _read_lesson,
    update_lesson as _update_lesson,
    reinforce_lesson as _reinforce_lesson,
    list_lesson_ids as _list_lesson_ids,
    match_lessons_for_task as _match_lessons_for_task,
    lesson_digest as _lesson_digest,
    core_lessons as _core_lessons,
    sync_lesson_index as _sync_lesson_index,
    lesson_eligible_for_promotion as _lesson_eligible_for_promotion,
    LESSON_CANDIDATE_KINDS,
    LESSON_CANDIDATE_SCOPES,
    lesson_candidates_defer as _lesson_candidates_defer,
    lesson_candidates_read as _lesson_candidates_read,
    lesson_candidates_clear as _lesson_candidates_clear,
    scan_transcripts_for_candidates as _scan_transcripts_for_candidates,
    write_idea as _write_idea,
    read_idea as _read_idea,
    update_idea as _update_idea,
    list_ideas as _list_ideas,
    write_decision as _write_decision,
    read_decision as _read_decision,
    update_decision as _update_decision,
    resolve_decision as _resolve_decision,
    drop_decision as _drop_decision,
    list_decision_ids as _list_decision_ids,
    decision_path as _decision_path,
    continuity_items as _continuity_items,
    write_task_file as _write_task_file,
    AUTO_MODES,
    AUTO_STAGES,
    AUTO_STAGE_GATE as _AUTO_STAGE_GATE,
    auto_stages_for_lane as _auto_stages_for_lane,
    AUTO_TASK_STATUSES,
    AUTO_FAIL_REASONS,
    init_auto_run as _init_auto_run,
    read_auto_state as _read_auto_state,
    write_auto_state as _write_auto_state,
    clear_auto_state as _clear_auto_state,
    append_auto_event_bp as _append_auto_event_bp,
    advance_stage as _advance_stage,
    next_planned_stage as _next_planned_stage,
    complete_current_task as _complete_current_task,
    auto_run_summary as _auto_run_summary,
    load_viewer_prefs,
    save_viewer_prefs,
    load_recap,
    save_recap,
    list_recaps,
    list_sessions,
    get_session_detail,
    load_session_snapshot,
    snapshot_diff as _snapshot_diff,
    read_hook_events as _read_hook_events,
    slim_entity as _slim_entity,
    resolve_sections as _resolve_sections,
    expand_link_ids as _expand_link_ids,
    build_tldr_index as _build_tldr_index,
    BODY_KEY as _BODY_KEY,
    render_frontmatter as _render_frontmatter,
    CANONICAL_SECTIONS as _CANONICAL_SECTIONS,
    rung_for_branch as _rung_for_branch,
    compute_merge_gate_state as _compute_merge_gate_state,
)


_HANDOVER_STATUS_BACKFILL_RAN = False


def _ensure_handover_status_backfilled() -> None:
    global _HANDOVER_STATUS_BACKFILL_RAN
    if _HANDOVER_STATUS_BACKFILL_RAN:
        return
    bp = _backlog_path()
    if not bp.exists():
        return
    try:
        data = _load()
    except Exception:
        return
    if data.get("handover_status_backfilled"):
        _HANDOVER_STATUS_BACKFILL_RAN = True
        return
    from taskmaster_v3 import backfill_handover_status as _bf
    flipped = _bf(data, bp)
    if flipped or "handover_status_backfilled" in data:
        _sync_handover_index(data, bp)
        _save(data)
    _HANDOVER_STATUS_BACKFILL_RAN = True


def _get_open_handovers_for_task(bp: Path, task_id: str) -> list[str]:
    """Scan handovers dir for open handovers referencing task_id."""
    hdir = bp.parent / "handovers"
    if not hdir.exists():
        return []
    result = []
    for path in sorted(hdir.glob("*.md")):
        try:
            fm, _ = _read_handover(bp, path.stem)
        except Exception:
            continue
        if fm.get("status") == "open" and task_id in (fm.get("task_ids") or []):
            result.append(fm.get("id") or path.stem)
    return result


def _append_grouped_links_block(
    lines: list[str],
    entity: dict,
    backlog_path: Path,
    *,
    expand_links: bool = False,
) -> None:
    """Append a Plan C grouped `links:` block to `lines` for slim-view rendering.

    Reads `entity.links` (typed array). When `expand_links` is True, swaps bare
    target IDs for `{id} ({tldr})` pills by reading peer entities.
    Emits nothing when there are no typed links.
    """
    from taskmaster_v3 import (
        links_grouped_by_type, read_entity_anywhere,
    )

    grouped = links_grouped_by_type(entity)
    if not grouped:
        return
    lines.append("\n**links:**")
    for ltype in sorted(grouped):
        targets = grouped[ltype]
        if expand_links:
            pills: list[str] = []
            for tgt in targets:
                peer = read_entity_anywhere(backlog_path, tgt) if backlog_path.exists() else None
                tldr = (peer or {}).get("tldr", "") if peer else ""
                pills.append(f"{tgt} ({tldr})" if tldr else tgt)
            lines.append(f"- {ltype}: [{', '.join(pills)}]")
        else:
            lines.append(f"- {ltype}: [{', '.join(targets)}]")


def _load_auto_state():
    """Read <backlog-parent>/auto/state.json, return parsed dict or None.

    Returns None when the file is missing OR contains invalid JSON.
    Used by GET /api/auto/state. Mutating writes are out of scope for Plan 2.

    Uses the CWD-fresh resolver from taskmaster_v3 (not _backlog_path()) so the
    path stays in sync with the writer's `bp.parent / "auto"` even when ROOT
    is frozen at import (e.g. under pytest with monkeypatch.chdir). ISS-004.
    """
    from taskmaster_v3 import _resolve_artifact_root
    p = _resolve_artifact_root() / "auto" / "state.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_paths() -> tuple[Path, Path]:
    """Resolve backlog.yaml and PROGRESS.md paths from config or defaults.

    Priority: .taskmaster/taskmaster.json > .claude/taskmaster.json (legacy)
    > .taskmaster/backlog.yaml > .claude/backlog.yaml (legacy) > ./backlog.yaml
    """
    for cfg_path in (CONFIG_PATH, LEGACY_CONFIG_PATH):
        if cfg_path.exists():
            try:
                config = json.loads(cfg_path.read_text(encoding="utf-8"))
                if cfg_path is LEGACY_CONFIG_PATH:
                    _warn_legacy_layout("config at .claude/taskmaster.json")
                return (
                    ROOT / config.get("backlog_path", "backlog.yaml"),
                    ROOT / config.get("progress_path", "PROGRESS.md"),
                )
            except (json.JSONDecodeError, KeyError):
                pass

    if (ROOT / ".taskmaster" / "backlog.yaml").exists():
        return ROOT / ".taskmaster" / "backlog.yaml", ROOT / ".taskmaster" / "PROGRESS.md"
    if (ROOT / ".claude" / "backlog.yaml").exists():
        _warn_legacy_layout("backlog at .claude/backlog.yaml")
        return ROOT / ".claude" / "backlog.yaml", ROOT / ".claude" / "PROGRESS.md"
    # Legacy: project root (before .taskmaster/ was introduced)
    return ROOT / "backlog.yaml", ROOT / "PROGRESS.md"


from taskmaster_v3 import warn_legacy_layout as _warn_legacy_layout


# Module-level accessors (resolved fresh each call via _load/_save)
def _backlog_path() -> Path:
    return _resolve_paths()[0]


def _progress_path() -> Path:
    return _resolve_paths()[1]


# ── Session identity (unique per MCP server process) ─────
SESSION_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

# ── File-level lock for backlog.yaml writes ──────────────
_backlog_lock = threading.Lock()


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    """ISO timestamp with minute precision: YYYY-MM-DDTHH:MM"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def _validate_date(s: str) -> date | None:
    """Parse YYYY-MM-DD string, return date or None if invalid."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _time_remaining(target_date_str: str | None) -> str | None:
    """Return human-readable time remaining/overdue, or None if no target."""
    if not target_date_str:
        return None
    try:
        target = datetime.strptime(str(target_date_str), "%Y-%m-%d").date()
        delta = (target - date.today()).days
        if delta > 0:
            return f"{delta}d remaining"
        elif delta == 0:
            return "due today"
        else:
            return f"{abs(delta)}d overdue"
    except ValueError:
        return None


def _normalize_priority(value: str) -> str:
    """Normalize a priority value: accept both legacy P0-P3 and new names."""
    if value in PRIORITY_NAMES:
        return value
    return _LEGACY_TO_NAME.get(value, value)


def _load() -> dict:
    bp = _backlog_path()
    # Peek at version without per-file enrichment so we can dispatch.
    raw = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
    version = _detect_schema_version(raw)
    data = _load_v3(bp) if version >= SCHEMA_V3 else raw
    # Backfill missing 'created' on tasks + normalize legacy priorities (applies to both versions).
    for epic in data.get("epics", []):
        for t in epic.get("tasks", []):
            if not t.get("created"):
                t["created"] = t.get("started") or t.get("completed") or "2025-01-01T00:00"
            pri = t.get("priority", "")
            if pri in _LEGACY_TO_NAME:
                t["priority"] = _LEGACY_TO_NAME[pri]
    return data


def _save(data: dict) -> None:
    with _backlog_lock:
        data["meta"]["updated"] = _today()
        bp = _backlog_path()
        if _detect_schema_version(data) >= SCHEMA_V3:
            _save_v3(bp, data)
        else:
            _atomic_write(
                bp,
                yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
            )


def _has_v3_content(data: dict) -> bool:
    """True when the backlog has any v3 narrative-continuity entity content.

    Independent of `schema_version` marker — used by the heuristic that lets
    skill auto-offers fire on backlogs whose marker was never bumped (ISS-001).
    """
    return bool(
        data.get("handovers")
        or data.get("issues")
        or data.get("lessons_meta")
    )


def _effective_schema_version(data: dict) -> int:
    """schema_version, but treats 'v3 content present, marker missing' as v3.

    Skill gates compare against this so they don't silently skip on backlogs
    that were created with v3 entities before the marker invariant existed.
    """
    declared = _detect_schema_version(data)
    if declared >= SCHEMA_V3:
        return declared
    if _has_v3_content(data):
        return SCHEMA_V3
    return declared


def _ensure_v3_marker(bp: Path) -> None:
    """Set `meta.schema_version: 3` in backlog.yaml when missing.

    Marker-only — does NOT split tasks into per-task files. Called from
    v3 entity-write paths so the marker stays consistent with reality.
    Idempotent. Subsequent saves may migrate task storage via the normal
    v3 dispatch.
    """
    raw = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
    if _detect_schema_version(raw) >= SCHEMA_V3:
        return
    raw.setdefault("meta", {})["schema_version"] = SCHEMA_V3
    _atomic_write(
        bp,
        yaml.dump(raw, default_flow_style=False, sort_keys=False, allow_unicode=True),
    )


def _find_task(data: dict, task_id: str) -> tuple[dict, dict] | None:
    """Returns (task, epic) or None."""
    for epic in data["epics"]:
        for task in epic.get("tasks", []):
            if task["id"] == task_id:
                return task, epic
    return None


def _find_epic(data: dict, epic_id: str) -> dict | None:
    for epic in data["epics"]:
        if epic["id"] == epic_id:
            return epic
    return None


def _find_phase(data: dict, phase_id: str) -> dict | None:
    """Find a phase by ID (exact) or name (case-insensitive, whitespace-normalized)."""
    phases = data.get("phases", [])
    # Exact ID match first
    for ph in phases:
        if ph["id"] == phase_id:
            return ph
    # Fuzzy: case-insensitive name match
    needle = phase_id.strip().lower().replace("-", " ").replace("_", " ")
    for ph in phases:
        name = ph.get("name", "").strip().lower().replace("-", " ").replace("_", " ")
        if name == needle:
            return ph
    # Partial: needle is a substring of the name or vice versa
    for ph in phases:
        name = ph.get("name", "").strip().lower().replace("-", " ").replace("_", " ")
        if needle in name or name in needle:
            return ph
    return None


def _active_phase(data: dict) -> dict | None:
    """Return the currently active phase, or None."""
    for ph in data.get("phases", []):
        if ph.get("status") == "active":
            return ph
    return None


def _phase_task_ids(data: dict, phase_id: str) -> set[str]:
    """Get all task IDs assigned to a phase."""
    ids = set()
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t.get("phase") == phase_id:
                ids.add(t["id"])
    return ids


def _phase_stats(data: dict, phase_id: str) -> dict:
    """Compute stats for a specific phase."""
    counts = {"todo": 0, "in-progress": 0, "in-review": 0, "done": 0, "blocked": 0, "archived": 0}
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t.get("phase") == phase_id:
                s = t.get("status", "todo")
                counts[s] = counts.get(s, 0) + 1
    total = sum(counts.values()) - counts["archived"]
    return {"total": total, **counts}


def _component_rollup(data: dict, epic_id: str) -> dict:
    """Per-component status rollup for an epic, computed on read.

    Returns { <component_key>: {total, done, in-progress, in-review, todo,
    blocked, status}, ..., "_unassigned": {...} }. Components with no tasks
    still appear (status "todo"). `status` is the node color:
      - "done"        : >0 tasks and all done/archived
      - "todo"        : no task started (all todo)
      - "blocked"     : any blocked and none in-progress/in-review
      - "in-progress" : otherwise (work underway)
    """
    epic = _find_epic(data, epic_id)
    declared = list((epic.get("components") or {}) if epic else [])
    buckets: dict[str, dict] = {}

    def _blank() -> dict:
        return {"total": 0, "done": 0, "in-progress": 0, "in-review": 0,
                "todo": 0, "blocked": 0, "archived": 0}

    for key in declared:
        buckets[key] = _blank()
    buckets["_unassigned"] = _blank()

    for t in (epic.get("tasks", []) if epic else []):
        comp = t.get("component")
        key = comp if comp in buckets else "_unassigned"
        st = t.get("status", "todo")
        b = buckets[key]
        b["total"] += 1
        if st in b:
            b[st] += 1

    for b in buckets.values():
        done = b["done"] + b["archived"]
        if b["total"] == 0:
            b["status"] = "todo"
        elif done == b["total"]:
            b["status"] = "done"
        elif b["in-progress"] == 0 and b["in-review"] == 0 and b["blocked"] > 0:
            b["status"] = "blocked"
        elif b["in-progress"] == 0 and b["in-review"] == 0 and done == 0:
            b["status"] = "todo"
        else:
            b["status"] = "in-progress"
    return buckets


def _touch_task(task: dict) -> None:
    """Update last_referenced timestamp on a task."""
    task["last_referenced"] = _now()


def _epic_names(data: dict) -> str:
    return ", ".join(e["id"] for e in data["epics"])


def _days_since(date_str: str | None) -> str:
    if not date_str:
        return "started date unknown"
    try:
        s = str(date_str)
        # Support both YYYY-MM-DD and YYYY-MM-DDTHH:MM
        d = datetime.fromisoformat(s).date() if "T" in s else datetime.strptime(s, "%Y-%m-%d").date()
        delta = (date.today() - d).days
        return f"{delta}d"
    except ValueError:
        return "?"


def _epic_status_label(status: str) -> str:
    return {"active": "Active", "planned": "Planned", "done": "Done"}.get(status, status.title())


def regenerate_context(data: dict) -> None:
    all_tasks = []
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            all_tasks.append((t, epic))

    in_progress = []
    blocked = []
    done_tasks = []
    status_counts = {"done": 0, "in-progress": 0, "in-review": 0, "todo": 0, "blocked": 0, "archived": 0}

    for t, epic in all_tasks:
        s = t.get("status", "todo")
        status_counts[s] = status_counts.get(s, 0) + 1

        if s in ("in-progress", "in-review"):
            entry = {
                "id": t["id"],
                "title": t["title"],
                "epic": epic["id"],
                "branch": t.get("branch", ""),
            }
            if t.get("locked_by"):
                entry["locked_by"] = t["locked_by"]
            in_progress.append(entry)
        elif s == "blocked":
            blocked.append({
                "id": t["id"],
                "title": t["title"],
                "epic": epic["id"],
                "blockers": t.get("blockers", ""),
            })
        elif s == "done" and t.get("completed"):
            done_tasks.append(t)

    # recent_completed: last 5 by completed date
    done_tasks.sort(key=lambda t: str(t.get("completed", "")), reverse=True)
    recent_completed = [
        {"id": t["id"], "title": t["title"], "completed": str(t["completed"])}
        for t in done_tasks[:5]
    ]

    # next_up: top 3 priority todo across active epics, filtered to active phase
    active_ph = _active_phase(data)
    task_statuses: dict[str, str] = {t["id"]: t.get("status", "todo") for t, _ in all_tasks}
    todo_tasks = []
    for t, epic in all_tasks:
        if t.get("status") != "todo" or epic.get("status") != "active":
            continue
        if active_ph and t.get("phase") != active_ph["id"]:
            continue
        deps = t.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        if any(task_statuses.get(d, "todo") != "done" for d in deps):
            continue
        todo_tasks.append((t, epic))
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    todo_tasks.sort(key=lambda x: (priority_order.get(x[0].get("priority", "medium"), 9), str(x[0].get("created", ""))))
    next_up = [
        {"id": t["id"], "title": t["title"], "priority": t.get("priority", "medium"), "epic": epic["id"]}
        for t, epic in todo_tasks[:3]
    ]

    # active_epic: epic with most in-progress tasks, tie-break alphabetically
    epic_ip_counts: dict[str, int] = {}
    for t, epic in all_tasks:
        if t.get("status") in ("in-progress", "in-review"):
            epic_ip_counts[epic["id"]] = epic_ip_counts.get(epic["id"], 0) + 1
    if epic_ip_counts:
        active_epic = sorted(epic_ip_counts.items(), key=lambda x: (-x[1], x[0]))[0][0]
    else:
        # fallback: first active epic alphabetically
        active_epics = sorted([e["id"] for e in data["epics"] if e.get("status") == "active"])
        active_epic = active_epics[0] if active_epics else (data["epics"][0]["id"] if data["epics"] else "")

    data["context"] = {
        "active_epic": active_epic,
        "in_progress": in_progress,
        "blocked": blocked,
        "recent_completed": recent_completed,
        "next_up": next_up,
        "stats": {
            "total": sum(status_counts.values()) - status_counts["archived"],
            "done": status_counts["done"],
            "in_progress": status_counts["in-progress"],
            "in_review": status_counts.get("in-review", 0),
            "todo": status_counts["todo"],
            "blocked": status_counts["blocked"],
            "archived": status_counts["archived"],
        },
    }

    # Phase context
    active_ph = _active_phase(data)
    if active_ph:
        ph_stats = _phase_stats(data, active_ph["id"])
        data["context"]["active_phase"] = {
            "id": active_ph["id"],
            "name": active_ph["name"],
            "stats": ph_stats,
            "target_date": active_ph.get("target_date"),
            "start_date": active_ph.get("start_date"),
        }
    else:
        data["context"]["active_phase"] = None

    # Stale tasks
    stale = []
    for ep in data["epics"]:
        for t in ep.get("tasks", []):
            if t.get("status") != "todo":
                continue
            last_ref = t.get("last_referenced")
            if not last_ref:
                continue
            try:
                ref_str = str(last_ref)
                if "T" in ref_str:
                    ref_date = datetime.fromisoformat(ref_str).date()
                else:
                    ref_date = datetime.strptime(ref_str, "%Y-%m-%d").date()
                days_ago = (date.today() - ref_date).days
                if days_ago >= 14:
                    stale.append({"id": t["id"], "title": t["title"], "last_referenced": ref_str, "days_stale": days_ago})
            except (ValueError, TypeError):
                pass
    stale.sort(key=lambda x: x["days_stale"], reverse=True)
    data["context"]["stale"] = stale[:10]


def regenerate_progress_dashboard(data: dict) -> None:
    """Rewrite PROGRESS.md above the '## Changelog' line."""
    progress_text = _progress_path().read_text(encoding="utf-8")

    changelog_marker = "## Changelog"
    idx = progress_text.find(changelog_marker)
    if idx == -1:
        changelog_section = ""
    else:
        changelog_section = progress_text[idx:]

    project_name = data["meta"].get("project", "Project")

    # Build dashboard
    lines = [
        f"# {project_name} Progress\n",
        "> Auto-generated from backlog.yaml — do not edit manually\n",
        "## Dashboard\n",
        "| Workstream | Status | Progress | Current Focus |",
        "|-----------|--------|----------|---------------|",
    ]

    for epic in data["epics"]:
        tasks = epic.get("tasks", [])
        active_tasks = [t for t in tasks if t.get("status") != "archived"]
        done_count = sum(1 for t in active_tasks if t.get("status") == "done")
        total = len(active_tasks)
        # Current focus: first in-progress task
        focus = "—"
        for t in active_tasks:
            if t.get("status") in ("in-progress", "in-review"):
                focus = t["title"]
                break
        lines.append(f"| {epic['name']} | {_epic_status_label(epic.get('status', 'planned'))} | {done_count}/{total} | {focus} |")

    lines.append("")

    # Phase progress
    phases = data.get("phases", [])
    if phases:
        active_ph = _active_phase(data)
        if active_ph:
            ph_stats = _phase_stats(data, active_ph["id"])
            ph_done = ph_stats["done"]
            ph_total = ph_stats["total"]
            remaining = _time_remaining(active_ph.get("target_date"))
            target_info = f" — target: {active_ph['target_date']}" if active_ph.get("target_date") else ""
            if remaining:
                target_info += f" ({remaining})"
            lines.append(f"**Active Phase:** {active_ph['name']} ({ph_done}/{ph_total} done){target_info}")
        # List all phases briefly
        ph_summary = []
        for ph in sorted(phases, key=lambda m: m.get("order", 999)):
            s = ph.get("status", "planned")
            if s == "archived":
                continue
            label = {"active": ">>", "done": "done", "planned": "..."}.get(s, s)
            ph_summary.append(f"{label} {ph['name']}")
        if ph_summary:
            lines.append(f"**Phases:** {' | '.join(ph_summary)}")
        lines.append("")

    ctx = data.get("context", {})
    ip_items = ctx.get("in_progress", [])
    if ip_items:
        ip_str = ", ".join(f"{t['id']} {t['title']}" for t in ip_items)
        lines.append(f"**In Progress:** {ip_str}")
    else:
        lines.append("**In Progress:** —")

    blocked_items = ctx.get("blocked", [])
    if blocked_items:
        bl_str = ", ".join(f"{t['id']} {t['title']}" for t in blocked_items)
        lines.append(f"**Blocked:** {bl_str}")
    else:
        lines.append("**Blocked:** —")

    next_items = ctx.get("next_up", [])
    if next_items:
        nu_str = ", ".join(f"{t['id']} {t['title']} ({t.get('priority', 'medium')})" for t in next_items)
        lines.append(f"**Next Up:** {nu_str}")
    else:
        lines.append("**Next Up:** —")

    lines.append("\n---\n")

    dashboard = "\n".join(lines) + "\n"
    _progress_path().write_text(dashboard + changelog_section, encoding="utf-8")


def _mutate_and_save(data: dict) -> None:
    """Regenerate context, save YAML, regenerate PROGRESS.md dashboard."""
    regenerate_context(data)
    _save(data)
    regenerate_progress_dashboard(data)


def _enqueue_linear_push_if_synced(task_id: str, task: dict | None = None) -> None:
    """Best-effort: enqueue a Linear sync push if this project has linear.yaml
    AND the task has a Linear tracker_id. Never raises — Linear sync is
    non-fatal to the local mutation.

    Called from post-mutation hooks (backlog_add_task / update_task /
    complete_task / archive_task). The drain runs separately (manually via
    /linear retry, or eventually automatically via session boundaries).

    Tasks without a tracker_id are silently no-op'd — they're not synced.
    Bootstrap (linear-005) is what links a TM task to a Linear issue by
    populating tracker_id.
    """
    try:
        bp = _backlog_path()
        cfg = _load_linear_config(bp)
        if cfg is None:
            return
        if task is None:
            r = _find_task(_load(), task_id)
            if not r:
                return
            task = r[0]
        tracker_id = task.get("tracker_id")
        if not tracker_id or not str(tracker_id).startswith("linear-"):
            return
        from integrations.linear.worker import enqueue as _linear_enqueue
        _linear_enqueue(bp, op="task_upsert", target_id=task_id, tracker_id=tracker_id)
    except Exception:
        # Sync failures must not break the local mutation.
        pass


def _deep_merge(dst: dict, src: dict) -> dict:
    """Recursively merge *src* into *dst* in-place and return *dst*.

    Dict-valued keys are merged recursively; all other values are replaced
    with a deep copy of the source value.
    """
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = deepcopy(v)
    return dst


def _task_context(data: dict, task: dict, epic: dict) -> str:
    """Format task details + epic context for display after pick."""
    lines = [
        f"**Epic:** {epic['name']} — {epic.get('description', '')}",
        f"**Priority:** {task.get('priority', 'medium')}",
    ]
    if task.get("notes"):
        lines.append(f"**Notes:** {task['notes']}")
    if task.get("branch"):
        lines.append(f"**Branch:** {task['branch']}")
    if task.get("blockers"):
        lines.append(f"**Blockers:** {task['blockers']}")
    if task.get("anchors"):
        lines.append(f"**Anchors:** {', '.join(f'`{a}`' for a in task['anchors'])}")
        file_anchors = [a for a in task["anchors"] if not a.startswith(("http", "localhost"))]
        url_anchors = [a for a in task["anchors"] if a.startswith(("http", "localhost"))]
        if file_anchors:
            lines.append(f"  Files: {', '.join(f'`{a}`' for a in file_anchors)}")
        if url_anchors:
            lines.append(f"  URLs: {', '.join(url_anchors)}")

    # Recently completed in same epic
    epic_done = [t for t in epic.get("tasks", []) if t.get("status") == "done"]
    epic_done.sort(key=lambda t: str(t.get("completed", "")), reverse=True)
    if epic_done[:3]:
        lines.append("\n**Recently completed in this epic:**")
        for t in epic_done[:3]:
            lines.append(f"- `{t['id']}` — {t['title']}")

    return "\n".join(lines)


# ── Read-Only Tools ──────────────────────────────────────────


@mcp.tool()
def backlog_status(verbose: bool = False) -> str:
    """Show project dashboard: epic progress table, in-progress tasks, blocked items, next priorities, and stats.

    Args:
        verbose: If True, include archived task count in stats and show up to
            10 stale tasks and all next-up items. Default (slim) mode omits
            archived counts, caps next-up at 5, and caps stale tasks at 3.
    """
    data = _load()
    regenerate_context(data)  # ensure fresh stats without writing
    ctx = data["context"]

    lines = [f"**Schema:** v{_effective_schema_version(data)}\n"]
    lines.append("## Dashboard\n")
    lines.append("| Workstream | Status | Progress | Current Focus |")
    lines.append("|-----------|--------|----------|---------------|")

    for epic in data["epics"]:
        if epic.get("status") == "archived":
            continue
        tasks = epic.get("tasks", [])
        active_tasks = [t for t in tasks if t.get("status") != "archived"]
        done_count = sum(1 for t in active_tasks if t.get("status") == "done")
        total = len(active_tasks)
        focus = "—"
        for t in active_tasks:
            if t.get("status") in ("in-progress", "in-review"):
                focus = t["title"]
                break
        lines.append(f"| {epic['name']} | {_epic_status_label(epic.get('status', 'planned'))} | {done_count}/{total} | {focus} |")

    lines.append("")

    # In Progress — split by actual status
    ip = ctx.get("in_progress", [])
    if ip:
        actual_ip = []
        actual_ir = []
        for item in ip:
            result = _find_task(data, item["id"])
            if result and result[0].get("status") == "in-review":
                actual_ir.append((item, result))
            else:
                actual_ip.append((item, result))

        if actual_ip:
            lines.append("**In Progress:**")
            for item, result in actual_ip:
                started = result[0].get("started") if result else None
                lock_label = f" [locked: {item['locked_by']}]" if item.get("locked_by") else ""
                lines.append(f"- `{item['id']}` — {item['title']} ({_days_since(started)}){lock_label}")
        else:
            lines.append("**In Progress:** —")

        if actual_ir:
            lines.append("**In Review (needs your testing):**")
            for item, result in actual_ir:
                started = result[0].get("started") if result else None
                lines.append(f"- `{item['id']}` — {item['title']} ({_days_since(started)})")
    else:
        lines.append("**In Progress:** —")

    # Blocked
    bl = ctx.get("blocked", [])
    if bl:
        lines.append("**Blocked:**")
        for t in bl:
            lines.append(f"- `{t['id']}` — {t['title']}, blocked by: {t.get('blockers', '?')}")
    else:
        lines.append("**Blocked:** —")

    # Next Up — cap at 5 in slim mode
    nu = ctx.get("next_up", [])
    next_up_cap = None if verbose else 5
    if nu:
        lines.append("**Next Up:**")
        for t in (nu if next_up_cap is None else nu[:next_up_cap]):
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('priority', 'medium')})")
    else:
        lines.append("**Next Up:** —")

    # Stats — omit archived count in slim mode
    s = ctx.get("stats", {})
    stats_line = f"\nTotal: {s.get('total', 0)} | Done: {s.get('done', 0)} | In Progress: {s.get('in_progress', 0)} | In Review: {s.get('in_review', 0)} | Active: {s.get('in_progress', 0) + s.get('in_review', 0)} | Todo: {s.get('todo', 0)} | Blocked: {s.get('blocked', 0)}"
    if verbose and s.get("archived", 0):
        stats_line += f" | Archived: {s['archived']}"
    lines.append(stats_line)

    # Phase info
    phases = data.get("phases", [])
    active_ph = _active_phase(data)
    if active_ph:
        ph_stats = _phase_stats(data, active_ph["id"])
        ph_done = ph_stats["done"]
        ph_total = ph_stats["total"]
        remaining = _time_remaining(active_ph.get("target_date"))
        time_note = f" — {remaining}" if remaining else ""
        lines.append(f"\n**Active Phase:** {active_ph['name']} — {ph_done}/{ph_total} tasks done{time_note}")
        if active_ph.get("description"):
            lines.append(f"  {active_ph['description']}")
    if phases:
        lines.append("\n**Phases:**")
        for ph in sorted(phases, key=lambda m: m.get("order", 999)):
            s = ph.get("status", "planned")
            if s == "archived":
                continue
            ph_st = _phase_stats(data, ph["id"])
            marker = {"active": "▶", "done": "✓", "planned": "○"}.get(s, "?")
            target_note = f", target: {ph.get('target_date')}" if ph.get("target_date") else ""
            lines.append(f"- {marker} **{ph['name']}** ({ph_st['done']}/{ph_st['total']}) — {s}{target_note}")

    # Stale tasks (todo tasks not referenced in 14+ days)
    # cap at 3 in slim mode, 10 in verbose
    stale_cap = 10 if verbose else 3
    stale_tasks = []
    for ep in data["epics"]:
        for t in ep.get("tasks", []):
            if t.get("status") != "todo":
                continue
            last_ref = t.get("last_referenced")
            if not last_ref:
                continue
            try:
                ref_str = str(last_ref)
                if "T" in ref_str:
                    ref_date = datetime.fromisoformat(ref_str).date()
                else:
                    ref_date = datetime.strptime(ref_str, "%Y-%m-%d").date()
                days_ago = (date.today() - ref_date).days
                if days_ago >= 14:
                    stale_tasks.append((t, ep, days_ago))
            except (ValueError, TypeError):
                pass
    if stale_tasks:
        stale_tasks.sort(key=lambda x: x[2], reverse=True)
        lines.append(f"\n**Stale tasks** (not referenced in 14+ days):")
        for t, ep, days in stale_tasks[:stale_cap]:
            lines.append(f"- `{t['id']}` — {t['title']} — stale {days}d ({ep['id']})")
        lines.append("*Still relevant? Archive with `backlog_archive_task` or touch to refresh.*")

    # Verbose: show archived tasks explicitly
    if verbose:
        archived_tasks = []
        for ep in data["epics"]:
            for t in ep.get("tasks", []):
                if t.get("status") == "archived":
                    archived_tasks.append((t, ep))
        if archived_tasks:
            lines.append(f"\n**Archived tasks** ({len(archived_tasks)}):")
            for t, ep in archived_tasks[:20]:
                lines.append(f"- `{t['id']}` — {t['title']} ({ep['id']})")

    return "\n".join(lines)


@mcp.tool()
def backlog_list_tasks(
    epic: str = "",
    status: str = "",
    priority: str = "",
    phase: str = "",
    verbose: bool = False,
    limit: int = 50,
) -> str:
    """List tasks with optional filters. Active tasks sort first; output is
    capped at `limit` rows with an overflow footer.

    Args:
        epic: Filter by epic ID
        status: Filter by status: todo, in-progress, in-review, done, blocked
        priority: Filter by priority: critical, high, medium, low
        phase: Filter by phase ID
        verbose: If True, include heavy fields (notes) per task entry. Slim
            (default) shows id, title, tldr, priority, epic, and status only.
        limit: Max rows returned (default 50). 0 = no cap.
    """
    data = _load()
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    status_order = {
        "in-progress": 0,
        "in-review": 1,
        "blocked": 2,
        "todo": 3,
        "done": 4,
        "archived": 5,
    }
    results: list[tuple[int, int, str, str, dict]] = []  # (status_rank, priority_rank, created, formatted, task)
    for ep in data["epics"]:
        if epic and ep["id"] != epic:
            continue
        # Hide tasks in archived epics unless explicitly filtering for archived status
        if not status and ep.get("status") == "archived":
            continue
        for t in ep.get("tasks", []):
            if status and t.get("status") != status:
                continue
            # Hide archived tasks unless explicitly filtering for them
            if not status and t.get("status") == "archived":
                continue
            if priority and t.get("priority") != priority:
                continue
            if phase and t.get("phase") != phase:
                continue
            pri = t.get("priority", "medium")
            entry = f"`{t['id']}` — {t['title']} ({pri}, {ep['id']}, {t.get('status', 'todo')})"
            results.append((
                status_order.get(t.get("status", "todo"), 9),
                priority_order.get(pri, 9),
                str(t.get("created", "")),
                entry,
                t,
            ))

    if not results:
        filters = []
        if epic:
            filters.append(f"epic={epic}")
        if status:
            filters.append(f"status={status}")
        if priority:
            filters.append(f"priority={priority}")
        if phase:
            filters.append(f"phase={phase}")
        return f"No tasks found matching: {', '.join(filters) if filters else 'any'}"

    results.sort(key=lambda x: (x[0], x[1], x[2]))

    total = len(results)
    overflow = 0
    if limit > 0 and total > limit:
        overflow = total - limit
        results = results[:limit]
    header = f"**{total} tasks:**" if not overflow else f"**{total} tasks (showing first {limit}):**"
    footer = (
        f"…{overflow} more tasks — pass status/epic/phase filters or limit=0 for all"
        if overflow
        else ""
    )

    if verbose:
        lines = [header]
        for _, _, _, entry, t in results:
            lines.append(f"- {entry}")
            if t.get("tldr"):
                lines.append(f"  tldr: {t['tldr']}")
            if t.get("notes"):
                lines.append(f"  notes: {t['notes']}")
        if footer:
            lines.append(footer)
        return "\n".join(lines)

    # Slim mode: include tldr inline but omit heavy fields (notes, body)
    lines = [header]
    for _, _, _, entry, t in results:
        tldr = t.get("tldr", "")
        slim_entry = entry
        if tldr:
            slim_entry = f"{entry} — {tldr}"
        lines.append(f"- {slim_entry}")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


@mcp.tool()
def backlog_get_task(
    task_id: str,
    verbose: bool = False,
    sections: list[str] | None = None,
    expand_links: bool = False,
) -> str:
    """Get details for a single task including epic context and related tasks.

    By default returns a slim view (tldr, status, priority, key links) to
    minimise token cost. Use verbose=True for the full body (notes, review
    instructions, docs, spec-review record, epic context). Use sections to
    pull specific named body sections (e.g. ["notes", "spec"]). Use
    expand_links=True to swap dependency/issue/lesson IDs for {id, tldr} pills.

    Note: Unlike the other _get tools (handover/issue/lesson/idea), this tool
    honours expand_links in *both* slim and verbose modes for the depends_on
    field — verbose mode hand-rolls the expansion inline. The other four tools
    treat expand_links as a slim-mode-only feature and silently ignore it when
    verbose=True.

    Args:
        task_id: The task ID (e.g., "ue-plugin-003")
        verbose: If True, include full body fields (notes, review_instructions,
            docs, spec-review, epic context, recently completed tasks).
        sections: Named sections to include (e.g. ["notes", "spec"]).
            Canonical sections for tasks: notes, review_instructions, spec,
            plan, design, analysis, roadmap.
        expand_links: If True, replace bare IDs in depends_on,
            related_issues, related_lessons with {id, tldr} pills.
    """
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result

    # Update staleness tracking
    task["last_referenced"] = _now()
    _save(data)

    bp = _backlog_path()

    # ── sections-only mode ───────────────────────────────────────────────────
    if sections is not None and not sections:
        return "Error: sections=[] requested no sections; pass sections=None for the slim view or name at least one section"
    if sections:
        try:
            sec_data = _resolve_sections(
                task,
                kind="task",
                sections=sections,
                body=task.get(_BODY_KEY, ""),
                project_root=bp.parent.parent if bp.exists() else None,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        lines = [f"## `{task['id']}` — {task['title']}\n"]
        for sec, content in sec_data.items():
            lines.append(f"### {sec}\n{content}")
        return "\n".join(lines)

    # ── slim mode (default) ──────────────────────────────────────────────────
    if not verbose:
        open_handovers = _get_open_handovers_for_task(bp, task_id) if bp.exists() else []
        slim = _slim_entity(task, kind="task", open_handovers=open_handovers or None)

        if expand_links:
            tldr_index = _build_tldr_index(data, project_root=bp.parent.parent if bp.exists() else None)
            for link_field in ("depends_on", "related_issues", "related_lessons"):
                if link_field in slim:
                    ids = slim[link_field]
                    if isinstance(ids, str):
                        ids = [x.strip() for x in ids.split(",") if x.strip()]
                    slim[link_field] = _expand_link_ids(ids, tldr_index)

        lines = [f"## `{slim.pop('id')}` — {slim.pop('title', task.get('title', ''))}\n"]
        for k, v in slim.items():
            lines.append(f"**{k}:** {v}")
        # Plan C: emit grouped typed-links block.
        _append_grouped_links_block(lines, task, bp, expand_links=expand_links)
        return "\n".join(lines)

    # ── verbose mode ─────────────────────────────────────────────────────────
    lines = [f"## `{task['id']}` — {task['title']}\n"]

    fields = [
        ("Status", task.get("status", "todo")),
        ("Priority", task.get("priority", "medium")),
        ("Epic", f"{epic['name']} ({epic['id']})"),
        ("Stage", str(task["stage"]) if task.get("stage") is not None else "—"),
        ("Estimate", task.get("estimate", "—")),
        ("Phase", task.get("phase", "—")),
        ("Anchors", ", ".join(task["anchors"]) if task.get("anchors") else "—"),
        ("Sub-repo", task.get("sub_repo", "—")),
        ("Created", str(task.get("created", "—"))),
        ("Started", str(task.get("started", "—"))),
        ("Completed", str(task.get("completed", "—"))),
        ("Branch", task.get("branch", "—")),
        ("Blockers", task.get("blockers", "—")),
        ("Locked by", task.get("locked_by", "—")),
        ("Review instructions", task.get("review_instructions", "—")),
        ("Notes", task.get("notes", "—")),
    ]
    for label, val in fields:
        if val is not None and str(val) not in ("—", "None", ""):
            lines.append(f"**{label}:** {val}")

    # Show dependencies
    depends_on = task.get("depends_on", [])
    if isinstance(depends_on, str):
        depends_on = [depends_on]
    if depends_on:
        if expand_links:
            tldr_index = _build_tldr_index(data, project_root=bp.parent.parent if bp.exists() else None)
            pills = _expand_link_ids(depends_on, tldr_index)
            lines.append("\n**Depends on:**")
            for pill in pills:
                tldr_str = f" — {pill['tldr']}" if pill.get("tldr") else ""
                lines.append(f"- `{pill['id']}`{tldr_str}")
        else:
            lines.append("\n**Depends on:**")
            for dep_id in depends_on:
                dep_result = _find_task(data, dep_id)
                if dep_result:
                    dep_task, _ = dep_result
                    status = dep_task.get("status", "todo")
                    lines.append(f"- `{dep_id}` — {dep_task['title']} ({status})")
                else:
                    lines.append(f"- `{dep_id}` — NOT FOUND")

    # Show docs references
    task_docs = task.get("docs")
    if task_docs and isinstance(task_docs, dict):
        lines.append("\n**Docs:**")
        for doc_key, doc_path in task_docs.items():
            lines.append(f"- **{doc_key}:** `{doc_path}`")

    # Show epic docs (parent context)
    epic_docs = epic.get("docs")
    if epic_docs and isinstance(epic_docs, dict):
        lines.append("\n**Epic docs:**")
        for doc_key, doc_path in epic_docs.items():
            lines.append(f"- **{doc_key}:** `{doc_path}`")

    # Show spec-review record
    sr = task.get("spec_review")
    if sr and isinstance(sr, dict):
        verdict = sr.get("verdict", "?")
        ts = sr.get("timestamp", "?")
        codex = "yes" if sr.get("codex_used") else "no"
        crit = sr.get("critical_count", 0)
        imp = sr.get("important_count", 0)
        spec_path = sr.get("spec_path", "—")
        lines.append(
            f"\n**Spec review:** {verdict} ({ts}) — codex: {codex}, "
            f"critical: {crit}, important: {imp}, spec: `{spec_path}`"
        )

    # Epic context
    lines.append(f"\n**Epic:** {epic['name']}")
    lines.append(f"**Description:** {epic.get('description', '—')}")

    # Related tasks in same epic
    epic_tasks = epic.get("tasks", [])
    recent_done = [t for t in epic_tasks if t.get("status") == "done"]
    recent_done.sort(key=lambda t: str(t.get("completed", "")), reverse=True)
    if recent_done[:3]:
        lines.append("\n**Recently completed in this epic:**")
        for t in recent_done[:3]:
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('completed', '?')})")

    next_todo = [t for t in epic_tasks if t.get("status") == "todo" and t["id"] != task_id]
    next_todo.sort(key=lambda t: ({"critical": 0, "high": 1, "medium": 2, "low": 3}.get(t.get("priority", "medium"), 9)))
    if next_todo[:3]:
        lines.append("\n**Next todo in this epic:**")
        for t in next_todo[:3]:
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('priority', 'medium')})")

    return "\n".join(lines)


@mcp.tool()
def backlog_search(query: str) -> str:
    """Full-text search across task IDs, titles, notes, branches, and doc paths. Returns matching tasks ranked by relevance.

    Args:
        query: Search text (case-insensitive). Matches against id, title, notes, branch, epic name, and doc paths.
    """
    data = _load()
    q = query.lower()
    scored: list[tuple[int, str]] = []

    for epic in data["epics"]:
        for task in epic.get("tasks", []):
            score = 0
            tid = task.get("id", "")
            title = task.get("title", "")
            notes = task.get("notes", "")
            branch = task.get("branch", "")
            epic_name = epic.get("name", "")
            docs_str = ""
            if isinstance(task.get("docs"), dict):
                docs_str = " ".join(task["docs"].values())

            # Weighted scoring: id/title matches worth more than notes
            if q in tid.lower():
                score += 10
            if q in title.lower():
                score += 8
            if q in epic_name.lower():
                score += 4
            if q in branch.lower():
                score += 3
            if q in docs_str.lower():
                score += 3
            if q in notes.lower():
                score += 1

            if score > 0:
                status = task.get("status", "todo")
                priority = task.get("priority", "medium")
                scored.append((score, f"`{tid}` — {title} ({priority}, {epic['id']}, {status})"))

    if not scored:
        return f"No tasks matching `{query}`"

    scored.sort(key=lambda x: -x[0])
    results = [item for _, item in scored[:15]]
    return f"**{len(scored)} match{'es' if len(scored) != 1 else ''}** for `{query}`:\n" + "\n".join(f"- {r}" for r in results)


@mcp.tool()
def backlog_dependencies(task_id: str) -> str:
    """Show the full dependency chain for a task — what it depends on (upstream) and what it unblocks (downstream).

    Args:
        task_id: The task ID (e.g., "cpp-parser-003")
    """
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    lines = [f"## Dependencies for `{task_id}` — {task['title']}\n"]

    # Upstream: what this task depends on
    depends_on = task.get("depends_on", [])
    if isinstance(depends_on, str):
        depends_on = [depends_on]

    if depends_on:
        lines.append("**Depends on (upstream):**")
        all_met = True
        for dep_id in depends_on:
            dep_result = _find_task(data, dep_id)
            if dep_result:
                dep_task, dep_epic = dep_result
                status = dep_task.get("status", "todo")
                check = "done" if status == "done" else "pending"
                if status != "done":
                    all_met = False
                lines.append(f"- [{check}] `{dep_id}` — {dep_task['title']} ({status})")
            else:
                all_met = False
                lines.append(f"- [missing] `{dep_id}` — NOT FOUND")
        lines.append(f"\nAll dependencies met: **{'Yes' if all_met else 'No'}**")
    else:
        lines.append("**Depends on:** none")

    # Downstream: what depends on this task
    all_tasks = []
    for ep in data["epics"]:
        for t in ep.get("tasks", []):
            all_tasks.append((t, ep))

    downstream = []
    for t, ep in all_tasks:
        deps = t.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        if task_id in deps:
            downstream.append((t, ep))

    if downstream:
        lines.append("\n**Unblocks (downstream):**")
        for t, ep in downstream:
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('status', 'todo')})")
    else:
        lines.append("\n**Unblocks:** nothing")

    return "\n".join(lines)


@mcp.tool()
def backlog_next_available(include_future_phases: bool = False) -> str:
    """Show tasks that are ready to work on — todo tasks in active epics with all dependencies satisfied.
    Sorted by priority, then by creation date. By default only shows tasks from the active phase;
    set include_future_phases=true to see tasks from all phases."""
    data = _load()
    active_ph = _active_phase(data)

    # Build lookup of all task statuses
    task_status: dict[str, str] = {}
    for epic in data["epics"]:
        for task in epic.get("tasks", []):
            task_status[task["id"]] = task.get("status", "todo")

    available: list[tuple[dict, dict]] = []
    blocked_by_deps: list[tuple[dict, dict, list[str]]] = []

    for epic in data["epics"]:
        if epic.get("status") != "active":
            continue
        for task in epic.get("tasks", []):
            if task.get("status") != "todo":
                continue
            # Filter by active phase unless future phases requested
            if active_ph and not include_future_phases and task.get("phase") != active_ph["id"]:
                continue

            # Check dependencies
            deps = task.get("depends_on", [])
            if isinstance(deps, str):
                deps = [deps]

            unmet = [d for d in deps if task_status.get(d, "todo") != "done"]
            if unmet:
                blocked_by_deps.append((task, epic, unmet))
            else:
                available.append((task, epic))

    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    available.sort(key=lambda x: (priority_order.get(x[0].get("priority", "medium"), 9), str(x[0].get("created", ""))))

    lines = ["## Available Tasks\n"]

    if active_ph:
        lines.append(f"*Filtered to phase: **{active_ph['name']}***\n")

    if available:
        lines.append(f"**{len(available)} tasks ready to pick:**")
        for task, epic in available:
            lines.append(f"- `{task['id']}` — {task['title']} ({task.get('priority', 'medium')}, {epic['id']})")
    else:
        if active_ph:
            lines.append(f"No tasks available in phase **{active_ph['name']}** — all tasks are done, in progress, or have unmet dependencies.")
        else:
            lines.append("No tasks available — all todo tasks have unmet dependencies or belong to non-active epics.")

    if blocked_by_deps:
        lines.append(f"\n**{len(blocked_by_deps)} tasks blocked by dependencies:**")
        for task, epic, unmet in blocked_by_deps[:5]:
            unmet_str = ", ".join(f"`{d}`" for d in unmet)
            lines.append(f"- `{task['id']}` — {task['title']} (waiting on {unmet_str})")

    # Show unassigned tasks hint
    if active_ph:
        unassigned = []
        for epic in data["epics"]:
            if epic.get("status") != "active":
                continue
            for task in epic.get("tasks", []):
                if task.get("status") == "todo" and not task.get("phase"):
                    unassigned.append(task)
        if unassigned:
            lines.append(f"\n*{len(unassigned)} todo tasks are not assigned to any phase.*")

    return "\n".join(lines)


@mcp.tool()
def backlog_validate() -> str:
    """Check backlog integrity: dangling dependency refs, missing dates on done tasks,
    docs paths that don't exist on disk, circular deps, and status inconsistencies."""
    data = _load()

    # Build task ID set and lookup
    all_task_ids: set[str] = set()
    all_tasks: list[tuple[dict, dict]] = []
    for epic in data["epics"]:
        for task in epic.get("tasks", []):
            all_task_ids.add(task["id"])
            all_tasks.append((task, epic))

    issues: list[str] = []

    for task, epic in all_tasks:
        tid = task["id"]

        # 1. Done tasks should have completed date
        if task.get("status") == "done" and not task.get("completed"):
            issues.append(f"`{tid}`: status=done but no `completed` date")

        # 2. In-progress tasks should have started date
        if task.get("status") == "in-progress" and not task.get("started"):
            issues.append(f"`{tid}`: status=in-progress but no `started` date")

        # 3. Dangling dependency references
        deps = task.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        for dep_id in deps:
            if dep_id not in all_task_ids:
                issues.append(f"`{tid}`: depends_on `{dep_id}` which does not exist")

        # 4. Docs paths that don't exist on disk
        docs = task.get("docs")
        if isinstance(docs, dict):
            for doc_key, doc_path in docs.items():
                # Strip fragment anchor (e.g., #task-6) before path check
                clean_path = doc_path.split("#")[0].strip()
                if not clean_path:
                    issues.append(f"`{tid}`: docs.{doc_key} is empty")
                    continue
                # Skip entries that look like notes rather than paths
                if " " in clean_path and not clean_path.endswith(".md"):
                    issues.append(f"`{tid}`: docs.{doc_key} looks like a note, not a path: `{doc_path[:60]}...`")
                    continue
                full_path = ROOT / clean_path
                if not full_path.exists():
                    issues.append(f"`{tid}`: docs.{doc_key} path not found: `{clean_path}`")

        # 5. Self-dependency
        if tid in deps:
            issues.append(f"`{tid}`: depends on itself")

    # 6. Circular dependency detection (DFS)
    dep_graph: dict[str, list[str]] = {}
    for task, _ in all_tasks:
        deps = task.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        dep_graph[task["id"]] = [d for d in deps if d in all_task_ids]

    visited: set[str] = set()
    in_stack: set[str] = set()
    cycles_found: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        if node in in_stack:
            cycle_start = path.index(node)
            cycles_found.append(path[cycle_start:] + [node])
            return
        if node in visited:
            return
        visited.add(node)
        in_stack.add(node)
        for neighbor in dep_graph.get(node, []):
            dfs(neighbor, path + [node])
        in_stack.discard(node)

    for tid in dep_graph:
        if tid not in visited:
            dfs(tid, [])

    for cycle in cycles_found:
        issues.append(f"Circular dependency: {' → '.join(f'`{c}`' for c in cycle)}")

    # 7. Phase validation
    for task, epic in all_tasks:
        tid = task["id"]
        # 8. Phase references that don't exist
        task_ph = task.get("phase")
        if task_ph and not _find_phase(data, task_ph):
            issues.append(f"`{tid}`: phase `{task_ph}` does not exist")

    # 9. Tracker validation: each tracker file's frontmatter is well-formed.
    bp = _backlog_path()
    on_disk_tracker_ids: set[str] = set(_list_tracker_ids(bp))
    for trk_id in on_disk_tracker_ids:
        try:
            fm, _ = _read_tracker(bp, trk_id)
        except OSError as e:
            issues.append(f"tracker `{trk_id}`: cannot read file ({e})")
            continue
        except yaml.YAMLError as e:
            issues.append(f"tracker `{trk_id}`: malformed YAML ({e})")
            continue
        try:
            _validate_tracker_fm(fm)
        except ValueError as e:
            issues.append(f"tracker `{trk_id}`: {e}")

    # 10. Task tracker_id references: must point at a tracker that exists on disk.
    #     Closed-in-Jira trackers stay on disk so this catches typos and bit-rot.
    for task, _epic in all_tasks:
        ref = task.get("tracker_id")
        if ref and ref not in on_disk_tracker_ids:
            issues.append(
                f"`{task['id']}`: tracker_id `{ref}` does not match any tracker file"
            )

    # 11. Issue tracker_id references: same rule as tasks.
    for iss in data.get("issues", []) or []:
        ref = iss.get("tracker_id")
        if ref and ref not in on_disk_tracker_ids:
            issues.append(
                f"issue `{iss.get('id', '?')}`: tracker_id `{ref}` does not match any tracker file"
            )

    # 12. Linear config: validate schema if .taskmaster/linear.yaml exists.
    #     Catches duplicate aliases / token_envs / dangling default_workspace
    #     before they cause runtime sync failures.
    try:
        _load_linear_config(bp)
    except OSError as e:
        issues.append(f"linear.yaml: cannot read file ({e})")
    except yaml.YAMLError as e:
        issues.append(f"linear.yaml: malformed YAML ({e})")
    except ValueError as e:
        issues.append(f"linear.yaml: {e}")

    # Stats summary
    stats = {"total": len(all_tasks), "issues": len(issues)}

    # ── tldr warnings (advisory, not blocking) ─────────────────────────────
    warnings: list[str] = []
    for epic in data.get("epics", []):
        for task in epic.get("tasks", []):
            if not task.get("tldr"):
                warnings.append(
                    f"  warning: task {task['id']} missing tldr — run scripts/backfill_tldr.py"
                )

    # Also scan artifact dirs for missing tldr
    bp = _backlog_path()
    tm_dir = bp.parent
    from taskmaster_v3 import read_task_file as _rtf
    for subdir in ("issues", "lessons", "handovers", "ideas"):
        d = tm_dir / subdir
        if not d.exists():
            continue
        for path in sorted(d.glob("*.md")):
            try:
                fm, _ = _rtf(path)
            except Exception:
                continue
            if fm.get("id") and not fm.get("tldr"):
                warnings.append(
                    f"  warning: {fm['id']} missing tldr — run scripts/backfill_tldr.py"
                )

    output_parts: list[str] = []
    if issues:
        header = f"**{len(issues)} issue{'s' if len(issues) != 1 else ''} found** across {stats['total']} tasks:\n"
        output_parts.append(header + "\n".join(f"- {i}" for i in issues))
    else:
        output_parts.append(f"All clear — {stats['total']} tasks validated, no issues found.")

    if warnings:
        output_parts.append("## Warnings\n" + "\n".join(warnings))

    return "\n\n".join(output_parts)


# ── Mutating Tools ───────────────────────────────────────────


@mcp.tool()
def backlog_init(project_name: str = "", location: str = "tracked", schema_version: int = 0) -> str:
    """Initialize taskmaster in the current project. Creates config, backlog.yaml, and PROGRESS.md.

    Args:
        project_name: Name for the project. Defaults to the directory name.
        location: Retained for backwards-compatibility. Only "tracked" is accepted —
                  taskmaster always writes to `.taskmaster/` now. Existing
                  `.claude/`-layout projects keep working via the resolver shim;
                  run `backlog_canonicalize_layout` to migrate them.
        schema_version: 2 (stable, single backlog.yaml) or 3 (latest, slim index +
                  per-task files + handovers/issues/lessons/auto). 0 → use SCHEMA_DEFAULT.
                  v3 init creates the directory layout up front (tasks/, handovers/,
                  lessons/, issues/, snapshots/, auto/).
    """
    if location == "hidden":
        return (
            "Error: 'hidden' location is no longer supported — taskmaster writes "
            "to `.taskmaster/` now. Re-run with location='tracked' (default), or "
            "if you have an existing `.claude/`-layout project, run "
            "`backlog_canonicalize_layout` to migrate it."
        )
    if location != "tracked":
        return f"Error: location must be 'tracked', got '{location}'"
    if schema_version == 0:
        schema_version = SCHEMA_DEFAULT
    if schema_version not in (SCHEMA_V2, SCHEMA_V3):
        return f"Error: schema_version must be {SCHEMA_V2} or {SCHEMA_V3}, got {schema_version}"

    if not project_name:
        project_name = ROOT.name

    # Check if already initialized (check all locations, including legacy .claude/)
    for check_path in [ROOT / ".taskmaster" / "backlog.yaml", ROOT / ".claude" / "backlog.yaml", ROOT / "backlog.yaml"]:
        if check_path.exists():
            rel = check_path.relative_to(ROOT)
            hint = ""
            if check_path.parts[-2:] == (".claude", "backlog.yaml"):
                hint = (
                    "\nNote: this is a legacy `.claude/`-layout project. "
                    "Run `backlog_canonicalize_layout` to migrate it into `.taskmaster/`."
                )
            return (
                f"Already initialized — `backlog.yaml` exists at `{rel}`.\n"
                f"Use `backlog_status` to see the current state.{hint}"
            )

    backlog_rel = ".taskmaster/backlog.yaml"
    progress_rel = ".taskmaster/PROGRESS.md"

    backlog_abs = ROOT / backlog_rel
    progress_abs = ROOT / progress_rel

    created = []

    # Write config so the server knows where to find files
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = {"backlog_path": backlog_rel, "progress_path": progress_rel}
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    created.append(".taskmaster/taskmaster.json")

    # Create backlog.yaml
    backlog_abs.parent.mkdir(parents=True, exist_ok=True)
    initial_data = {
        "meta": {
            "project": project_name,
            "schema_version": schema_version,
            "updated": _today(),
        },
        "context": {
            "active_epic": "",
            "in_progress": [],
            "blocked": [],
            "recent_completed": [],
            "next_up": [],
            "stats": {"total": 0, "done": 0, "in_progress": 0, "in_review": 0, "todo": 0, "blocked": 0, "archived": 0},
        },
        "epics": [],
        "phases": [],
    }
    if schema_version >= SCHEMA_V3:
        # v3 adds top-level entity indexes + creates the directory layout.
        initial_data["handovers"] = []
        initial_data["issues"] = []
        initial_data["lessons_meta"] = []
        # Pre-create directories so first-run tooling has somewhere to write.
        for sub in ("tasks", "handovers", "lessons", "issues", "snapshots", "auto"):
            (backlog_abs.parent / sub).mkdir(parents=True, exist_ok=True)

    backlog_abs.write_text(
        yaml.dump(initial_data, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    created.append(backlog_rel)

    # Create PROGRESS.md
    progress_content = f"# {project_name} Progress\n\n> Auto-generated from backlog.yaml — do not edit manually\n\n## Dashboard\n\n---\n\n## Changelog\n"
    progress_abs.write_text(progress_content, encoding="utf-8")
    created.append(progress_rel)

    schema_label = "v3 (latest — narrative continuity)" if schema_version >= SCHEMA_V3 else "v2 (stable)"
    return (
        f"Initialized taskmaster for **{project_name}** in `.taskmaster/` (trackable in git) on schema {schema_label}.\n"
        f"Created: {', '.join(created)}"
    )


@mcp.tool()
def backlog_migrate_v3() -> str:
    """Migrate this project's backlog to v3 layout (slim index + per-task files).

    v3 introduces narrative-continuity features (handovers, lessons, issues, recap,
    auto modes). The on-disk shape changes: heavy task fields (description, notes,
    docs, review_instructions) move out of `backlog.yaml` into per-task files at
    `tasks/<id>.md`. Slim metadata (id, title, status, priority, etc.) stays in
    `backlog.yaml` as the index.

    The migration is idempotent — running on a v3 backlog is a no-op. The in-memory
    shape is identical across versions, so existing tools/skills keep working.
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    summary = _migrate_v2_to_v3(bp)

    # Flip the viewer to v3 mode. Both freshly migrated and already-v3 projects
    # should serve the v3 viewer — otherwise the migration completes but the
    # user still sees the v2 UI (no Issues / Lessons / Sessions tabs).
    try:
        prefs = load_viewer_prefs()
        if not prefs.get("use_v3"):
            prefs["use_v3"] = True
            save_viewer_prefs(prefs)
    except Exception:
        pass

    if summary["status"] == "already_v3":
        return (
            f"Already on v3 — {summary['tasks_total']} tasks, no changes made.\n"
            f"Backlog at: {bp.relative_to(ROOT)}"
        )
    files = summary["task_files_written"]
    files_msg = (
        f"Wrote {len(files)} per-task files under `tasks/`."
        if files
        else "No tasks had heavy content — only the index was rewritten."
    )
    return (
        f"Migrated v2 → v3.\n"
        f"- Tasks: {summary['tasks_total']}\n"
        f"- {files_msg}\n"
        f"- Index: {bp.relative_to(ROOT)}\n"
        f"- Viewer: switched to v3 UI (use_v3=true)\n"
        f"\nv3 features (handovers, lessons, issues, recap, auto modes) will land in "
        f"subsequent slices."
    )


@mcp.tool()
def backlog_backfill_lanes(grandfather_active: bool = True) -> str:
    """One-time optional migration: assign a `lane` (by priority) to every task that
    lacks one. For tasks already in-progress/in-review/done, mark their lane's required
    gates skipped(reason="grandfathered") so enforcement never retroactively wedges
    in-flight work. Tasks that already have a lane are left untouched.

    Args:
        grandfather_active: if true, grandfather gates on non-todo tasks (recommended).
    """
    data = _load()
    migrated = 0
    for epic in data.get("epics", []):
        for task in epic.get("tasks", []):
            if task.get("lane"):
                continue
            task["lane"] = _default_lane(task.get("priority", "medium"))
            if grandfather_active and task.get("status") in ("in-progress", "in-review", "done"):
                gates = task.setdefault("gates", {})
                for g in _required_gates(task["lane"]):
                    if not _gate_satisfied(gates.get(g)):
                        gates[g] = {"skipped": True, "reason": "grandfathered",
                                    "by": "migration", "at": _now()}
            task["gate_state"] = _compute_gate_state(task)
            migrated += 1
    if migrated:
        _mutate_and_save(data)
    return f"Backfilled lanes on {migrated} task(s) (grandfather_active={grandfather_active})."


@mcp.tool()
def backlog_canonicalize_layout(dry_run: bool = False) -> str:
    """Migrate the v3 backlog from `.claude/` or root layout into canonical `.taskmaster/`.

    Moves backlog.yaml + the artifact subdirs (tasks, handovers, lessons, issues,
    recaps, snapshots, auto, PROGRESS.md, viewer.json) into `.taskmaster/`.
    Idempotent: re-running on a canonical layout is a no-op. Refuses to clobber:
    if a destination file already holds different content, nothing moves and the
    conflicts are reported. After a successful `.claude/` migration, the redundant
    `.claude/taskmaster.json` config is deleted.

    Use this once per project to fix ISS-004 silent divergence between the v3
    handover writer (which uses `bp.parent / "handovers"`) and readers that
    historically hard-coded `.taskmaster/`.

    Args:
        dry_run: When true, returns the move plan without modifying anything.
    """
    from taskmaster_v3 import canonicalize_layout
    summary = canonicalize_layout(ROOT, dry_run=dry_run)
    status = summary["status"]

    if status == "no_backlog":
        return "No backlog.yaml found — nothing to canonicalize."
    if status == "already_canonical":
        return f"Already canonical at `{summary['destination']}` — no changes."
    if status == "ambiguous":
        srcs = ", ".join(summary.get("sources_found", []))
        return (
            f"Ambiguous: multiple backlog.yaml files exist ({srcs}). Resolve by "
            f"keeping only one before canonicalizing."
        )
    if status == "conflicts":
        lines = [
            f"  {c['src']}  →  {c['dst']}" for c in summary["conflicts"]
        ]
        return (
            f"Conflicts — destination already holds different content. "
            f"Nothing moved. Resolve manually:\n" + "\n".join(lines)
        )
    if status == "would_migrate":
        moves = summary.get("would_move", [])
        lines = [f"  {m['src']}  →  {m['dst']}" for m in moves]
        head = (
            f"Dry run: would move {len(moves)} file(s) from `{summary['source']}` "
            f"layout into `{summary['destination']}`."
        )
        if summary["skipped_already_at_dst"]:
            head += (
                f"\n{len(summary['skipped_already_at_dst'])} file(s) already at "
                f"destination would be cleaned up."
            )
        return head + ("\n" + "\n".join(lines) if lines else "")
    # status == "migrated"
    moved = summary["moved"]
    out = [
        f"Canonicalized v3 layout: `{summary['source']}` → `.taskmaster/`.",
        f"Moved {len(moved)} file(s).",
    ]
    if summary["skipped_already_at_dst"]:
        out.append(
            f"Cleaned up {len(summary['skipped_already_at_dst'])} duplicate file(s) "
            f"already at destination."
        )
    if summary["deleted_config"]:
        out.append(f"Deleted redundant config: `{summary['deleted_config']}`.")
    return "\n".join(out)


@mcp.tool()
def backlog_snapshot(quiet: bool = False) -> str:
    """Capture a slim snapshot of the backlog for later recap diffing.

    Snapshots are written to `<backlog_dir>/snapshots/last.json` (gitignored).
    Each snapshot tracks per-task status/priority/stage/epic and the active
    phase — enough to compute a "what changed since" diff without storing
    full backlog history.

    Args:
        quiet: When true, return an empty string on success (use for hooks).
               Errors still surface.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "" if quiet else "No backlog found — nothing to snapshot."
    try:
        data = _load()
    except Exception as exc:
        return f"snapshot failed: {exc}"
    snap = _take_snapshot(data)
    sp = _write_snapshot(bp, snap)
    if quiet:
        return ""
    return f"Snapshot written: {sp.relative_to(ROOT)} ({snap['structural_hash'][:18]}…)"


@mcp.tool()
def backlog_recap() -> str:
    """Show what changed in the backlog since the last snapshot.

    Compares the current backlog state against `<backlog_dir>/snapshots/last.json`
    and renders a compact diff: tasks added/removed, status/priority/stage/epic
    changes, and active-phase transitions. Returns guidance text when no prior
    snapshot exists.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    try:
        data = _load()
    except Exception as exc:
        return f"recap failed: {exc}"
    prev = _read_snapshot(bp)
    diff = _diff_against_snapshot(data, prev)
    return _format_recap(diff)


@mcp.tool()
def backlog_handover_create(
    tldr: str,
    next_action: str = "",
    body: str = "",
    task_ids: list[str] | None = None,
    session_kind: str = "end-of-day",
    context_size_at_write: str = "",
    supersedes: str = "",
    branch: str = "",
    tip_commit: str = "",
    flag_for_review: bool = False,
    review_reason: str = "",
) -> str:
    """Write a session handover — committed markdown artifact for cross-session
    continuity.

    Use to capture the unwritten context at the end of a long session: decisions
    made, blockers, where to start tomorrow. The body is freeform markdown
    (suggested sections: Decisions, Blockers, Where I'd start, Open threads).

    Args:
        tldr: One-line summary. Required.
        next_action: One-line "where to start next session." Optional but useful.
        body: Markdown body (the four-section narrative).
        task_ids: Tasks this handover relates to (surfaces in pick-task).
        session_kind: One of {", ".join(HANDOVER_KINDS)}.
        context_size_at_write: Optional marker for compaction-prompted handovers.
        supersedes: Optional id of an older handover this one supersedes. When
            set, the new handover records `supersedes:` in its frontmatter and
            the old handover gets a `superseded_by:` field plus a SUPERSEDED
            callout prepended to its body. Use for milestone-complete or pivot
            chains.
        branch: Optional git branch name. Lands in frontmatter when set.
        tip_commit: Optional tip commit SHA (short or long). Lands in
            frontmatter when set.
        flag_for_review: When True, marks this handover for retro extraction in
            the next lesson-sweep session. Sets `flag_for_review: true` and
            `review_reason` in the handover's frontmatter.
        review_reason: Free-text reason for the review flag (e.g. "multi-tab
            fanout retro"). Only used when `flag_for_review` is True.
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    _ensure_handover_status_backfilled()
    try:
        hid, target = _write_handover(
            bp,
            tldr=tldr,
            next_action=next_action,
            body=body,
            task_ids=task_ids or [],
            session_kind=session_kind,
            context_size_at_write=context_size_at_write or None,
            supersedes=supersedes or None,
            branch=branch or None,
            tip_commit=tip_commit or None,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    superseded_warning = None
    if supersedes:
        try:
            _apply_supersession(bp, old_id=supersedes, new_id=hid)
        except FileNotFoundError:
            superseded_warning = (
                f"WARNING: supersedes={supersedes} not found on disk; old "
                f"handover not updated."
            )

    if flag_for_review:
        try:
            _apply_handover_review_flag(
                bp, handover_id=hid, review_reason=review_reason or ""
            )
        except FileNotFoundError as exc:
            return f"Error: handover not found: {exc}."

    data = _load()
    _sync_handover_index(data, bp)
    _save(data)
    _ensure_v3_marker(bp)

    # Plan C: auto-detect inline ID mentions, materialize as `references` links.
    from taskmaster_v3 import auto_link_on_save as _auto_link_on_save
    try:
        _auto_link_on_save(bp, hid)
    except Exception:
        pass

    lines = [
        f"Handover written: {hid}",
        f"- File: {target.relative_to(ROOT)}",
        f"- Index entries: {len(data.get('handovers') or [])}",
    ]
    if supersedes and not superseded_warning:
        lines.append(f"- Superseded: {supersedes}")
    if superseded_warning:
        lines.append(f"- {superseded_warning}")
    if flag_for_review:
        lines.append(f"- Flagged for review: {review_reason}")
    return "\n".join(lines)


@mcp.tool()
def backlog_handover_list(
    task_id: str = "",
    session_kind: str = "",
    since: str = "",
    status: str = "all",
    limit: int = 10,
    verbose: bool = False,
) -> str:
    """List recent handovers. By default shows slim one-liners (id, date, tldr).

    Reads from the backlog.yaml index, which is bounded to the most recent 30.
    Older handovers are still on disk under handovers/_archive/ but not listed
    here — fetch by id with `backlog_handover_get` if needed.

    Args:
        task_id: If set, only entries whose `task_ids` list contains this id.
        session_kind: If set, only entries with this session_kind
            (e.g. "end-of-day", "context-handoff", "milestone-complete").
        since: ISO date string (YYYY-MM-DD). If set, only entries whose
            date prefix is >= since. Raises ValueError for invalid formats.
        status: One of open, closed, superseded, or "all" (default). Filters
            against the index entry — does not read every file.
        limit: Maximum number of entries to return after filtering (default 10).
        verbose: If True, include additional index fields (next_action, task_ids,
            status) per entry. Slim (default) shows id, date, kind, and tldr.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    _ensure_handover_status_backfilled()
    data = _load()
    entries = list(data.get("handovers") or [])

    # Validate `since` before filtering so we fail fast on bad input.
    if since:
        from datetime import date as _date
        try:
            _date.fromisoformat(since)
        except ValueError:
            return f"Error: `since` must be a date in YYYY-MM-DD format, got {since!r}."

    # Apply filters in spec order.
    if task_id:
        entries = [e for e in entries if task_id in e.get("task_ids", [])]
    if session_kind:
        entries = [e for e in entries if e.get("session_kind") == session_kind]
    if since:
        entries = [e for e in entries if e.get("date", e.get("id", "")) >= since]

    from taskmaster_v3 import HANDOVER_STATUSES as _STATUSES
    if status and status != "all":
        if status not in _STATUSES:
            return f"Error: status must be one of {_STATUSES} or 'all', got {status!r}."
        entries = [e for e in entries if e.get("status") == status]

    if limit < 1:
        return f"Error: limit must be >= 1, got {limit}."

    # Truncate to limit after all filters.
    entries = entries[:limit]

    if not entries:
        filtered = any([task_id, session_kind, since, status != "all"])
        return "No handovers match those filters." if filtered else "No handovers yet."

    lines = []
    for e in entries:
        kind = e.get("session_kind", "")
        tag = f" [{kind}]" if kind else ""
        when = e.get("created") or e.get("date") or ""
        when_tag = f" ({when})" if when else ""
        flag = e.get("flag_reason", "")
        flag_tag = f" ▸ FLAGGED: {flag}" if flag else ""
        lines.append(f"- {e['id']}{when_tag}{tag} — {e.get('tldr', '')}{flag_tag}")
        if verbose:
            if e.get("next_action"):
                lines.append(f"  next: {e['next_action']}")
            tids = e.get("task_ids") or []
            if tids:
                lines.append(f"  tasks: {', '.join(tids)}")
            if e.get("status"):
                lines.append(f"  status: {e['status']}")
    return "\n".join(lines)


@mcp.tool()
def backlog_handover_get(
    handover_id: str,
    verbose: bool = False,
    sections: list[str] | None = None,
    expand_links: bool = False,
) -> str:
    """Read a handover's content.

    By default returns a slim view (frontmatter fields only, no body) to
    minimise token cost. Use verbose=True to include the full markdown body.
    Use sections to pull specific named body sections (decisions, notes,
    blockers, where_id_start). Use expand_links=True to expand task_ids to
    {id, tldr} pills.

    Note: expand_links is a slim-mode feature. When verbose=True, the full
    frontmatter is rendered as-is and expand_links is silently ignored. To
    get expanded links use slim mode (verbose=False) with expand_links=True.

    Use when start-session shows a handover tldr that you want to read in full,
    or when picking a task that has linked handovers.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    if verbose and expand_links:
        # expand_links is a slim-mode feature; in verbose mode the full frontmatter
        # is rendered as-is (no ID→pill substitution). To get expanded links, use
        # slim mode (verbose=False) with expand_links=True.
        pass  # silently ignore expand_links in verbose
    _ensure_handover_status_backfilled()
    try:
        fm, body = _read_handover(bp, handover_id)
    except FileNotFoundError:
        # Maybe it's archived?
        archive_root = bp.parent / "handovers" / "_archive"
        candidates = list(archive_root.rglob(f"{handover_id}.md")) if archive_root.exists() else []
        if not candidates:
            return f"Handover not found: {handover_id}"
        from taskmaster_v3 import read_task_file as _read_task_file
        fm, body = _read_task_file(candidates[0])

    # ── sections-only mode ───────────────────────────────────────────────────
    if sections is not None and not sections:
        return "Error: sections=[] requested no sections; pass sections=None for the slim view or name at least one section"
    if sections:
        try:
            sec_data = _resolve_sections(fm, kind="handover", sections=sections, body=body)
        except ValueError as exc:
            return f"Error: {exc}"
        lines = [f"## Handover: {handover_id}\n"]
        for sec, content in sec_data.items():
            lines.append(f"### {sec}\n{content}")
        return "\n".join(lines)

    # ── verbose mode ─────────────────────────────────────────────────────────
    if verbose:
        fm_lines = [f"  {k}: {v}" for k, v in fm.items()]
        return "---\n" + "\n".join(fm_lines) + "\n---\n" + body

    # ── slim mode (default) ──────────────────────────────────────────────────
    slim = _slim_entity(fm, kind="handover")

    if expand_links:
        data = _load()
        tldr_index = _build_tldr_index(data, project_root=bp.parent.parent if bp.exists() else None)
        task_ids = slim.get("task_ids") or []
        if task_ids:
            slim["task_ids"] = _expand_link_ids(task_ids, tldr_index)

    lines = [f"## Handover: {slim.pop('id', handover_id)}\n"]
    for k, v in slim.items():
        lines.append(f"**{k}:** {v}")
    # Plan C: emit grouped typed-links block.
    _append_grouped_links_block(lines, fm, bp, expand_links=expand_links)
    return "\n".join(lines)


@mcp.tool()
def backlog_handover_latest() -> str:
    """[DEPRECATED] Alias for backlog_handover_list(status="open", limit=1, sort="created_desc").

    Use `backlog_handover_list(status="open")` instead — it returns all in-flight
    handover tracks, not just the newest one. This alias will be removed in the
    next major release.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    _ensure_handover_status_backfilled()
    data = _load()
    entries = list(data.get("handovers") or [])
    open_entries = [e for e in entries if e.get("status") == "open"]
    # Sort by created descending; fall back to id for stable ordering.
    open_entries.sort(key=lambda e: (e.get("created") or e.get("id") or ""), reverse=True)

    deprecation_notice = (
        "[DEPRECATED] backlog_handover_latest is an alias — "
        "use backlog_handover_list(status=\"open\") for all open tracks.\n\n"
    )

    if not open_entries:
        return deprecation_notice + "No open handovers."

    e = open_entries[0]
    when_line = e.get("created") or e.get("date") or ""
    when_label = f" ({when_line})" if when_line else ""
    return (
        deprecation_notice
        + f"Latest open handover: {e['id']}{when_label}\n"
        + f"- TLDR: {e.get('tldr', '')}\n"
        + f"- Next: {e.get('next_action', '(none)')}\n"
        + f"- Tasks: {', '.join(e.get('task_ids') or []) or '(none)'}\n"
        + f"- Kind: {e.get('session_kind', 'end-of-day')}\n"
        + f"\nFetch body with `backlog_handover_get {e['id']}`.\n"
        + f"List all open tracks with `backlog_handover_list(status=\"open\")`."
    )


@mcp.tool()
def backlog_handover_resync() -> str:
    """Rebuild the handover index in backlog.yaml from disk.

    Useful after manual edits to the handovers/ directory (deletes, renames),
    or to enforce the 30-entry cap and archive overflow.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    _ensure_handover_status_backfilled()
    data = _load()
    _sync_handover_index(data, bp)
    _save(data)
    n = len(data.get("handovers") or [])
    return f"Handover index resynced — {n} entries in `backlog.yaml`."


# ── Plan C: typed-link MCP tools (spec §6) ─────────────────────────────


@mcp.tool()
def backlog_link_create(source: str, target: str, type: str, note: str = "") -> str:
    """Create a typed link from `source` to `target`. Server writes the inverse
    on the target side automatically (see spec §6A/§6B).

    Validates: link type is canonical; source/target kinds match the type's
    domain; target entity exists; depends_on writes don't create cycles.
    Idempotent — re-running with the same args is a no-op.
    """
    from taskmaster_v3 import (
        LINK_TYPES, is_valid_link, entity_kind_of,
        read_entity_anywhere, write_entity_anywhere, add_link, entity_links,
        sync_inverse, would_create_cycle, load_v3,
    )

    backlog_path = _backlog_path()

    if type not in LINK_TYPES:
        return f"error: invalid link type {type!r} (valid: {sorted(LINK_TYPES)})"

    src_kind = entity_kind_of(source)
    dst_kind = entity_kind_of(target)
    if src_kind is None:
        return f"error: invalid source ID {source!r}"
    if dst_kind is None:
        return f"error: invalid target ID {target!r}"
    if not is_valid_link(type, src_kind, dst_kind):
        return (f"error: invalid link — type {type!r} cannot go from "
                f"{src_kind} ({source}) to {dst_kind} ({target})")

    src_entity = read_entity_anywhere(backlog_path, source)
    if src_entity is None:
        return f"error: source {source!r} not found"
    dst_entity = read_entity_anywhere(backlog_path, target)
    if dst_entity is None:
        return f"error: target {target!r} not found"

    # Cycle check on depends_on / blocks (model both as forward edges in a
    # single task→task graph; `blocks` is reversed onto `depends_on`).
    if type in ("depends_on", "blocks"):
        graph: dict[str, list[str]] = {}
        data = load_v3(backlog_path)
        for epic in data.get("epics", []):
            for task in epic.get("tasks", []):
                tid = task.get("id")
                if not tid:
                    continue
                graph.setdefault(tid, [])
                for link in task.get("links", []) or []:
                    if link.get("type") == "depends_on":
                        graph[tid].append(link["target"])
                    elif link.get("type") == "blocks":
                        # B blocks A == A depends_on B
                        graph.setdefault(link["target"], []).append(tid)
        # Normalize the new edge to a depends_on direction for the check.
        new_src, new_dst = (source, target) if type == "depends_on" else (target, source)
        if would_create_cycle(graph, new_src, new_dst):
            return (f"error: would create cycle in depends_on chain "
                    f"({new_src} -> {new_dst})")

    added = add_link(src_entity, type, target)
    if added:
        write_entity_anywhere(backlog_path, src_entity)
    try:
        sync_inverse(backlog_path, source=source, target=target, type=type)
    except KeyError as e:
        return f"error: {e}"

    suffix = "" if added else " (no-op, link already present)"
    note_part = f" -- {note}" if note else ""
    return f"ok: linked {source} -[{type}]-> {target}{suffix}{note_part}"


@mcp.tool()
def backlog_link_remove(source: str, target: str, type: str = "") -> str:
    """Remove a link (and its inverse) between `source` and `target`.

    If `type` is omitted, removes all link types between the pair.
    """
    from taskmaster_v3 import (
        LINK_TYPES, entity_kind_of, read_entity_anywhere, write_entity_anywhere,
        remove_link, entity_links, sync_inverse,
    )

    backlog_path = _backlog_path()

    if entity_kind_of(source) is None:
        return f"error: invalid source ID {source!r}"
    if entity_kind_of(target) is None:
        return f"error: invalid target ID {target!r}"

    src_entity = read_entity_anywhere(backlog_path, source)
    if src_entity is None:
        return f"error: source {source!r} not found"

    types_to_remove: list[str]
    if type:
        if type not in LINK_TYPES:
            return f"error: invalid link type {type!r}"
        types_to_remove = [type]
    else:
        types_to_remove = sorted({link["type"] for link in entity_links(src_entity)
                                  if link["target"] == target})

    if not types_to_remove:
        return f"ok: no-op (no links from {source} to {target})"

    removed_any = False
    for t in types_to_remove:
        if remove_link(src_entity, t, target):
            removed_any = True
        try:
            sync_inverse(backlog_path, source=source, target=target, type=t, remove=True)
        except KeyError:
            pass
    if removed_any:
        write_entity_anywhere(backlog_path, src_entity)
        return f"ok: removed {len(types_to_remove)} link(s) between {source} and {target}"
    return f"ok: no-op (links not present between {source} and {target})"


@mcp.tool()
def backlog_link_query(source: str = "", target: str = "", type: str = "",
                       depth: int = 1) -> str:
    """Return links matching the source/target/type filter.

    With depth>1, traverses transitively along the same `type`. Returns a JSON
    array of {source, target, type} entries.
    """
    import json as _json
    from taskmaster_v3 import (
        entity_kind_of, read_entity_anywhere, entity_links, load_v3,
    )

    backlog_path = _backlog_path()

    def edges_from(entity_id: str) -> list[dict]:
        entity = read_entity_anywhere(backlog_path, entity_id)
        if entity is None:
            return []
        return [{"source": entity_id, "target": link["target"], "type": link["type"]}
                for link in entity_links(entity)]

    def all_edges() -> list[dict]:
        out: list[dict] = []
        data = load_v3(backlog_path)
        for epic in data.get("epics", []):
            for task in epic.get("tasks", []):
                tid = task.get("id")
                if not tid:
                    continue
                for link in task.get("links", []) or []:
                    out.append({"source": tid, "target": link["target"], "type": link["type"]})
        for sub, prefix in (("handovers", "HND"), ("issues", "ISS"),
                            ("lessons", "L"), ("ideas", "IDEA")):
            sub_dir = backlog_path.parent / sub
            if not sub_dir.exists():
                continue
            for fp in sub_dir.glob(f"{prefix}-*.md"):
                eid = fp.stem
                entity = read_entity_anywhere(backlog_path, eid)
                if entity is None:
                    continue
                for link in entity_links(entity):
                    out.append({"source": eid, "target": link["target"], "type": link["type"]})
        return out

    if source and entity_kind_of(source) is None:
        return f"error: invalid source ID {source!r}"
    if target and entity_kind_of(target) is None:
        return f"error: invalid target ID {target!r}"

    if source and read_entity_anywhere(backlog_path, source) is None:
        return f"error: source {source!r} not found"

    if source:
        results = list(edges_from(source))
        if depth > 1 and type:
            seen = {(e["source"], e["target"]) for e in results}
            frontier = [e["target"] for e in results if e["type"] == type]
            for _ in range(depth - 1):
                next_frontier: list[str] = []
                for node in frontier:
                    for edge in edges_from(node):
                        if edge["type"] != type:
                            continue
                        key = (edge["source"], edge["target"])
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append(edge)
                        next_frontier.append(edge["target"])
                frontier = next_frontier
    else:
        results = all_edges()

    if target:
        results = [e for e in results if e["target"] == target]
    if type:
        results = [e for e in results if e["type"] == type]
    return _json.dumps(results)


@mcp.tool()
def backlog_link_validate() -> str:
    """Report link drift: orphan links, asymmetric pairs, depends_on cycles.

    Returns a JSON object {orphans, asymmetric, cycles, archived_targets}.
    Links to archived entities (status: archived) are flagged in
    `archived_targets` but NOT auto-removed.
    """
    import json as _json
    from taskmaster_v3 import (
        REVERSE_TYPE, read_entity_anywhere, entity_links, load_v3, find_cycle,
    )

    backlog_path = _backlog_path()

    def iter_all_entities():
        data = load_v3(backlog_path)
        for epic in data.get("epics", []):
            for task in epic.get("tasks", []):
                if task.get("id"):
                    yield task["id"], task
        for sub, prefix in (("handovers", "HND"), ("issues", "ISS"),
                            ("lessons", "L"), ("ideas", "IDEA")):
            sub_dir = backlog_path.parent / sub
            if not sub_dir.exists():
                continue
            for fp in sub_dir.glob(f"{prefix}-*.md"):
                eid = fp.stem
                entity = read_entity_anywhere(backlog_path, eid)
                if entity is not None:
                    yield eid, entity

    orphans: list[dict] = []
    asymmetric: list[dict] = []
    archived_targets: list[dict] = []
    depends_graph: dict[str, list[str]] = {}

    entities_by_id: dict[str, dict] = {}
    for eid, ent in iter_all_entities():
        entities_by_id[eid] = ent

    for eid, ent in entities_by_id.items():
        for link in entity_links(ent):
            tgt = link["target"]
            ltype = link["type"]
            if tgt not in entities_by_id:
                orphans.append({"source": eid, "target": tgt, "type": ltype})
                continue
            target_entity = entities_by_id[tgt]
            # Flag links to archived entities as a warning (not auto-removed).
            if target_entity.get("status") == "archived":
                archived_targets.append({"source": eid, "target": tgt, "type": ltype})
            inverse = REVERSE_TYPE.get(ltype)
            if inverse is None:
                continue
            peer_links = entity_links(target_entity)
            if {"type": inverse, "target": eid} not in peer_links:
                asymmetric.append({"source": eid, "target": tgt, "type": ltype,
                                   "missing_inverse": inverse})
            if ltype == "depends_on":
                depends_graph.setdefault(eid, []).append(tgt)
                depends_graph.setdefault(tgt, depends_graph.get(tgt, []))

    cycles: list[list[str]] = []
    # Find up to 5 cycles by iteratively excising one edge of each cycle found.
    graph_copy = {k: list(v) for k, v in depends_graph.items()}
    for _ in range(5):
        cyc = find_cycle(graph_copy)
        if cyc is None:
            break
        cycles.append(cyc)
        # Excise the first edge of the cycle to find further independent ones.
        if len(cyc) >= 2:
            a, b = cyc[0], cyc[1]
            if b in graph_copy.get(a, []):
                graph_copy[a].remove(b)

    return _json.dumps({"orphans": orphans, "asymmetric": asymmetric,
                        "cycles": cycles, "archived_targets": archived_targets})


@mcp.tool()
def backlog_link_reconcile() -> str:
    """Add missing inverse links on peers. Reports unfixable drift.

    Returns JSON {fixed: N, unfixable: [...], cycles: [...]}.
    """
    import json as _json
    from taskmaster_v3 import sync_inverse

    validation = _json.loads(backlog_link_validate())
    fixed = 0
    unfixable: list[dict] = list(validation.get("orphans", []))
    backlog_path = _backlog_path()

    for entry in validation.get("asymmetric", []):
        try:
            sync_inverse(backlog_path,
                         source=entry["source"],
                         target=entry["target"],
                         type=entry["type"])
            fixed += 1
        except (KeyError, ValueError) as e:
            unfixable.append({**entry, "reason": str(e)})

    return _json.dumps({"fixed": fixed, "unfixable": unfixable,
                        "cycles": validation.get("cycles", [])})


@mcp.tool()
def backlog_handover_supersede(old_id: str, new_id: str) -> str:
    """Mark an existing handover as superseded by another.

    Edits the old handover in place: prepends a SUPERSEDED callout, sets
    `superseded_by: <new_id>` in its frontmatter. Use this to repair a
    supersession chain after the fact (e.g., a handover was written without
    `supersedes=`, but should chain off a prior one).

    Both ids must exist on disk. Idempotent on the same `old_id` — calling it
    again with a newer `new_id` updates the pointer instead of stacking
    callouts.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    _ensure_handover_status_backfilled()
    try:
        old_path = _apply_supersession(bp, old_id=old_id, new_id=new_id)
    except FileNotFoundError as exc:
        return f"Error: handover not found: {exc}."
    return f"Superseded {old_id} → {new_id} ({old_path.name} updated)."


@mcp.tool()
def backlog_handover_update_status(
    handover_id: str,
    status: str,
    reason: str = "",
) -> str:
    """Manually set a handover's status (todo / in-progress / done).

    Marks status_user_set: true — subsequent auto-transitions (supersession,
    task-complete, resume) will skip this handover.

    Args:
        handover_id: The handover id (e.g. "2026-05-09-shipped-x").
        status: One of todo, in-progress, done.
        reason: Optional free-text rationale stored as `status_reason`.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    _ensure_handover_status_backfilled()
    try:
        fm, _ = _update_handover_status(bp, handover_id=handover_id, status=status, reason=reason)
    except ValueError as exc:
        return f"Error: {exc}"
    except FileNotFoundError:
        return f"Handover not found: {handover_id}"
    data = _load()
    _sync_handover_index(data, bp)
    _save(data)
    return f"Handover {handover_id} → status={fm['status']} (user-set)."


@mcp.tool()
def backlog_issue_create(
    title: str,
    severity: str,
    evidence: str = "",
    impact: str = "",
    components: list[str] | None = None,
    location: list[str] | None = None,
    related_tasks: list[str] | None = None,
    discovered_by: str = "",
    body: str = "",
    tldr: str = "",
) -> str:
    """Log a systemic or recurring defect as a first-class Issue.

    Issues require evidence of recurrence, systemic scope, or outstanding
    customer impact — one-off defects should go to `backlog_bug_create` instead.
    A *task* is the unit of work; an *issue* is the unit of broken-ness.
    One issue can spawn multiple fix attempts; one task can close many issues.

    Args:
        title: Required. Short summary.
        severity: One of P0 (data loss/security), P1, P2, P3 (cosmetic).
        evidence: Required. Cite recurrence/systemic/outstanding criterion.
        impact: Why this matters (user-visible consequences).
        components: Tags for which parts of the system are affected.
        location: file:line refs to relevant code.
        related_tasks: Task ids attempting or related to this issue.
        discovered_by: Who/what found it (manual QA, alert, customer report).
        body: Markdown body for repro steps + investigation notes.
        tldr: One-line essence of the issue. Auto-generated from impact or
            title if omitted.
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    if severity not in ISSUE_SEVERITIES:
        return f"Error: severity must be one of {ISSUE_SEVERITIES}"
    # tldr: use supplied value, or auto-generate from impact/title
    tldr_autogen = False
    if not tldr:
        tldr = extract_tldr(impact) or title[:TLDR_MAX_CHARS]
        tldr_autogen = True
    try:
        iid, target = _write_issue(
            bp,
            title=title,
            severity=severity,
            evidence=evidence,
            impact=impact,
            components=components or [],
            location=location or [],
            related_tasks=related_tasks or [],
            discovered_by=discovered_by,
            body=body,
            tldr=tldr,
            tldr_autogen=tldr_autogen,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    data = _load()
    _sync_issue_index(data, bp)
    _save(data)
    _ensure_v3_marker(bp)

    # Plan C: auto-detect inline ID mentions, materialize as `references` links.
    from taskmaster_v3 import auto_link_on_save as _auto_link_on_save
    try:
        _auto_link_on_save(bp, iid)
    except Exception:
        pass

    return f"Issue created: {iid} ({severity}) — {title}\nFile: {target.relative_to(ROOT)}"


@mcp.tool()
def backlog_issue_list(
    severity: str = "",
    status: str = "",
    limit: int = 20,
    verbose: bool = False,
) -> str:
    """List issues, optionally filtered by severity and/or status.

    Reads from the backlog.yaml index (sorted P0 → P3). Default lists the
    top 20 active issues regardless of status — pass `status=open` to
    focus on what still needs work.

    Args:
        severity: Filter by severity: P0, P1, P2, P3.
        status: Filter by status: open, investigating, fixed, wontfix, duplicate.
        limit: Maximum number of entries to return (default 20).
        verbose: If True, include body content (repro steps) per entry. Slim
            (default) shows id, severity, status, title, and tldr.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    data = _load()
    entries = data.get("issues") or []
    if severity:
        entries = [e for e in entries if e.get("severity") == severity]
    if status:
        entries = [e for e in entries if e.get("status") == status]
    entries = entries[: max(1, limit)]
    if not entries:
        return "No issues match."
    lines = []
    for e in entries:
        comps = ", ".join(e.get("components") or [])
        comps_tag = f" [{comps}]" if comps else ""
        line = (
            f"- {e['id']} {e.get('severity', '?')} {e.get('status', '?'):14} "
            f"— {e.get('title', '')}{comps_tag}"
        )
        # In slim mode, enrich with tldr from file (index omits tldr)
        if not verbose:
            try:
                fm, _ = _read_issue(bp, e["id"])
                tldr = fm.get("tldr", "")
                if tldr:
                    line += f" — {tldr}"
            except (FileNotFoundError, OSError):
                pass
        lines.append(line)
        if verbose:
            try:
                fm, body = _read_issue(bp, e["id"])
                if fm.get("tldr"):
                    lines.append(f"  tldr: {fm['tldr']}")
                if body and body.strip():
                    lines.append(f"  body: {body.strip()[:200]}")
            except (FileNotFoundError, OSError):
                pass
    return "\n".join(lines)


@mcp.tool()
def backlog_issue_get(
    issue_id: str,
    verbose: bool = False,
    sections: list[str] | None = None,
    expand_links: bool = False,
) -> str:
    """Read an issue's content.

    By default returns a slim view (frontmatter fields only, no body) to
    minimise token cost. Use verbose=True for the full body (repro steps,
    investigation notes). Use sections to pull specific named body sections
    (repro, investigation, notes). Use expand_links=True to expand
    related_tasks to {id, tldr} pills.

    Note: expand_links is a slim-mode feature. When verbose=True, the full
    frontmatter is rendered as-is and expand_links is silently ignored. To
    get expanded links use slim mode (verbose=False) with expand_links=True.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    if verbose and expand_links:
        # expand_links is a slim-mode feature; in verbose mode the full frontmatter
        # is rendered as-is (no ID→pill substitution). To get expanded links, use
        # slim mode (verbose=False) with expand_links=True.
        pass  # silently ignore expand_links in verbose
    try:
        fm, body = _read_issue(bp, issue_id)
    except FileNotFoundError:
        return f"Issue not found: {issue_id}"

    # ── sections-only mode ───────────────────────────────────────────────────
    if sections is not None and not sections:
        return "Error: sections=[] requested no sections; pass sections=None for the slim view or name at least one section"
    if sections:
        try:
            sec_data = _resolve_sections(fm, kind="issue", sections=sections, body=body)
        except ValueError as exc:
            return f"Error: {exc}"
        lines = [f"## Issue: {issue_id}\n"]
        for sec, content in sec_data.items():
            lines.append(f"### {sec}\n{content}")
        return "\n".join(lines)

    # ── verbose mode ─────────────────────────────────────────────────────────
    if verbose:
        fm_lines = [f"  {k}: {v}" for k, v in fm.items()]
        return "---\n" + "\n".join(fm_lines) + "\n---\n" + body

    # ── slim mode (default) ──────────────────────────────────────────────────
    slim = _slim_entity(fm, kind="issue")

    if expand_links:
        data = _load()
        tldr_index = _build_tldr_index(data, project_root=bp.parent.parent if bp.exists() else None)
        related_tasks = slim.get("related_tasks") or []
        if related_tasks:
            slim["related_tasks"] = _expand_link_ids(related_tasks, tldr_index)
        fixed_in = slim.get("fixed_in_task")
        if fixed_in:
            pills = _expand_link_ids([fixed_in], tldr_index)
            slim["fixed_in_task"] = pills[0] if pills else fixed_in

    lines = [f"## Issue: {slim.pop('id', issue_id)}\n"]
    for k, v in slim.items():
        lines.append(f"**{k}:** {v}")
    # Plan C: emit grouped typed-links block.
    _append_grouped_links_block(lines, fm, bp, expand_links=expand_links)
    return "\n".join(lines)


@mcp.tool()
def backlog_issue_update(
    issue_id: str,
    status: str = "",
    title: str = "",
    severity: str = "",
    impact: str = "",
    components: list[str] | None = None,
    location: list[str] | None = None,
    related_tasks: list[str] | None = None,
    fixed_in_task: str = "",
    duplicate_of: str = "",
    body: str = "",
) -> str:
    """Update an issue's metadata or body. Pass empty strings/None to skip a field.

    Lifecycle constraints enforced:
    - status=fixed requires fixed_in_task to be set.
    - status=duplicate requires duplicate_of to be set.
    - resolved date is auto-filled when status moves to fixed.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    updates: dict[str, Any] = {}
    if status:
        if status not in ISSUE_STATUSES:
            return f"Error: status must be one of {ISSUE_STATUSES}"
        updates["status"] = status
    if title:
        updates["title"] = title
    if severity:
        if severity not in ISSUE_SEVERITIES:
            return f"Error: severity must be one of {ISSUE_SEVERITIES}"
        updates["severity"] = severity
    if impact:
        updates["impact"] = impact
    if components is not None:
        updates["components"] = components
    if location is not None:
        updates["location"] = location
    if related_tasks is not None:
        updates["related_tasks"] = related_tasks
    if fixed_in_task:
        updates["fixed_in_task"] = fixed_in_task
    if duplicate_of:
        updates["duplicate_of"] = duplicate_of
    if body:
        updates["body"] = body

    try:
        fm, _ = _update_issue(bp, issue_id, **updates)
    except FileNotFoundError:
        return f"Issue not found: {issue_id}"
    except ValueError as exc:
        return f"Error: {exc}"

    data = _load()
    _sync_issue_index(data, bp)
    _save(data)

    # Plan C: auto-detect inline ID mentions on body updates.
    if body:
        from taskmaster_v3 import auto_link_on_save as _auto_link_on_save
        try:
            _auto_link_on_save(bp, issue_id)
        except Exception:
            pass

    return f"Issue updated: {issue_id} → status={fm['status']}, severity={fm['severity']}"


@mcp.tool()
def backlog_issue_resync() -> str:
    """Rebuild the issue index in backlog.yaml from disk."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    data = _load()
    _sync_issue_index(data, bp)
    _save(data)
    n = len(data.get("issues") or [])
    return f"Issue index resynced — {n} entries."


# ── Bug MCP tools ─────────────────────────────────────────────────────────────


@mcp.tool()
def backlog_bug_create(
    title: str,
    found_in: str = "",
    discovered_by: str = "user",
    severity: str = "",
    components: list[str] | None = None,
    location: list[str] | None = None,
    body: str = "",
) -> str:
    """Log a Bug — the user-flagged sink for defects that don't clear the Issue bar.

    Bugs are project-wide lightweight artifacts (no aging window, no fix-by).
    Use this for one-off defects, cosmetic issues, or anything the user wants
    tracked but that doesn't yet meet the recurring/systemic/outstanding bar.

    Args:
        title: Required. Short summary.
        found_in: Optional task ID where the bug was flagged.
        discovered_by: 'user' (default) or 'claude'. claude is only valid for
            the offer-on-explicit-finding entry point, never for proactive
            AI sightings.
        severity: Optional. P0|P1|P2|P3 if you want a sort hint.
        components: Affected component tags.
        location: file:line refs.
        body: Markdown body for repro/notes.
    """
    from taskmaster_v3 import write_bug as _write_bug, sync_bug_index as _sync_bug_index
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    try:
        bid, target = _write_bug(
            bp,
            title=title,
            found_in=found_in or None,
            discovered_by=discovered_by,
            severity=severity or None,
            components=components or [],
            location=location or [],
            body=body,
        )
    except ValueError as exc:
        return f"Error: {exc}"
    data = _load()
    _sync_bug_index(data, bp)
    _save(data)
    return f"Bug created: {bid} — {title}\nFile: {target.relative_to(ROOT)}"


@mcp.tool()
def backlog_bug_list(
    status: str = "",
    found_in: str = "",
    limit: int = 50,
    include_archive: bool = False,
) -> str:
    """List Bugs from the active set (and optionally archive).

    Defaults to the active set sorted by (status weight asc, discovered desc).

    Args:
        status: Filter by status: open, fixed, shelved, adopted, promoted.
        found_in: Filter by task ID where bug was discovered.
        limit: Max entries to return (default 50).
        include_archive: If True, also include archived bugs.
    """
    from taskmaster_v3 import (
        sync_bug_index as _sync_bug_index,
        list_bug_ids as _list_bug_ids,
        read_bug as _read_bug,
    )
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    data = _load()
    _sync_bug_index(data, bp)
    entries = list(data.get("bugs") or [])
    if include_archive:
        active_ids = {e["id"] for e in entries}
        for bid in _list_bug_ids(bp, include_archive=True):
            if bid in active_ids:
                continue
            try:
                fm, _ = _read_bug(bp, bid)
            except (OSError, ValueError):
                continue
            entries.append({
                "id": fm["id"],
                "title": fm["title"],
                "status": fm["status"],
                "components": fm.get("components"),
                "found_in": fm.get("found_in"),
                "discovered": fm.get("discovered"),
            })
    if status:
        entries = [e for e in entries if e.get("status") == status]
    if found_in:
        entries = [e for e in entries if e.get("found_in") == found_in]
    entries = entries[: max(1, limit)]
    if not entries:
        return "No bugs match."
    lines = []
    for e in entries:
        comps = ", ".join(e.get("components") or [])
        comps_tag = f" [{comps}]" if comps else ""
        line = f"- {e['id']} {e.get('status', '?'):10} — {e.get('title', '')}{comps_tag}"
        if e.get("found_in"):
            line += f"  (found_in: {e['found_in']})"
        lines.append(line)
    return "\n".join(lines)


@mcp.tool()
def backlog_bug_get(bug_id: str, verbose: bool = False) -> str:
    """Read a Bug. Falls through to archive if not in active set.

    Args:
        bug_id: Bug ID (e.g. B-001).
        verbose: If True, return full frontmatter + body. Default is slim view.
    """
    from taskmaster_v3 import read_bug as _read_bug
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    try:
        fm, body = _read_bug(bp, bug_id)
    except FileNotFoundError:
        return f"Bug not found: {bug_id}"
    if verbose:
        fm_lines = [f"  {k}: {v}" for k, v in fm.items()]
        return "---\n" + "\n".join(fm_lines) + "\n---\n" + body
    lines = [f"## Bug: {fm['id']}\n"]
    for k in ("title", "status", "severity", "components", "found_in", "discovered", "discovered_by"):
        if fm.get(k) is not None:
            lines.append(f"**{k}:** {fm[k]}")
    return "\n".join(lines)


@mcp.tool()
def backlog_bug_update(
    bug_id: str,
    status: str = "",
    title: str = "",
    severity: str = "",
    components: list[str] | None = None,
    location: list[str] | None = None,
    fix_commit: str = "",
    adopted_into: str = "",
    promoted_to: str = "",
    body: str = "",
) -> str:
    """Update a Bug's status or fields. Lifecycle constraints enforced.

    - status=fixed requires fix_commit
    - status=adopted requires adopted_into
    - status=promoted requires promoted_to

    Args:
        bug_id: Bug ID (e.g. B-001).
        status: New status. One of open, fixed, shelved, adopted, promoted.
        title: Updated title.
        severity: Updated severity (P0-P3).
        components: Replace components list.
        location: Replace location list.
        fix_commit: Commit SHA that fixed this bug (required for status=fixed).
        adopted_into: Task ID that adopted this bug (required for status=adopted).
        promoted_to: Issue ID this was promoted to (required for status=promoted).
        body: Replace bug body.
    """
    from taskmaster_v3 import update_bug as _update_bug, sync_bug_index as _sync_bug_index, BUG_STATUSES
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    updates: dict[str, Any] = {}
    if status:
        if status not in BUG_STATUSES:
            return f"Error: status must be one of {BUG_STATUSES}"
        updates["status"] = status
    if title:
        updates["title"] = title
    if severity:
        updates["severity"] = severity
    if components is not None:
        updates["components"] = components
    if location is not None:
        updates["location"] = location
    if fix_commit:
        updates["fix_commit"] = fix_commit
    if adopted_into:
        updates["adopted_into"] = adopted_into
    if promoted_to:
        updates["promoted_to"] = promoted_to
    if body:
        updates["body"] = body
    try:
        fm, _ = _update_bug(bp, bug_id, **updates)
    except FileNotFoundError:
        return f"Bug not found: {bug_id}"
    except ValueError as exc:
        return f"Error: {exc}"
    data = _load()
    _sync_bug_index(data, bp)
    _save(data)
    return f"Bug updated: {bug_id} — status={fm['status']}"


@mcp.tool()
def backlog_bug_archive(bug_id: str) -> str:
    """Move bugs/B-NNN.md to bugs/archive/B-NNN.md.

    Refuses if status is open or shelved. Called automatically by task-close
    for fixed bugs; rarely called directly.

    Args:
        bug_id: Bug ID (e.g. B-001).
    """
    from taskmaster_v3 import archive_bug as _archive_bug, sync_bug_index as _sync_bug_index
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    try:
        _archive_bug(bp, bug_id)
    except FileNotFoundError:
        return f"Bug not found: {bug_id}"
    except ValueError as exc:
        return f"Error: {exc}"
    data = _load()
    _sync_bug_index(data, bp)
    _save(data)
    return f"Bug archived: {bug_id}"


@mcp.tool()
def backlog_bug_pattern_scan(mode: str = "all") -> str:
    """Run the cross-bug signature scanner and return groups.

    mode: "all" (default), "open_only", "end_of_task" (excludes archive).
    Returns a human-readable digest of candidate groups; groups have >=2 bugs.

    Args:
        mode: Scan scope. "all" includes archive, "end_of_task" excludes it.
    """
    from taskmaster_v3 import scan_bug_patterns as _scan_bug_patterns
    if mode not in {"all", "open_only", "end_of_task"}:
        return f"Error: invalid mode {mode!r} (expected all|open_only|end_of_task)"
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    include_archive = (mode == "all")
    open_only = (mode == "open_only")
    groups = _scan_bug_patterns(bp, include_archive=include_archive, open_only=open_only)
    if not groups:
        return "No bug patterns found (need >=2 matching signatures)."
    lines = [f"Found {len(groups)} pattern group(s):"]
    for i, g in enumerate(groups, 1):
        comps = ", ".join(g["signature"]["components"]) or "(no components)"
        toks = ", ".join(g["signature"]["tokens"])
        ids = ", ".join(g["bug_ids"])
        lines.append(f"  {i}. [{comps}] tokens: {toks}")
        lines.append(f"     bugs: {ids}")
    return "\n".join(lines)


@mcp.tool()
def backlog_bug_promote(
    bug_ids: list[str],
    title: str,
    severity: str,
    evidence_text: str,
    components: list[str] | None = None,
    body: str = "",
) -> str:
    """Atomic: create an Issue from N Bugs; mark each Bug status=promoted.

    The user must provide evidence_text — this is the systemic/recurring/
    outstanding rationale that the new bar requires. Bugs are marked
    promoted_to=<new ISS-NNN>.

    Args:
        bug_ids: List of Bug IDs to promote (e.g. ["B-001", "B-002"]).
        title: Title for the new Issue.
        severity: Severity for the new Issue (P0-P3).
        evidence_text: Required rationale: cite recurrence/systemic/outstanding.
        components: Component tags for the Issue. Inferred from bugs if omitted.
        body: Markdown body for the new Issue.
    """
    from taskmaster_v3 import (
        promote_bugs_to_issue as _promote,
        sync_bug_index as _sync_bug_index,
        sync_issue_index as _sync_issue_index,
    )
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    try:
        iid = _promote(
            bp,
            bug_ids=list(bug_ids or []),
            title=title,
            severity=severity,
            evidence_text=evidence_text,
            components=components or None,
            body=body,
        )
    except ValueError as exc:
        return f"Error: {exc}"
    data = _load()
    _sync_bug_index(data, bp)
    _sync_issue_index(data, bp)
    _save(data)
    return f"Promoted {len(bug_ids)} bug(s) to {iid}."


@mcp.tool()
def backlog_decision_create(
    title: str,
    options: list[str],
    recommendation: int | None = None,
    task_id: str | None = None,
    related_issues: list[str] | None = None,
    branch: str | None = None,
    raised_in: str | None = None,
    body: str = "",
) -> str:
    """Write a decision menu as a first-class entity (`DEC-NNN`).

    Use when ≥2 mutually exclusive paths need user input. Replaces inline
    option lists in chat — the decision survives the session.

    Args:
        title: Short summary (≤80 chars).
        options: At least 2 mutually exclusive paths.
        recommendation: 1-indexed pick from `options`. None if no preference.
        task_id: Optional link to the task this decision blocks.
        related_issues: Optional ISS-NNN list.
        branch: Optional branch context.
        raised_in: Optional handover id that surfaced this decision.
        body: Free-form context (rationale, constraints, links).
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    try:
        did, target = _write_decision(
            bp,
            title=title,
            options=options,
            recommendation=recommendation,
            task_id=task_id,
            related_issues=related_issues or [],
            branch=branch,
            raised_in=raised_in,
            body=body,
        )
    except ValueError as exc:
        return f"Error: {exc}"
    _ensure_v3_marker(bp)
    return f"Decision created: {did} — {title}\nFile: {target.relative_to(ROOT)}"


@mcp.tool()
def backlog_decision_list(status: str = "open", task_id: str = "", limit: int = 20) -> str:
    """List decisions filtered by status. `status='all'` returns every state."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    ids = _list_decision_ids(bp)
    rows: list[str] = []
    for did in ids:
        try:
            fm, _ = _read_decision(bp, did)
        except (OSError, ValueError):
            continue
        if status != "all" and fm.get("status") != status:
            continue
        if task_id and fm.get("task_id") != task_id:
            continue
        rec = fm.get("recommendation")
        rec_str = f" [rec={rec}]" if rec else ""
        rows.append(f"{did} · {fm.get('status')} · {fm.get('title')}{rec_str}")
        if len(rows) >= limit:
            break
    return "\n".join(rows) if rows else f"No decisions matching status={status}."


@mcp.tool()
def backlog_decision_get(decision_id: str) -> str:
    """Return full decision frontmatter + body as readable text."""
    bp = _backlog_path()
    try:
        fm, body = _read_decision(bp, decision_id)
    except FileNotFoundError:
        return f"Decision not found: {decision_id}"
    lines = [f"{k}: {v}" for k, v in fm.items()]
    return "\n".join(lines) + "\n\n---\n" + body


@mcp.tool()
def backlog_decision_resolve(
    decision_id: str,
    resolved_with: int,
    rationale: str = "",
    resolved_in: str = "",
) -> str:
    """Resolve a decision with a chosen option (1-indexed)."""
    bp = _backlog_path()
    try:
        fm = _resolve_decision(
            bp, decision_id,
            resolved_with=int(resolved_with),
            rationale=rationale,
            resolved_in=resolved_in or None,
        )
    except (ValueError, FileNotFoundError) as exc:
        return f"Error: {exc}"
    return (
        f"Decision {decision_id} resolved with option {fm['resolved_with']}: "
        f"\"{fm['options'][fm['resolved_with'] - 1]}\""
    )


@mcp.tool()
def backlog_decision_drop(decision_id: str, reason: str) -> str:
    """Drop a decision with a reason (no option picked)."""
    bp = _backlog_path()
    try:
        _drop_decision(bp, decision_id, reason=reason)
    except (ValueError, FileNotFoundError) as exc:
        return f"Error: {exc}"
    return f"Decision {decision_id} dropped: {reason}"


@mcp.tool()
def backlog_decision_update(
    decision_id: str,
    title: str = "",
    options: list[str] | None = None,
    recommendation: int | None = None,
    body: str = "",
) -> str:
    """Edit a decision in place (pre-resolution fields only)."""
    bp = _backlog_path()
    patch: dict = {}
    if title:
        patch["title"] = title
    if options:
        patch["options"] = options
    if recommendation is not None:
        patch["recommendation"] = recommendation
    try:
        fm = _update_decision(bp, decision_id, patch)
    except (ValueError, FileNotFoundError) as exc:
        return f"Error: {exc}"
    if body:
        cur_fm, _ = _read_decision(bp, decision_id)
        _write_task_file(_decision_path(bp, decision_id), cur_fm, body)
    return f"Decision {decision_id} updated."


@mcp.tool()
def backlog_continuity_items(
    view: str = "action",
    include_auto_stage: bool = False,
) -> str:
    """Return all continuity items as JSON: {"items": [...], "view": "..."}.

    `view` is informational only — the server returns the full set; the client
    decides grouping (Action / Time / Entity).

    Args:
        view: "action" | "time" | "entity" (echoed in the response).
        include_auto_stage: When True, include auto-stage handovers (debug).
    """
    import json
    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({"items": [], "view": view, "error": "no backlog"})
    items = _continuity_items(bp, include_auto_stage=include_auto_stage)
    return json.dumps({"items": items, "view": view}, default=str)


@mcp.tool()
def backlog_idea_create(
    title: str,
    body: str = "",
    tags: list[str] | None = None,
    status: str = "",
    related_tasks: list[str] | None = None,
    related_issues: list[str] | None = None,
    related_lessons: list[str] | None = None,
    created_by: str = "Claude",
    tldr: str = "",
) -> str:
    """Log an idea — a lightweight, unvalidated thought. Lighter than a task.

    An *idea* is a parking lot for things you might want to do, explore, or
    revisit. It can grow into a task / lesson / issue later via linkage.
    Title is required; everything else is optional. Status is freeform.

    Args:
        title: Required. Short summary.
        body: Markdown body — anything goes (sketches, code blocks, prose).
        tags: Freeform tag strings.
        status: Freeform status ("exploring", "parking-lot", "candidate", "").
        related_tasks: Task ids this idea relates to.
        related_issues: Issue ids this idea relates to.
        related_lessons: Lesson ids this idea relates to.
        created_by: Who logged it ("Claude" by default; "user" when
            invoked via /add-idea).
        tldr: One-line essence of the idea. Auto-generated from body or
            title if omitted.
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    # tldr: use supplied value, or auto-generate from body/title
    tldr_autogen = False
    if not tldr:
        tldr = extract_tldr(body) or title[:TLDR_MAX_CHARS]
        tldr_autogen = True
    try:
        iid, target = _write_idea(
            bp,
            title=title,
            body=body,
            tags=tags or [],
            status=status,
            related_tasks=related_tasks or [],
            related_issues=related_issues or [],
            related_lessons=related_lessons or [],
            created_by=created_by,
            tldr=tldr,
            tldr_autogen=tldr_autogen,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    # Plan C: auto-detect inline ID mentions, materialize as `references` links.
    from taskmaster_v3 import auto_link_on_save as _auto_link_on_save
    try:
        _auto_link_on_save(bp, iid)
    except Exception:
        pass

    try:
        rel = target.relative_to(ROOT)
    except ValueError:
        rel = target
    return f"Idea created: {iid} — {title}\nFile: {rel}"


@mcp.tool()
def backlog_idea_list(
    idea_id: str = "",
    status: str = "",
    tag: str = "",
    archived: bool = False,
    related_task: str = "",
    related_issue: str = "",
    related_lesson: str = "",
    limit: int = 50,
    summary: bool = True,
) -> str:
    """List ideas, optionally filtered. With `idea_id`, returns one full record.

    Without `idea_id`, returns summary lines (no body) — newest first. Pass
    `summary=False` to render full record per entry (heavier payload, useful
    when scripting). Filters compose as AND. By default archived ideas are
    excluded.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    if idea_id:
        out = _list_ideas(bp, idea_id=idea_id)
        if not out:
            return f"Idea not found: {idea_id}"
        rec = out[0]
        body = rec.pop("body", "")
        fm_lines = [f"  {k}: {v}" for k, v in rec.items()]
        return "---\n" + "\n".join(fm_lines) + "\n---\n" + body

    entries = _list_ideas(
        bp,
        status=status or None,
        tag=tag or None,
        archived=archived,
        related_task=related_task or None,
        related_issue=related_issue or None,
        related_lesson=related_lesson or None,
        limit=max(1, limit),
        summary=summary,
    )
    if not entries:
        return "No ideas match."
    if not summary:
        # Full-record mode: render each as a frontmatter+body block.
        blocks = []
        for e in entries:
            body = e.pop("body", "")
            fm_lines = [f"  {k}: {v}" for k, v in e.items()]
            blocks.append("---\n" + "\n".join(fm_lines) + "\n---\n" + body)
        return "\n\n".join(blocks)
    lines = []
    for e in entries:
        st = e.get("status") or ""
        st_tag = f" [{st}]" if st else ""
        lines.append(f"- {e['id']} — {e.get('title', '')}{st_tag}")
    return "\n".join(lines)


@mcp.tool()
def backlog_idea_get(
    idea_id: str,
    verbose: bool = False,
    sections: list[str] | None = None,
    expand_links: bool = False,
) -> str:
    """Read an idea's content.

    By default returns a slim view (frontmatter fields only, no body) to
    minimise token cost. Use verbose=True for the full body. Ideas do not
    have canonical body sections, so sections= is not supported (returns
    an error if provided). Use expand_links=True to expand related_tasks,
    related_issues, and related_lessons to {id, tldr} pills.

    Note: expand_links is a slim-mode feature. When verbose=True, the full
    frontmatter is rendered as-is and expand_links is silently ignored. To
    get expanded links use slim mode (verbose=False) with expand_links=True.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    if verbose and expand_links:
        # expand_links is a slim-mode feature; in verbose mode the full frontmatter
        # is rendered as-is (no ID→pill substitution). To get expanded links, use
        # slim mode (verbose=False) with expand_links=True.
        pass  # silently ignore expand_links in verbose
    if sections is not None and not sections:
        return "Error: sections=[] requested no sections; pass sections=None for the slim view or name at least one section"
    if sections:
        return "Error: ideas have no canonical body sections — use verbose=True to read the full body."
    try:
        fm, body = _read_idea(bp, idea_id)
    except FileNotFoundError:
        return f"Idea not found: {idea_id}"

    # ── verbose mode ─────────────────────────────────────────────────────────
    if verbose:
        fm_lines = [f"  {k}: {v}" for k, v in fm.items()]
        return "---\n" + "\n".join(fm_lines) + "\n---\n" + body

    # ── slim mode (default) ──────────────────────────────────────────────────
    slim = _slim_entity(fm, kind="idea")

    if expand_links:
        data = _load()
        tldr_index = _build_tldr_index(data, project_root=bp.parent.parent if bp.exists() else None)
        for link_field in ("related_tasks", "related_issues", "related_lessons"):
            ids = slim.get(link_field) or []
            if ids:
                slim[link_field] = _expand_link_ids(ids, tldr_index)

    lines = [f"## Idea: {slim.pop('id', idea_id)} — {slim.pop('title', fm.get('title', ''))}\n"]
    for k, v in slim.items():
        lines.append(f"**{k}:** {v}")
    # Plan C: emit grouped typed-links block.
    _append_grouped_links_block(lines, fm, bp, expand_links=expand_links)
    return "\n".join(lines)


@mcp.tool()
def backlog_idea_update(
    idea_id: str,
    title: str = "",
    body: str = "",
    status: str = "",
    tags: list[str] | None = None,
    related_tasks: list[str] | None = None,
    related_issues: list[str] | None = None,
    related_lessons: list[str] | None = None,
    promoted_to: str = "",
    archived: bool | None = None,
) -> str:
    """Patch an idea's frontmatter and/or body.

    Pass empty strings / None to skip a field. To promote an idea into a
    task, pass `promoted_to="T-XYZ"` and optionally `archived=True`. To
    archive without promotion, pass `archived=True`.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    updates: dict[str, Any] = {}
    if title:
        updates["title"] = title
    if body:
        updates["body"] = body
    if status:
        updates["status"] = status
    if tags is not None:
        updates["tags"] = tags
    if related_tasks is not None:
        updates["related_tasks"] = related_tasks
    if related_issues is not None:
        updates["related_issues"] = related_issues
    if related_lessons is not None:
        updates["related_lessons"] = related_lessons
    if promoted_to:
        updates["promoted_to"] = promoted_to
    if archived is not None:
        updates["archived"] = archived

    try:
        fm, _ = _update_idea(bp, idea_id, **updates)
    except FileNotFoundError:
        return f"Idea not found: {idea_id}"
    except ValueError as exc:
        return f"Error: {exc}"

    # Plan C: auto-detect inline ID mentions on body updates.
    if body:
        from taskmaster_v3 import auto_link_on_save as _auto_link_on_save
        try:
            _auto_link_on_save(bp, idea_id)
        except Exception:
            pass

    return f"Idea updated: {idea_id} — {fm.get('title', '')}"


@mcp.tool()
def backlog_note_create(text: str, pinned: bool = False) -> str:
    """Write a sticky note onto the user's Desk (dashboard).

    Notes are the lightest continuity surface: freeform, situational,
    NOT attached to tasks. Claude-created notes are stamped author
    "claude" and render visually distinct from the user's own notes.
    Write at most one consolidated note per session, and only for loose
    thoughts that fit no other entity (task/idea/issue/handover).
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    from taskmaster_v3 import write_note as _write_note
    try:
        nid, target = _write_note(bp, text=text, author="claude", pinned=pinned)
    except ValueError as exc:
        return f"Error: {exc}"
    try:
        rel = target.relative_to(ROOT)
    except ValueError:
        rel = target
    return f"Note created: {nid}\nFile: {rel}"


@mcp.tool()
def backlog_note_list(include_archived: bool = False) -> str:
    """List sticky notes from the user's Desk — pinned first, newest first.

    Returns one line per note: id, author, pin marker, created date, first
    line of text. Use during session start to surface the user's desk."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    from taskmaster_v3 import list_notes as _list_notes
    notes = _list_notes(bp, include_archived=include_archived)
    if not notes:
        return "Desk is clear — no notes."
    lines = []
    for n in notes:
        first = (n.get("body") or "").strip().splitlines()[0] if n.get("body") else ""
        pin = "📌 " if n.get("pinned") else ""
        arch = " [archived]" if n.get("archived") else ""
        created = str(n.get("created", ""))[:10]
        lines.append(f"- {n['id']} ({n.get('author')}, {created}){arch} — {pin}{first}")
    return "\n".join(lines)


@mcp.tool()
def backlog_note_get(note_id: str) -> str:
    """Read one sticky note in full (frontmatter + complete text)."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    from taskmaster_v3 import read_note as _read_note
    try:
        fm, body = _read_note(bp, note_id)
    except FileNotFoundError:
        return f"Note not found: {note_id}"
    fm_lines = [f"  {k}: {v}" for k, v in fm.items()]
    return "---\n" + "\n".join(fm_lines) + "\n---\n" + body


@mcp.tool()
def backlog_note_update(note_id: str, text: str = "", pinned: bool | None = None) -> str:
    """Edit a sticky note's text and/or pin state. Author is immutable —
    a user-authored note stays user-authored even if Claude edits it
    (avoid editing user notes unless explicitly asked)."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    from taskmaster_v3 import update_note as _update_note
    try:
        _update_note(bp, note_id, text=text or None, pinned=pinned)
    except FileNotFoundError:
        return f"Note not found: {note_id}"
    except ValueError as exc:
        return f"Error: {exc}"
    return f"Note updated: {note_id}"


@mcp.tool()
def backlog_note_archive(note_id: str) -> str:
    """Archive a sticky note (moves it off the Desk into notes/_archive/).
    Never archive user-authored notes unless the user explicitly asks."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    from taskmaster_v3 import archive_note as _archive_note
    try:
        _archive_note(bp, note_id)
    except FileNotFoundError:
        return f"Note not found: {note_id}"
    return f"Note archived: {note_id}"


@mcp.tool()
def viewer_prefs_get() -> str:
    """Return current viewer prefs as JSON."""
    import json
    prefs = load_viewer_prefs()
    return json.dumps(prefs, indent=2)


@mcp.tool()
def viewer_prefs_set(patch_json: str) -> str:
    """Deep-merge a JSON patch into the persisted viewer prefs.
    Patch is a JSON object; only the keys present are updated.
    """
    import json
    try:
        patch = json.loads(patch_json)
    except Exception as e:
        return f"error: invalid JSON ({e})"
    if not isinstance(patch, dict):
        return "error: patch must be a JSON object"

    prefs = load_viewer_prefs()
    _deep_merge(prefs, patch)
    save_viewer_prefs(prefs)
    return "ok"


@mcp.tool()
def backlog_lesson_create(
    title: str,
    kind: str,
    body: str = "",
    files: list[str] | None = None,
    task_titles_match: list[str] | None = None,
    task_kinds: list[str] | None = None,
    related_tasks: list[str] | None = None,
    related_issues: list[str] | None = None,
    tier: str = "active",
    tldr: str = "",
) -> str:
    """Create a project-scoped lesson — a reusable pattern, anti-pattern, or gotcha.

    Lessons compound: each session reinforces lessons that get applied,
    raising their priority. Auto-promote to 'core' tier (always loaded full)
    when a gotcha/anti-pattern reaches reinforce_count >= 5.

    Triggers determine when this lesson loads on `pick-task`:
    - files: glob patterns matched against files the task will touch.
    - task_titles_match: substrings checked against the task title.
    - task_kinds: list of task kinds (feature/bug/spike/etc).

    Args:
        title: Required. One-line summary of the lesson.
        kind: pattern | anti-pattern | gotcha.
        body: Markdown body. Suggested sections: ## Why, ## What to do, ## Examples.
        files: Glob patterns for trigger matching.
        task_titles_match: Substrings for trigger matching.
        task_kinds: Task kinds for trigger matching.
        related_tasks / related_issues: Cross-refs.
        tier: active (default) | core | retired.
        tldr: One-line essence of the lesson. Auto-generated from body or
            title if omitted.
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    if kind not in LESSON_KINDS:
        return f"Error: kind must be one of {LESSON_KINDS}"
    if tier not in LESSON_TIERS:
        return f"Error: tier must be one of {LESSON_TIERS}"
    # tldr: use supplied value, or auto-generate from body/title
    tldr_autogen = False
    if not tldr:
        tldr = extract_tldr(body) or title[:TLDR_MAX_CHARS]
        tldr_autogen = True
    triggers = {
        "files": files or [],
        "task_titles_match": task_titles_match or [],
        "task_kinds": task_kinds or [],
    }
    try:
        lid, target = _write_lesson(
            bp,
            title=title,
            kind=kind,
            triggers=triggers,
            body=body,
            tier=tier,
            related_tasks=related_tasks or [],
            related_issues=related_issues or [],
            tldr=tldr,
            tldr_autogen=tldr_autogen,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    # Seed an initial event so the lesson lands on the "active" shelf
    # immediately. Without this, compute_lesson_shelf sees zero events within
    # retired_after_days and classifies a brand-new lesson as "retired".
    # We add the event directly rather than going through lesson_reinforce —
    # creation is not a reinforcement, so reinforce_count must stay at 0
    # until the user actually applies the lesson.
    try:
        from datetime import datetime as _dt, timezone as _tz
        from taskmaster_v3 import load_lesson as _load_lesson, save_lesson as _save_lesson
        _l = _load_lesson(lid)
        _seed_at = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _l.setdefault("reinforce_events", []).append(
            {"at": _seed_at, "source": "user", "note": "created"}
        )
        _save_lesson(_l)
    except Exception:
        pass

    data = _load()
    _sync_lesson_index(data, bp)
    _save(data)
    _ensure_v3_marker(bp)

    # Plan C: auto-detect inline ID mentions, materialize as `references` links.
    from taskmaster_v3 import auto_link_on_save as _auto_link_on_save
    try:
        _auto_link_on_save(bp, lid)
    except Exception:
        pass

    return f"Lesson created: {lid} ({kind}, {tier}) — {title}\nFile: {target.relative_to(ROOT)}"


@mcp.tool()
def backlog_lesson_list(
    tier: str = "",
    kind: str = "",
    verbose: bool = False,
) -> str:
    """List lessons, optionally filtered by tier and/or kind.

    Args:
        tier: Filter by tier: active, core, retired.
        kind: Filter by kind: pattern, anti-pattern, gotcha.
        verbose: If True, include reinforce_count and tier/kind metadata per
            entry. Slim (default) shows id, title, and tldr only.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    lines = []
    for lid in _list_lesson_ids(bp):
        try:
            fm, _ = _read_lesson(bp, lid)
        except Exception:
            continue
        if tier and fm.get("tier") != tier:
            continue
        if kind and fm.get("kind") != kind:
            continue
        if verbose:
            rc = fm.get("reinforce_count", 0)
            lines.append(
                f"- {fm['id']} [{fm.get('tier','active')}/{fm.get('kind','?')}] x{rc} — {fm.get('title','')}"
            )
            if fm.get("tldr"):
                lines.append(f"  tldr: {fm['tldr']}")
        else:
            # Slim: id, title, and tldr pill (omit heavy fields)
            tldr = fm.get("tldr", "")
            entry = f"- {fm['id']} — {fm.get('title', '')}"
            if tldr:
                entry += f" — {tldr}"
            lines.append(entry)
    return "\n".join(lines) if lines else "No lessons match."


@mcp.tool()
def backlog_lesson_get(
    lesson_id: str,
    verbose: bool = False,
    sections: list[str] | None = None,
    expand_links: bool = False,
) -> str:
    """Read a lesson's content.

    By default returns a slim view (frontmatter fields only, no body) to
    minimise token cost. Use verbose=True for the full body. Use sections
    to pull specific named body sections (why, what_to_do, examples).
    Use expand_links=True to expand related_tasks and related_issues to
    {id, tldr} pills.

    Note: expand_links is a slim-mode feature. When verbose=True, the full
    frontmatter is rendered as-is and expand_links is silently ignored. To
    get expanded links use slim mode (verbose=False) with expand_links=True.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    if verbose and expand_links:
        # expand_links is a slim-mode feature; in verbose mode the full frontmatter
        # is rendered as-is (no ID→pill substitution). To get expanded links, use
        # slim mode (verbose=False) with expand_links=True.
        pass  # silently ignore expand_links in verbose
    try:
        fm, body = _read_lesson(bp, lesson_id)
    except FileNotFoundError:
        return f"Lesson not found: {lesson_id}"

    # ── sections-only mode ───────────────────────────────────────────────────
    if sections is not None and not sections:
        return "Error: sections=[] requested no sections; pass sections=None for the slim view or name at least one section"
    if sections:
        try:
            sec_data = _resolve_sections(fm, kind="lesson", sections=sections, body=body)
        except ValueError as exc:
            return f"Error: {exc}"
        lines = [f"## Lesson: {lesson_id}\n"]
        for sec, content in sec_data.items():
            lines.append(f"### {sec}\n{content}")
        return "\n".join(lines)

    # ── verbose mode ─────────────────────────────────────────────────────────
    if verbose:
        fm_lines = [f"  {k}: {v}" for k, v in fm.items()]
        return "---\n" + "\n".join(fm_lines) + "\n---\n" + body

    # ── slim mode (default) ──────────────────────────────────────────────────
    slim = _slim_entity(fm, kind="lesson")

    if expand_links:
        data = _load()
        tldr_index = _build_tldr_index(data, project_root=bp.parent.parent if bp.exists() else None)
        for link_field in ("related_tasks", "related_issues"):
            ids = slim.get(link_field) or []
            if ids:
                slim[link_field] = _expand_link_ids(ids, tldr_index)

    lines = [f"## Lesson: {slim.pop('id', lesson_id)}\n"]
    for k, v in slim.items():
        lines.append(f"**{k}:** {v}")
    # Plan C: emit grouped typed-links block.
    _append_grouped_links_block(lines, fm, bp, expand_links=expand_links)
    return "\n".join(lines)


@mcp.tool()
def backlog_lesson_update(
    lesson_id: str,
    title: str = "",
    kind: str = "",
    tier: str = "",
    files: list[str] | None = None,
    task_titles_match: list[str] | None = None,
    task_kinds: list[str] | None = None,
    body: str = "",
) -> str:
    """Update a lesson's metadata or body. Pass empty values to skip a field."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    updates: dict[str, Any] = {}
    if title:
        updates["title"] = title
    if kind:
        if kind not in LESSON_KINDS:
            return f"Error: kind must be one of {LESSON_KINDS}"
        updates["kind"] = kind
    if tier:
        if tier not in LESSON_TIERS:
            return f"Error: tier must be one of {LESSON_TIERS}"
        updates["tier"] = tier
    if files is not None or task_titles_match is not None or task_kinds is not None:
        # Need a fresh read to merge trigger fields
        fm, _ = _read_lesson(bp, lesson_id)
        triggers = dict(fm.get("triggers") or {})
        if files is not None:
            triggers["files"] = files
        if task_titles_match is not None:
            triggers["task_titles_match"] = task_titles_match
        if task_kinds is not None:
            triggers["task_kinds"] = task_kinds
        updates["triggers"] = triggers
    if body:
        updates["body"] = body
    try:
        _update_lesson(bp, lesson_id, **updates)
    except FileNotFoundError:
        return f"Lesson not found: {lesson_id}"
    except ValueError as exc:
        return f"Error: {exc}"
    data = _load()
    _sync_lesson_index(data, bp)
    _save(data)

    # Plan C: auto-detect inline ID mentions on body updates.
    if body:
        from taskmaster_v3 import auto_link_on_save as _auto_link_on_save
        try:
            _auto_link_on_save(bp, lesson_id)
        except Exception:
            pass

    return f"Lesson updated: {lesson_id}"


@mcp.tool()
def backlog_lesson_reinforce(lesson_id: str) -> str:
    """Mark a lesson as applied this session (reinforce_count++, last_reinforced=today).

    Suggests promotion to core tier when a gotcha/anti-pattern reaches the
    reinforcement threshold.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    try:
        fm = _reinforce_lesson(bp, lesson_id)
    except FileNotFoundError:
        return f"Lesson not found: {lesson_id}"
    msg = f"Reinforced {lesson_id} → x{fm['reinforce_count']}"
    if _lesson_eligible_for_promotion(fm):
        msg += "\n→ Eligible for promotion to core tier (auto-load at session start). Use `backlog_lesson_update {} tier=core` to promote.".format(lesson_id)
    return msg


@mcp.tool()
def lesson_reinforce(lesson_id: str, source: str = "user", note: str = "") -> str:
    """Record a reinforcement event for a lesson.

    Args:
        lesson_id: e.g. "L-014"
        source: one of "user" | "claude" | "skill"
        note: optional free-text annotation

    Returns the updated lesson summary as a JSON string.
    """
    import json as _json
    from taskmaster_v3 import lesson_reinforce as _impl

    try:
        summary = _impl(lesson_id, source=source, note=note)
    except FileNotFoundError:
        return _json.dumps({"ok": False, "error": f"lesson {lesson_id} not found"})
    except ValueError as e:
        return _json.dumps({"ok": False, "error": str(e)})
    return _json.dumps(summary, indent=2, default=str)


@mcp.tool()
def backlog_lesson_digest() -> str:
    """Return the slim digest of active-tier lessons for session-start injection.

    Format: one line per lesson with id, kind, title. Capped at 30 entries.
    Excludes core (loaded separately, full body) and retired tiers.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    digest = _lesson_digest(bp)
    if not digest:
        return "No active lessons."
    return "\n".join(f"- {d['id']} [{d['kind']}] {d['title']}" for d in digest)


def _transcripts_dir() -> Path:
    """Resolve the Claude project transcripts directory.

    Override with `TASKMASTER_TRANSCRIPTS_DIR` for tests. Default is
    `~/.claude/projects/<encoded-cwd>/` per Claude Code's storage layout.
    """
    override = os.environ.get("TASKMASTER_TRANSCRIPTS_DIR")
    if override:
        return Path(override)
    home = Path.home() / ".claude" / "projects"
    encoded = str(ROOT.resolve()).replace("\\", "-").replace("/", "-").replace(":", "")
    return home / encoded


@mcp.tool()
def backlog_lesson_candidate_defer(
    title: str,
    kind: str = "",
    topic: str = "",
    scope: str = "point",
    context: str = "",
) -> str:
    """Defer a lesson candidate to `.taskmaster/lessons/_candidates.md`.

    Use mid-session when Claude wants to flag a candidate but the user isn't
    ready to write a full lesson. End-session sweep reads this file.

    Args:
        title: One-line summary. Required.
        kind: pattern | anti-pattern | gotcha. Optional — leave empty to
            classify later during the sweep.
        topic: One-word handle for grouping in the sweep UI.
        scope: 'point' (default) or 'session' (flags the active handover for
            retro-extraction; see references/session-retro.md).
        context: Free text — session id, commit sha, anything traceable.
    """
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}. Run `backlog_init` first."
    try:
        idx = _lesson_candidates_defer(
            bp,
            title=title,
            kind=kind,
            topic=topic,
            scope=scope,
            context=context,
        )
    except ValueError as exc:
        return f"Error: {exc}"
    return f"Deferred candidate #{idx}: {title.strip()}"


@mcp.tool()
def backlog_lesson_candidates_list() -> str:
    """List deferred lesson candidates (markdown bullet list, indexed)."""
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}."
    items = _lesson_candidates_read(bp)
    if not items:
        return "No deferred candidates."
    lines = []
    for idx, it in enumerate(items):
        kind = it.get("kind") or "?"
        topic = it.get("topic") or ""
        scope = it.get("scope") or "point"
        head = f"- [#{idx}] [{kind}/{scope}]"
        if topic:
            head += f" ({topic})"
        head += f" — {it.get('title', '')}"
        lines.append(head)
    return "\n".join(lines)


@mcp.tool()
def backlog_lesson_candidate_drop(index: int) -> str:
    """Drop the deferred candidate at `index` (0-based)."""
    bp = _backlog_path()
    if not bp.exists():
        return f"Error: no backlog found at {bp}."
    items = _lesson_candidates_read(bp)
    if index < 0 or index >= len(items):
        return f"Error: candidate #{index} not found (have {len(items)} entries)."
    title = items[index].get("title", "")
    n = _lesson_candidates_clear(bp, indices=[index])
    if not n:
        return f"Error: candidate #{index} not found."
    return f"Dropped candidate #{index}: {title}"


@mcp.tool()
def backlog_lesson_candidates_scan(days: int = 7, kind: str = "") -> str:
    """Grep this project's transcript jsonl for `<lesson-candidate>` tags.

    Recovery path for tags lost to compaction (until the PreCompact hook in
    v3-skills-006 lands, this is the only such path). Reads
    `~/.claude/projects/<this-project>/*.jsonl` within the last `days` days.

    Args:
        days: Window in days (default 7).
        kind: Filter to a single kind (gotcha / pattern / anti-pattern).

    Returns markdown grouped by source jsonl filename.
    """
    transcripts = _transcripts_dir()
    if not transcripts.exists():
        return f"No transcripts directory at {transcripts}."
    matches = _scan_transcripts_for_candidates(
        transcripts, days=days, kind_filter=kind
    )
    if not matches:
        return f"No `<lesson-candidate>` tags found in last {days} days."
    by_file: dict[str, list[dict[str, Any]]] = {}
    for m in matches:
        by_file.setdefault(Path(m["source_file"]).name, []).append(m)
    lines: list[str] = []
    for fname, items in by_file.items():
        lines.append(f"## {fname}")
        for it in items:
            tag = f"[{it.get('kind') or '?'}]"
            topic = f" ({it['topic']})" if it.get("topic") else ""
            preview = (it.get("body") or "").splitlines()[0][:100]
            lines.append(
                f"- L{it['source_line']} {tag}{topic} — {preview}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


@mcp.tool()
def backlog_auto_start(
    mode: str,
    target: str,
    continue_on_fail: bool = False,
    no_gate: bool = False,
) -> str:
    """Start an auto run (skill-driven state machine).

    The orchestrating skill (taskmaster:auto-task / auto-epic / auto-phase)
    drives Claude through PICK → SPEC_REVIEW → IMPLEMENT → REVIEW_GATE →
    HANDOVER_STUB → END_SESSION for each task. This tool seeds the run.

    Args:
        mode: 'task', 'epic', or 'phase'.
        target: Task id (mode=task), epic id (mode=epic), or phase id (mode=phase).
        continue_on_fail: If true, batch runs proceed past failed tasks.
        no_gate: If true, skip user-approval gates (SPEC_REVIEW, REVIEW_GATE).

    Per-task model selection:
        Each task may declare `auto_model: sonnet|opus`. Missing → sonnet.
        The orchestrator skill reads `state.cursor.model` to pick the
        subagent model when dispatching.
    """
    if mode not in AUTO_MODES:
        return f"Error: mode must be one of {AUTO_MODES}"
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."

    data = _load()
    pending: list[str] = []
    model_for_task: dict[str, str] = {}
    lane_for_task: dict[str, str] = {}

    if mode == "task":
        # target is a task id
        if not _find_task(data, target):
            return f"Error: task {target} not found"
        task, _ = _find_task(data, target)
        pending = [target]
        model_for_task[target] = task.get("auto_model", "sonnet")
        lane_for_task[target] = task.get("lane")
    elif mode == "epic":
        epic = next((e for e in data.get("epics") or [] if e.get("id") == target), None)
        if not epic:
            return f"Error: epic {target} not found"
        for t in epic.get("tasks") or []:
            if t.get("status") in (None, "todo"):
                pending.append(t["id"])
                model_for_task[t["id"]] = t.get("auto_model", "sonnet")
                lane_for_task[t["id"]] = t.get("lane")
    else:  # phase
        for epic in data.get("epics") or []:
            for t in epic.get("tasks") or []:
                if t.get("phase") == target and t.get("status") in (None, "todo"):
                    pending.append(t["id"])
                    model_for_task[t["id"]] = t.get("auto_model", "sonnet")
                    lane_for_task[t["id"]] = t.get("lane")

    if not pending:
        return f"No todo tasks under {mode} {target} — nothing to do."

    state = _init_auto_run(
        bp,
        mode=mode,
        target=target,
        pending_task_ids=pending,
        model_for_task=model_for_task,
        lane_for_task=lane_for_task,
        config={"continue_on_fail": continue_on_fail, "no_gate": no_gate},
    )

    cur = state["cursor"]
    planned = cur.get("planned_stages") or []
    return (
        f"Auto run started: mode={mode}, target={target}, tasks={len(pending)}.\n"
        f"First task: {cur['task_id']} (model={cur['model']}, lane={cur.get('lane') or 'standard (default)'}).\n"
        f"Cursor stage: PICK.\n"
        f"Lane pipeline: {' → '.join(planned)}\n"
        f"Walk it with backlog_auto_advance() (no stage arg) until COMPLETE. "
        f"The auto-{mode} skill should drive from here."
    )


@mcp.tool()
def backlog_auto_status() -> str:
    """Show current auto-run status (which task, which stage, completed/pending counts)."""
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    state = _read_auto_state(bp)
    if not state:
        return "No auto run in progress."
    return _auto_run_summary(state)


@mcp.tool()
def backlog_auto_advance(stage: str = "") -> str:
    """Move the cursor to the next (or a specific) lane stage. Used by
    orchestrating skills between steps.

    Spec A C2 — lane-aware walking:
      - Call with NO stage (the default) to advance to the NEXT stage in the
        cursor task's lane-specific planned sequence. This is the preferred
        path: it records exactly the gates that lane requires, in order, so a
        standard-lane task records design-review (not spec-review) and a
        full-lane task records spec-review + plan-review. When the cursor is
        already at the last planned stage, returns a "pipeline complete" note.
      - Call with an explicit stage to jump there (back-compat for existing
        callers/tests). Valid stages: PICK, SPEC, SPEC_REVIEW, PLAN,
        PLAN_REVIEW, DESIGN_REVIEW, WRITE_TESTS, IMPLEMENT, TEST, REVIEW_GATE,
        HANDOVER_STUB, END_SESSION, COMPLETE.

    Advancing to a gate-mapped stage auto-records that stage's gate.
    """
    bp = _backlog_path()
    state = _read_auto_state(bp)
    if not state:
        return "No auto run in progress."
    cursor = state.get("cursor")
    if not cursor:
        return "No active cursor — auto run is complete."

    if not stage:
        # No-arg: walk the lane-specific planned sequence.
        stage = _next_planned_stage(cursor)
        if not stage:
            return (
                f"Pipeline complete for {cursor['task_id']} "
                f"(stage={cursor.get('stage')}). "
                f"Finalize with backlog_auto_complete_task."
            )

    if stage not in AUTO_STAGES:
        return f"Error: stage must be one of {AUTO_STAGES}"
    prev_stage = (state.get("cursor") or {}).get("stage")
    try:
        _advance_stage(state, stage)
    except ValueError as exc:
        return f"Error: {exc}"
    _write_auto_state(bp, state)
    from datetime import datetime as _dt, timezone as _tz
    _append_auto_event_bp(bp, state["session_id"], {
        "ts": _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": state["session_id"],
        "kind": "stage_advanced",
        "task_id": state["cursor"]["task_id"],
        "from": prev_stage,
        "stage": stage,
    })
    cur = state["cursor"]
    # Spec A: lane-aware auto-mode — advancing to a gate-mapped stage records the
    # matching gate on the cursor task. Status gates take status="done"; verdict
    # gates take verdict="pass". Walking the lane sequence in order keeps
    # backlog_record_gate's ordering guard satisfied.
    gate = _AUTO_STAGE_GATE.get(stage)
    gate_note = ""
    if gate:
        tid = cur["task_id"]
        if gate in _VERDICT_GATES:
            res = backlog_record_gate(tid, gate, verdict="pass")
        else:
            res = backlog_record_gate(tid, gate, status="done")
        if res.startswith("Error:"):
            gate_note = f"\n⚠ gate `{gate}` not recorded: {res}"
        else:
            gate_note = f"\nGate `{gate}` recorded."
    return f"Stage → {stage} (task={cur['task_id']}, model={cur['model']}){gate_note}"


@mcp.tool()
def backlog_auto_complete_task(
    status: str,
    summary: str = "",
    commits: list[str] | None = None,
    fail_reason: str = "",
    handover_id: str = "",
) -> str:
    """Finalize the current cursor task and advance to the next pending task.

    Args:
        status: 'done' | 'failed' | 'blocked'.
        summary: One-paragraph summary returned by the task subagent.
        commits: Commit shas produced by the task.
        fail_reason: Required if status != 'done'. One of tests-failed |
                     spec-rejected | blocked | crashed | user-aborted.
        handover_id: If a per-task handover was written, its id.
    """
    if status not in AUTO_TASK_STATUSES:
        return f"Error: status must be one of {AUTO_TASK_STATUSES}"
    if status != "done" and not fail_reason:
        return "Error: fail_reason is required when status != 'done'"
    bp = _backlog_path()
    state = _read_auto_state(bp)
    if not state:
        return "No auto run in progress."
    completed_tid = (state.get("cursor") or {}).get("task_id")
    try:
        _complete_current_task(
            state,
            status=status,
            summary=summary,
            commits=commits or [],
            fail_reason=fail_reason,
            handover_id=handover_id,
        )
    except ValueError as exc:
        return f"Error: {exc}"
    _write_auto_state(bp, state)
    from datetime import datetime as _dt, timezone as _tz
    _append_auto_event_bp(bp, state["session_id"], {
        "ts": _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": state["session_id"],
        "kind": "task_completed" if status == "done" else "task_failed",
        "task_id": completed_tid,
        "status": status,
        "summary": summary,
        "commits": commits or [],
        "fail_reason": fail_reason,
        "handover_id": handover_id,
    })

    if state["cursor"] is None:
        return (
            f"Task complete (status={status}). No more pending tasks — auto run is done.\n"
            f"Completed: {len(state['completed'])}, Failed: {len(state['failed'])}.\n"
            f"Use `backlog_auto_finish` to clear state and write run-level handover."
        )
    cur = state["cursor"]
    return (
        f"Task complete (status={status}). Next: {cur['task_id']} (model={cur['model']}, stage={cur['stage']}).\n"
        f"Completed so far: {len(state['completed'])}, Pending: {len(state['pending'])}, Failed: {len(state['failed'])}."
    )


@mcp.tool()
def backlog_auto_finish() -> str:
    """Clear the auto state file. Call after the run-level handover is written."""
    bp = _backlog_path()
    state = _read_auto_state(bp)
    if not state:
        return "No auto run to finish."
    cleared = _clear_auto_state(bp)
    return f"Auto run cleared. Final: completed={len(state.get('completed') or [])}, failed={len(state.get('failed') or [])}." if cleared else "No state to clear."


@mcp.tool()
def backlog_auto_abort() -> str:
    """Abort an in-progress auto run, leaving completed work intact."""
    bp = _backlog_path()
    state = _read_auto_state(bp)
    if not state:
        return "No auto run to abort."
    cleared = _clear_auto_state(bp)
    return f"Auto run aborted. Completed: {len(state.get('completed') or [])}." if cleared else "Nothing to abort."


@mcp.tool()
def backlog_lesson_match(
    task_title: str = "",
    touched_files: list[str] | None = None,
    verbose: bool = False,
) -> str:
    """Find lessons matching a task by title and/or file globs.

    Used by `pick-task` to inject relevant lessons before a task starts.
    Returns up to 3 best-match lesson summaries.

    Args:
        task_title: Task title to match against lesson trigger phrases.
        touched_files: File paths the task will touch (matched against lesson
            file glob triggers).
        verbose: If True, return full metadata per lesson (kind, tier,
            reinforce_count). Slim (default) returns id + tldr pills only.
    """
    bp = _backlog_path()
    if not bp.exists():
        return "No backlog found."
    matches = _match_lessons_for_task(
        bp,
        {"title": task_title or ""},
        touched_files=touched_files or [],
    )
    if not matches:
        return "No matching lessons."
    lines = []
    for fm, _body in matches:
        if verbose:
            lines.append(
                f"- {fm.get('id')} [{fm.get('kind')}] x{fm.get('reinforce_count', 0)}: {fm.get('title')}"
            )
        else:
            # Slim: id — tldr (omit reinforce_count, kind, tier)
            tldr = fm.get("tldr") or fm.get("title", "")
            lines.append(f"- {fm.get('id')} — {tldr}")
    return "\n".join(lines)


@mcp.tool()
def backlog_add_task(
    title: str, epic: str, priority: str = "medium", notes: str = "",
    docs: str = "", depends_on: str = "", sub_repo: str = "",
    stage: int | None = None, estimate: str = "", phase: str = "",
    anchors: str = "",
    tldr: str = "", next_step: str = "", task_id: str = "",
) -> str:
    """Create a new task under an epic. Auto-generates the task ID unless task_id is supplied.

    Args:
        title: Short imperative description of the task
        epic: Epic ID (e.g., "ue-plugin", "desktop-app", "cpp-parser")
        priority: critical, high, medium (default), or low
        notes: Optional freeform context
        docs: Optional doc references as "key:path" pairs separated by semicolons (e.g., "plan:docs/plans/foo.md;spec:docs/specs/bar.md")
        depends_on: Optional comma-separated task IDs this task depends on (e.g., "cpp-parser-002,cpp-parser-003")
        sub_repo: Optional sub-repo directory name for monorepo projects
        stage: Optional stage number for phased work
        estimate: Optional size estimate (e.g., "S", "M", "L")
        phase: Optional phase ID to assign this task to
        anchors: Optional comma-separated glob patterns or URLs declaring target files/systems (e.g., "src/auth/**,localhost:3000/api/auth")
        tldr: One-sentence essence of the task. Auto-generated from notes or title when omitted.
        next_step: Concrete immediate action to take on this task.
        task_id: Override the auto-generated task ID. Must be unique. Defaults to {epic}-{NNN}.
    """
    priority = _normalize_priority(priority)
    if priority not in VALID_PRIORITIES:
        return f"Error: invalid priority `{priority}`. Valid: {', '.join(PRIORITY_NAMES)}"

    data = _load()
    epic_obj = _find_epic(data, epic)
    if not epic_obj:
        return f"Error: epic `{epic}` not found. Valid epics: {_epic_names(data)}"

    # Resolve task ID: caller-supplied or auto-generated
    if task_id:
        if _find_task(data, task_id):
            return f"Error: task ID `{task_id}` already exists"
        new_id = task_id
    else:
        # Generate ID: {epic-id}-{NNN}
        tasks = epic_obj.get("tasks", [])
        max_suffix = 0
        prefix = f"{epic}-"
        for t in tasks:
            tid = t["id"]
            if tid.startswith(prefix):
                suffix_str = tid[len(prefix):]
                try:
                    max_suffix = max(max_suffix, int(suffix_str))
                except ValueError:
                    pass
        new_id = f"{epic}-{max_suffix + 1:03d}"

    # tldr: use supplied value, or auto-generate from notes/title
    tldr_autogen = False
    if not tldr:
        body_source = notes or title
        tldr = extract_tldr(body_source) or title[:TLDR_MAX_CHARS]
        tldr_autogen = True

    new_task: dict = {
        "id": new_id,
        "title": title,
        "tldr": tldr,
        "status": "todo",
        "priority": priority,
        "created": _now(),
        "last_referenced": _now(),
        "notes": notes,
    }
    if tldr_autogen:
        new_task["tldr_autogen"] = True
    if next_step:
        new_task["next_step"] = next_step

    # Optional fields
    if depends_on:
        dep_ids = [d.strip() for d in depends_on.split(",") if d.strip()]
        for dep_id in dep_ids:
            if not _find_task(data, dep_id):
                return f"Error: dependency `{dep_id}` not found"
        new_task["depends_on"] = dep_ids
    if sub_repo:
        new_task["sub_repo"] = sub_repo
    if stage is not None:
        new_task["stage"] = stage
    if estimate:
        new_task["estimate"] = estimate
    if not phase:
        return "Error: `phase` is required — every task must belong to a phase. Use `backlog_phase_status()` to see available phases."
    if not _find_phase(data, phase):
        return f"Error: phase `{phase}` not found. Use `backlog_phase_status()` to see available phases."
    new_task["phase"] = _find_phase(data, phase)["id"]
    if anchors:
        anchor_list = [a.strip() for a in anchors.split(",") if a.strip()]
        new_task["anchors"] = anchor_list

    # Parse docs if provided: "plan:path;spec:path"
    if docs:
        parsed_docs = {}
        for pair in docs.split(";"):
            pair = pair.strip()
            if ":" in pair:
                k, v = pair.split(":", 1)
                k, v = k.strip(), v.strip()
                if k in VALID_DOC_KEYS:
                    parsed_docs[k] = v
        if parsed_docs:
            new_task["docs"] = parsed_docs

    # Assign lane (standard; bumped to full for high/critical priority) and
    # initialize gate_state mirror so the slim field tier is populated on creation.
    new_task["lane"] = _default_lane(new_task.get("priority", "medium"))
    new_task["gate_state"] = _compute_gate_state(new_task)
    new_task["merge_gate_state"] = ""   # no merges yet

    if "tasks" not in epic_obj:
        epic_obj["tasks"] = []
    epic_obj["tasks"].append(new_task)

    _mutate_and_save(data)
    _enqueue_linear_push_if_synced(new_id, task=new_task)

    # Soft cap enforcement (only when explicitly set on the epic via `max_tasks`).
    # The previous default of 8 has been lifted — large epics with many tasks are
    # legitimate, and notes/work counts are not capped by default. Set
    # `epic.max_tasks` explicitly if you want a per-epic budget.
    budget_warning = ""
    if "max_tasks" in epic_obj:
        active_count = sum(
            1 for t in epic_obj.get("tasks", [])
            if t.get("status") not in ("archived", "done")
        )
        max_tasks = epic_obj["max_tasks"]
        if active_count > max_tasks:
            budget_warning = (
                f"\n\n**Warning:** Epic `{epic}` now has {active_count} active tasks "
                f"(this epic's `max_tasks` cap: {max_tasks})."
            )

    return f"Added `{new_id}` — {title} ({priority}) under {epic_obj['name']}" + budget_warning


def _build_worktree_instruction(task_id: str, sub_repo: str, branch: str, worktree: str) -> str:
    """Build a worktree creation instruction for the pick_task response."""
    if worktree:
        return f"\n\n**Worktree:** Already recorded at `{worktree}` — verify it exists and work there."

    branch_name = branch or f"feature/{task_id}"

    if sub_repo:
        repo_path = sub_repo
        wt_path = f"{sub_repo}/.worktrees/{task_id}"
        cmd = f"cd {sub_repo} && git worktree add .worktrees/{task_id} -b {branch_name}"
    else:
        repo_path = "the appropriate sub-repo"
        wt_path = f"<sub-repo>/.worktrees/{task_id}"
        cmd = f"git worktree add .worktrees/{task_id} -b {branch_name}"

    return (
        f"\n\n**REQUIRED — Create a worktree before writing any code:**\n"
        f"```\n{cmd}\n```\n"
        f"Then record it:\n"
        f"```\nbacklog_update_task({task_id}, branch, {branch_name})\n"
        f"backlog_update_task({task_id}, worktree, .worktrees/{task_id})\n```\n"
        f"All work for this task MUST happen in the worktree, not on the main branch."
    )


@mcp.tool()
def backlog_pick_task(task_id: str, force: bool = False) -> str:
    """Start working on a task — sets it to in-progress. Idempotent if already in-progress.

    Args:
        task_id: The task ID to pick (e.g., "ue-plugin-003")
        force: Force-claim the task even if locked by another session. Use when a previous
               session ended without releasing the lock.
    """
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    _touch_task(task)
    status = task.get("status", "todo")

    # Allowed statuses: todo, in-progress (idempotent), in-review (revert to in-progress)
    locked_by = task.get("locked_by")

    if status == "in-progress":
        if locked_by and locked_by != SESSION_ID:
            if not force:
                return (
                    f"Error: task `{task_id}` is locked by another session (`{locked_by}`). "
                    f"It is already in-progress elsewhere. Pick a different task, or use "
                    f"`backlog_pick_task({task_id}, force=true)` to reclaim it for this session."
                )
            # Force-claim: transfer lock to this session
            task["locked_by"] = SESSION_ID
            _mutate_and_save(data)
        # Idempotent: update session state and lock, return details without mutation
        if not locked_by:
            task["locked_by"] = SESSION_ID
            _mutate_and_save(data)
        _set_session_task(task, epic)
        sub_repo = task.get("sub_repo", "")
        branch = task.get("branch", "")
        worktree = task.get("worktree", "")
        worktree_instruction = _build_worktree_instruction(task_id, sub_repo, branch, worktree)
        # Open handovers stay open automatically under the new model — no resumed transition needed.
        return f"Already in progress: `{task_id}` — {task['title']}\n\n" + _task_context(data, task, epic) + worktree_instruction

    if status not in ("todo", "in-review"):
        # blocked/done tasks cannot be picked — use backlog_update_task to change status first
        return f"Error: task `{task_id}` is `{status}`, expected one of: todo, in-progress, in-review"

    # Surface unmet dependencies (B-050). Pick is an explicit override, so we warn
    # rather than block — but we no longer disagree silently with next_available,
    # which classifies a task with unmet deps as "blocked by dependencies".
    deps = task.get("depends_on", [])
    if isinstance(deps, str):
        deps = [deps]
    unmet_deps: list[str] = []
    for dep_id in deps:
        dep_result = _find_task(data, dep_id)
        dep_status = dep_result[0].get("status", "todo") if dep_result else "todo"
        if dep_status != "done":
            unmet_deps.append(dep_id)
    dep_warning = ""
    if unmet_deps:
        unmet_str = ", ".join(f"`{d}`" for d in unmet_deps)
        dep_warning = (
            f"\n\n⚠️ **Unmet dependencies:** {unmet_str} not yet done. "
            f"Picking anyway (explicit override) — `backlog_next_available` treats this task as blocked."
        )

    task["status"] = "in-progress"
    task["started"] = task.get("started") or _now()
    task["locked_by"] = SESSION_ID

    _mutate_and_save(data)
    _set_session_task(task, epic)

    # Build worktree instruction
    sub_repo = task.get("sub_repo", "")
    branch = task.get("branch", "")
    worktree = task.get("worktree", "")
    worktree_instruction = _build_worktree_instruction(task_id, sub_repo, branch, worktree)

    # Open handovers stay open automatically under the new model — no resumed transition needed.
    return f"Picked `{task_id}` — {task['title']} (locked to this session)" + dep_warning + "\n\n" + _task_context(data, task, epic) + worktree_instruction


def _append_changelog(
    session_title: str,
    done: str,
    decisions: str,
    issues: str,
    tasks_touched: str,
    auto: bool = False,
    auto_stats: str = "",
) -> str:
    """Insert a changelog entry into PROGRESS.md right after the '## Changelog' marker.

    Returns a confirmation message for the tool response.
    """
    title = session_title or "Work session"

    if auto:
        heading = f"### {_today()} — auto"
        entry = f"{heading}\n{auto_stats}\nTasks touched: {tasks_touched}\n"
        try:
            text = _progress_path().read_text(encoding="utf-8")
        except FileNotFoundError:
            return "No PROGRESS.md found."
        marker = "## Changelog"
        idx = text.find(marker)
        if idx == -1:
            return "No changelog section found."
        insert_pos = idx + len(marker)
        new_text = text[:insert_pos] + "\n\n" + entry + text[insert_pos:]
        _progress_path().write_text(new_text, encoding="utf-8")
        return f"\nSession auto-logged to PROGRESS.md."

    heading = f"### {_today()} — {title}"

    lines = [heading]

    # Done section
    done_items = [d.strip() for d in done.strip().split("\n") if d.strip()] if done.strip() else []
    lines.append("**Done:**")
    if done_items:
        for item in done_items:
            bullet = item if item.startswith("- ") else f"- {item}"
            lines.append(bullet)
    else:
        lines.append("- (no items logged)")

    # Decisions section
    decision_items = [d.strip() for d in decisions.strip().split("\n") if d.strip()] if decisions.strip() else []
    lines.append("")
    lines.append("**Decisions:**")
    if decision_items:
        for item in decision_items:
            bullet = item if item.startswith("- ") else f"- {item}"
            lines.append(bullet)
    else:
        lines.append("- None")

    # Issues section
    issue_items = [d.strip() for d in issues.strip().split("\n") if d.strip()] if issues.strip() else []
    lines.append("")
    lines.append("**Issues:**")
    if issue_items:
        for item in issue_items:
            bullet = item if item.startswith("- ") else f"- {item}"
            lines.append(bullet)
    else:
        lines.append("- None")

    # Tasks touched
    lines.append("")
    lines.append(f"**Tasks touched:** {tasks_touched or 'N/A'}")
    lines.append("")
    lines.append("---")
    lines.append("")

    entry = "\n".join(lines)

    # Insert into PROGRESS.md after ## Changelog
    progress_text = _progress_path().read_text(encoding="utf-8")
    changelog_marker = "## Changelog"
    idx = progress_text.find(changelog_marker)
    if idx == -1:
        progress_text += f"\n{changelog_marker}\n\n{entry}"
    else:
        insert_point = idx + len(changelog_marker)
        progress_text = progress_text[:insert_point] + "\n\n" + entry + progress_text[insert_point:]

    _progress_path().write_text(progress_text, encoding="utf-8")
    return f"\n\n**Session logged** to PROGRESS.md changelog."


def _open_bugs_for_task(bp, task_id: str) -> tuple[list[str], list[str]]:
    """Return (open_bugs, fixed_bugs) whose found_in matches task_id.

    Matching is case-insensitive (B-025): a bug filed with found_in="TEST-EPIC-001"
    must still gate task id "test-epic-001". Shared by complete_task and batch_update
    so both honor the same close-gate.
    """
    from taskmaster_v3 import (
        list_bug_ids as _list_bug_ids,
        read_bug as _read_bug,
    )
    open_bugs: list[str] = []
    fixed_bugs: list[str] = []
    tid = (task_id or "").casefold()
    for bid in _list_bug_ids(bp):
        try:
            bfm, _ = _read_bug(bp, bid)
        except (OSError, ValueError):
            continue
        if (bfm.get("found_in") or "").casefold() == tid:
            st = bfm.get("status")
            if st == "open":
                open_bugs.append(bid)
            elif st == "fixed":
                fixed_bugs.append(bid)
    return open_bugs, fixed_bugs


def _completion_block_reason(task) -> str:
    """Return a rejection message if a lane'd task has unsatisfied required gates, else ''."""
    if not task.get("lane"):
        return ""   # laneless => exempt (Spec A rollout rule)
    outstanding = _outstanding_required_gates(task)
    if outstanding:
        return (f"Cannot complete `{task['id']}` — outstanding gates for lane "
                f"`{task['lane']}`: {', '.join(outstanding)}. "
                f"Record each (backlog_record_gate) or skip it (backlog_skip_gate).")
    return ""


@mcp.tool()
def backlog_complete_task(
    task_id: str,
    session_title: str = "",
    done: str = "",
    decisions: str = "",
    issues: str = "",
    tasks_touched: str = "",
    target_status: str = "done",
    auto_summary: bool = False,
    patchnote: str = "",
    release: str = "",
) -> str:
    """Mark a task as done (or in-review) and optionally log a session summary to the PROGRESS.md changelog.

    When session summary fields are provided, a changelog entry is appended automatically.
    This combines the status transition and session logging into one atomic operation.

    Use target_status="in-review" when implementation is complete but the user needs to
    manually test before confirming. Use target_status="done" when no manual testing is needed
    or the user has already confirmed.

    Accepts tasks that are in-progress, in-review, or blocked.

    Args:
        task_id: The task ID to complete (e.g., "ue-plugin-003")
        session_title: Optional session title (e.g., "C++ Parser: Graph Analysis"). Auto-prefixed with today's date.
        done: Optional newline-separated list of accomplishments for the changelog
        decisions: Optional newline-separated list of decisions made
        issues: Optional newline-separated list of issues encountered. Use "None" if none.
        tasks_touched: Optional comma-separated task IDs that changed status this session
        target_status: Target status — "done" (default) or "in-review" (needs manual testing)
        auto_summary: If true, generates a lightweight auto-summary instead of the structured format. Pass git stats as the done field.
        patchnote: Optional 1-2 sentence user-facing release-note line describing what shipped. Leave empty for internal/infra tasks.
        release: Optional release bucket this task ships in (e.g., "pre-alpha", "alpha-1.0"). Groups patchnotes for release notes.
    """
    if target_status not in ("done", "in-review"):
        return f"Error: target_status must be 'done' or 'in-review', got '{target_status}'"

    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    _touch_task(task)
    status = task.get("status", "todo")

    if status not in ("in-progress", "in-review", "blocked"):
        return f"Error: task `{task_id}` is `{status}`, expected one of: in-progress, in-review, blocked"

    # Bug close-gate (per bug-tier redesign)
    bp = _backlog_path()
    from taskmaster_v3 import (
        archive_bug as _archive_bug,
        sync_bug_index as _sync_bug_index,
    )
    open_bugs, fixed_bugs = _open_bugs_for_task(bp, task_id)
    if open_bugs:
        return (
            f"Cannot complete {task_id} — {len(open_bugs)} open bug(s) linked via found_in: "
            f"{', '.join(open_bugs)}.\n"
            f"Resolve each (fix/adopt/shelve/promote) before closing the task."
        )

    # Gate-completeness guard (Spec A): a lane'd task may only go to `done`
    # once its required gates are satisfied. Laneless tasks are exempt.
    # Does not apply to target_status == "in-review".
    if target_status == "done":
        block = _completion_block_reason(task)
        if block:
            return block

    # Warn if skipping in-review when going straight to done
    review_warning = ""
    if target_status == "done" and status == "in-progress":
        review_warning = "\n\n**Note:** Task went directly from in-progress → done, skipping the in-review stage. Consider using `in-review` first so the user can manually test and confirm it works."

    task["status"] = target_status
    if target_status == "done":
        task["completed"] = _now()
    task.pop("locked_by", None)

    if patchnote:
        task["patchnote"] = patchnote
    if release:
        task["release"] = release

    _mutate_and_save(data)
    _enqueue_linear_push_if_synced(task_id, task=task)
    if target_status == "done":
        _clear_session_task(task_id)

    if target_status == "done":
        try:
            from taskmaster_v3 import smart_auto_close_handovers as _smart_close
            # Collect done/archived task IDs from backlog for smart-close evaluation.
            _all_terminal: set[str] = set()
            for _epic in data.get("epics", []):
                for _t in _epic.get("tasks", []):
                    if _t.get("status") in ("done", "archived"):
                        _all_terminal.add(_t["id"])
            _all_terminal.add(task_id)  # the one we just transitioned
            smart_close_result = _smart_close(
                _backlog_path(),
                triggering_task_id=task_id,
                done_or_archived_ids=_all_terminal,
            )
            flipped_handovers = smart_close_result["closed"] + smart_close_result["flagged"]
        except Exception:
            flipped_handovers = []
        if flipped_handovers:
            data2 = _load()
            _sync_handover_index(data2, _backlog_path())
            _save(data2)

    # Archive bugs that were fixed during this task (per bug-tier redesign)
    if target_status == "done" and fixed_bugs:
        for bid in fixed_bugs:
            try:
                _archive_bug(bp, bid)
            except (OSError, ValueError):
                pass
        data3 = _load()
        _sync_bug_index(data3, bp)
        _save(data3)

    # Append changelog entry if session summary provided
    changelog_msg = ""
    if auto_summary:
        changelog_msg = _append_changelog(session_title, done, decisions, issues, tasks_touched, auto=True, auto_stats=done)
    elif session_title or done:
        changelog_msg = _append_changelog(session_title, done, decisions, issues, tasks_touched)

    # Suggest next task in same epic
    next_todo = [t for t in epic.get("tasks", []) if t.get("status") == "todo"]
    next_todo.sort(key=lambda t: ({"critical": 0, "high": 1, "medium": 2, "low": 3}.get(t.get("priority", "medium"), 9)))
    suggestion = ""
    if next_todo:
        n = next_todo[0]
        suggestion = f"\n\n**Next in {epic['name']}:** `{n['id']}` — {n['title']} ({n.get('priority', 'medium')})"

    status_label = "Completed" if target_status == "done" else "Moved to in-review"
    return f"{status_label} `{task_id}` — {task['title']}" + changelog_msg + review_warning + suggestion


@mcp.tool()
def backlog_release_notes(release: str = "", group_by: str = "epic", include_unreleased: bool = False) -> str:
    """Aggregate user-facing patchnotes across tasks for a given release bucket.

    Returns markdown output grouping patchnotes by epic or phase — ready to feed into a
    release-notes writer. Only tasks with a non-empty `patchnote` field are included.
    Internal/infra tasks (no patchnote) are omitted by design.

    Args:
        release: Release bucket to filter on (e.g., "pre-alpha", "alpha-1.0"). Empty = all releases.
        group_by: "epic" (default) or "phase" — how to group the patchnotes in the output.
        include_unreleased: If true, also include tasks that have a patchnote but no release tag.
    """
    if group_by not in ("epic", "phase"):
        return f"Error: group_by must be 'epic' or 'phase', got '{group_by}'"

    data = _load()
    phases_by_id = {p["id"]: p for p in data.get("phases", [])}

    # group_key -> list of (task, epic)
    groups: dict[str, list[tuple[dict, dict]]] = {}
    group_labels: dict[str, str] = {}

    for epic in data.get("epics", []):
        for task in epic.get("tasks", []):
            note = task.get("patchnote", "").strip()
            if not note:
                continue
            task_release = task.get("release", "").strip()
            if release:
                if task_release != release:
                    continue
            elif not include_unreleased and not task_release:
                continue

            if group_by == "epic":
                key = epic["id"]
                group_labels[key] = epic.get("name", epic["id"])
            else:
                phase_id = task.get("phase", "")
                key = phase_id or "_no_phase"
                if phase_id and phase_id in phases_by_id:
                    group_labels[key] = phases_by_id[phase_id].get("name", phase_id)
                else:
                    group_labels[key] = "Unphased"

            groups.setdefault(key, []).append((task, epic))

    if not groups:
        filt = f" for release `{release}`" if release else ""
        return f"No patchnotes found{filt}."

    header = f"# Release Notes" + (f" — `{release}`" if release else " — all releases")
    lines = [header, ""]
    for key in sorted(groups.keys(), key=lambda k: group_labels.get(k, k).lower()):
        lines.append(f"## {group_labels[key]}")
        lines.append("")
        for task, _epic in groups[key]:
            tag = f" ({task['id']})"
            lines.append(f"- {task['patchnote'].strip()}{tag}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


VALID_ARCHIVE_REASONS = {"done", "deprecated", "duplicate", "wont-fix", "superseded"}


@mcp.tool()
def backlog_archive_task(task_id: str, reason: str = "done") -> str:
    """Archive a task — hides it from the board and default listings.
    Tasks with status `done`, `blocked`, or `todo` can be archived. Archiving captures WHY the task
    was archived (e.g., verified and done, deprecated, duplicate, won't fix).
    Note: `todo` tasks require a reason other than "done" (e.g., deprecated, duplicate, wont-fix, superseded).

    Args:
        task_id: The task ID to archive (e.g., "ue-plugin-003")
        reason: Why the task is being archived. One of: done, deprecated, duplicate, wont-fix, superseded. Default: done.
    """
    if reason not in VALID_ARCHIVE_REASONS:
        return f"Error: invalid reason `{reason}`. Valid: {', '.join(sorted(VALID_ARCHIVE_REASONS))}"

    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    status = task.get("status", "todo")

    if status not in ("done", "blocked", "todo"):
        return f"Error: task `{task_id}` is `{status}`, only `done`, `blocked`, or `todo` tasks can be archived"

    # todo tasks require a non-"done" reason
    if status == "todo" and reason == "done":
        return f"Error: cannot archive a `todo` task with reason `done`. Use one of: deprecated, duplicate, wont-fix, superseded"

    task["status"] = "archived"
    task["archive_reason"] = reason
    task["archived"] = _now()
    task.pop("locked_by", None)
    _mutate_and_save(data)
    _enqueue_linear_push_if_synced(task_id, task=task)

    # Smart-close open handovers that reference this task.
    try:
        from taskmaster_v3 import smart_auto_close_handovers as _smart_close
        _all_terminal: set[str] = set()
        for _epic in data.get("epics", []):
            for _t in _epic.get("tasks", []):
                if _t.get("status") in ("done", "archived"):
                    _all_terminal.add(_t["id"])
        _all_terminal.add(task_id)  # the one we just archived
        smart_close_result = _smart_close(
            _backlog_path(),
            triggering_task_id=task_id,
            done_or_archived_ids=_all_terminal,
        )
        flipped_handovers = smart_close_result["closed"] + smart_close_result["flagged"]
        if flipped_handovers:
            data2 = _load()
            _sync_handover_index(data2, _backlog_path())
            _save(data2)
    except Exception:
        pass

    return f"Archived `{task_id}` — {task['title']} (reason: {reason})"


# ── Worktree Discovery ───────────────────────────────────


# (Note: a richer `_discover_sub_repos(project_root: Path) -> list[dict]` is
#  defined later in the Project Structure helpers block and supersedes the
#  earlier zero-arg stub that lived here. The stub had no callers in the
#  codebase or tests; project-structure-visibility-003 reclaims the name.)


def _git_subprocess_kwargs() -> dict:
    """Common kwargs for git subprocess calls — prevents hangs on Windows."""
    kwargs: dict = {"capture_output": True, "text": True, "timeout": 3}
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}
    kwargs["env"] = env
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


@mcp.tool()
def backlog_last_session() -> str:
    """Get the most recent session summary from the PROGRESS.md changelog.
    Returns the last changelog entry (everything between the first and second ### headings)."""
    try:
        text = _progress_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return "No PROGRESS.md found."

    changelog_marker = "## Changelog"
    idx = text.find(changelog_marker)
    if idx == -1:
        return "No changelog section found in PROGRESS.md."

    # Find the first ### heading after ## Changelog
    after_changelog = text[idx + len(changelog_marker):]
    first_entry_start = after_changelog.find("### ")
    if first_entry_start == -1:
        return "No session entries found in changelog."

    # Find the second ### heading (end of first entry)
    rest = after_changelog[first_entry_start:]
    second_entry_start = rest.find("### ", 4)  # skip the first "### "
    if second_entry_start == -1:
        # Only one entry — take everything
        entry = rest.strip()
    else:
        entry = rest[:second_entry_start].strip()

    return f"**Last Session:**\n\n{entry}" if entry else "No session entries found in changelog."


# ── Session State (in-memory, per MCP server process) ────

_session_task: dict | None = None  # {"id", "title", "epic", "picked_at"}


def _set_session_task(task: dict, epic: dict) -> None:
    global _session_task
    _session_task = {
        "id": task["id"],
        "title": task["title"],
        "epic": epic["id"],
        "picked_at": datetime.now().isoformat(timespec="seconds"),
    }


def _clear_session_task(task_id: str) -> None:
    global _session_task
    if _session_task and _session_task["id"] == task_id:
        _session_task = None


ALLOWED_FIELDS = {"title", "status", "priority", "notes", "branch", "worktree", "blockers", "docs", "depends_on", "sub_repo", "stage", "estimate", "locked_by", "review_instructions", "phase", "anchors", "blast_radius_depth", "patchnote", "release", "tldr", "next_step", "component", "design_change", "lane"}
VALID_STATUSES = {"todo", "in-progress", "in-review", "done", "archived", "blocked"}
# Spec A Task 11: forward-transition table enforced on lane'd tasks via
# backlog_update_task. Laneless tasks are exempt (old permissive behavior).
# Same-status writes (value == current) bypass the table.
LEGAL_STATUS_TRANSITIONS = {
    "todo":        {"in-progress", "blocked", "archived"},
    "in-progress": {"in-review", "done", "blocked", "todo", "archived"},
    "in-review":   {"done", "in-progress", "blocked", "archived"},
    "blocked":     {"todo", "in-progress", "in-review", "archived"},
    "done":        {"in-review", "archived"},
    "archived":    {"todo"},
}
VALID_PRIORITIES = {"critical", "high", "medium", "low"}
VALID_DOC_KEYS = {"plan", "spec", "roadmap", "design", "analysis"}


@mcp.tool()
def backlog_update_task(
    task_id: str, field: str = "", value: str = "",
    tldr: str = "", next_step: str = "",
) -> str:
    """Update a single field on a task. Status changes trigger appropriate date updates.

    Two calling styles are supported:
    - Field/value style: backlog_update_task(task_id, field, value) — the classic API.
    - Keyword style: backlog_update_task(task_id, tldr="...", next_step="...") — for tldr/next_step.

    Args:
        task_id: The task ID (e.g., "ue-plugin-003")
        field: Field to update — one of: title, status, priority, notes, branch, worktree, blockers,
            docs, depends_on, sub_repo, stage, estimate, locked_by, review_instructions, phase,
            patchnote, release, tldr, next_step
        value: New value. Format varies by field:
            - docs: "key:path" (e.g., "plan:docs/plans/foo.md")
            - depends_on: comma-separated task IDs (e.g., "cpp-parser-002,cpp-parser-003")
            - stage: integer
            - estimate: size string (e.g., "S", "M", "L")
            - sub_repo: sub-repo directory name for monorepo projects
            - locked_by: session ID to claim the lock, or "" to clear it
            - phase: phase ID to assign, or "" to clear
            - anchors: comma-separated glob patterns/URLs, or "" to clear
            - patchnote: 1-2 sentence user-facing release-note line, or "" to clear
            - release: release bucket this task ships in (e.g., "pre-alpha", "alpha-1.0"), or "" to clear
        tldr: One-sentence essence of the task (keyword style only).
        next_step: Concrete immediate action to take on this task (keyword style only).
    """
    # Guard against ambiguous mixed-style calls — silent field drops are worse
    # than an explicit error. Pick one calling convention per call.
    if (tldr or next_step) and (field or value):
        return "Error: use either field/value style or keyword style (tldr=/next_step=), not both"

    # Keyword style: tldr= / next_step= kwargs take precedence over field/value.
    if tldr or next_step:
        data = _load()
        result = _find_task(data, task_id)
        if not result:
            return f"Error: task `{task_id}` not found"
        task, epic = result
        _touch_task(task)
        updated = []
        if tldr:
            task["tldr"] = tldr
            task.pop("tldr_autogen", None)  # caller-supplied tldr is no longer auto-generated
            updated.append(f"tldr → {tldr}")
        if next_step:
            task["next_step"] = next_step
            updated.append(f"next_step → {next_step}")
        _mutate_and_save(data)
        _enqueue_linear_push_if_synced(task_id, task=task)
        return f"Updated `{task_id}`: " + "; ".join(updated)

    # Classic field/value style
    if not field:
        return "Error: provide either `field`/`value` or keyword args `tldr`/`next_step`"
    if field not in ALLOWED_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(ALLOWED_FIELDS))}"

    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    _touch_task(task)

    if field == "status":
        if value not in VALID_STATUSES:
            return f"Error: invalid status `{value}`. Valid: {', '.join(sorted(VALID_STATUSES))}"
        # Spec A Task 11: enforce the forward-transition table for lane'd tasks
        # only. Laneless tasks keep the old permissive behavior. Same-status
        # writes (value == current) are always allowed — the guard only checks
        # when the status actually changes.
        cur = task.get("status", "todo")
        if task.get("lane") and value != cur:
            allowed = LEGAL_STATUS_TRANSITIONS.get(cur, set())
            if value not in allowed:
                return (f"Error: illegal transition `{cur}` → `{value}` for `{task_id}`. "
                        f"Legal: {', '.join(sorted(allowed)) or '(none)'}.")
            if value == "done":
                block = _completion_block_reason(task)
                if block:
                    return block
        task["status"] = value
        if value == "in-progress" and not task.get("started"):
            task["started"] = _now()
        elif value == "done" and not task.get("completed"):
            task["completed"] = _now()
        # Note: archived status is allowed via update_task for flexibility (prefer backlog_archive_task)
        # Clear lock when leaving in-progress
        if value not in ("in-progress",):
            task.pop("locked_by", None)
    elif field == "priority":
        value = _normalize_priority(value)
        if value not in VALID_PRIORITIES:
            return f"Error: invalid priority `{value}`. Valid: {', '.join(PRIORITY_NAMES)}"
        task["priority"] = value
    elif field == "docs":
        # Parse "key:path" format, e.g. "plan:docs/plans/2026-03-11-foo.md"
        if ":" not in value:
            return f"Error: docs value must be `key:path` format (e.g., `plan:docs/plans/foo.md`). Valid keys: {', '.join(sorted(VALID_DOC_KEYS))}"
        doc_key, doc_path = value.split(":", 1)
        doc_key = doc_key.strip()
        doc_path = doc_path.strip()
        if doc_key not in VALID_DOC_KEYS:
            return f"Error: invalid docs key `{doc_key}`. Valid: {', '.join(sorted(VALID_DOC_KEYS))}"
        if "docs" not in task or not isinstance(task.get("docs"), dict):
            task["docs"] = {}
        task["docs"][doc_key] = doc_path
    elif field == "depends_on":
        # Comma-separated task IDs, e.g. "cpp-parser-002,cpp-parser-003"
        dep_ids = [d.strip() for d in value.split(",") if d.strip()]
        # Validate all deps exist
        for dep_id in dep_ids:
            if not _find_task(data, dep_id):
                return f"Error: dependency `{dep_id}` not found"
        task["depends_on"] = dep_ids
    elif field == "stage":
        try:
            task["stage"] = int(value)
        except ValueError:
            return f"Error: stage must be an integer, got `{value}`"
    elif field == "locked_by":
        if value == "" or value.lower() == "none":
            task.pop("locked_by", None)
        else:
            task["locked_by"] = value
    elif field == "phase":
        if value == "" or value.lower() == "none":
            task.pop("phase", None)
        else:
            if not _find_phase(data, value):
                return f"Error: phase `{value}` not found"
            task["phase"] = _find_phase(data, value)["id"]
    elif field == "anchors":
        if value == "" or value.lower() == "none":
            task.pop("anchors", None)
        else:
            task["anchors"] = [a.strip() for a in value.split(",") if a.strip()]
    elif field in ("patchnote", "release"):
        if value == "" or value.lower() == "none":
            task.pop(field, None)
        else:
            task[field] = value
    elif field == "blast_radius_depth":
        if value == "" or value.lower() == "none":
            task.pop("blast_radius_depth", None)
        elif value in ("shallow", "deep"):
            task["blast_radius_depth"] = value
        else:
            return f"Error: `blast_radius_depth` must be 'shallow', 'deep', or '' to clear. Got: `{value}`"
    elif field == "tldr":
        # Caller-supplied tldr clears the autogen flag. Empty value is rejected —
        # tldr is required on every task; use the kwarg API or recreate the task
        # to refresh from autogen.
        if not value:
            return "Error: tldr cannot be cleared — provide a non-empty value or use autogen"
        task["tldr"] = value
        task.pop("tldr_autogen", None)
    elif field == "next_step":
        if value == "" or value.lower() == "none":
            task.pop("next_step", None)
        else:
            task["next_step"] = value
    elif field == "lane":
        if value not in _VALID_LANES:
            return (f"Error: invalid lane `{value}`. "
                    f"Valid: {', '.join(_VALID_LANES)}")
        task["lane"] = value
        task["gate_state"] = _compute_gate_state(task)
    elif field == "component":
        if value == "" or value.lower() == "none":
            task.pop("component", None)
        else:
            comps = (epic.get("components") or {})
            if value not in comps:
                declared = ", ".join(sorted(comps)) or "(none declared)"
                return (f"Error: component `{value}` not declared on epic `{epic['id']}`. "
                        f"Declared: {declared}. Add it via backlog_update_epic(<epic>, 'components', ...).")
            task["component"] = value
    elif field == "design_change":
        truthy = value.strip().lower() in ("true", "1", "yes")
        if truthy:
            design = epic.get("design_status", "exploring")
            if design == "locked":
                return (f"Error: epic `{epic['id']}` design is locked — cannot flag a "
                        f"design-change task. To reopen, set the epic to revising "
                        f"(backlog_update_epic('{epic['id']}', 'design_status', 'revising')) "
                        f"and record the reason as a decision (taskmaster:decision).")
            task["design_change"] = True
        else:
            task.pop("design_change", None)
    else:
        task[field] = value

    _mutate_and_save(data)

    # Plan C: auto-detect inline ID mentions when body-bearing fields change.
    if field in ("notes", "review_instructions"):
        bp = _backlog_path()
        from taskmaster_v3 import auto_link_on_save as _auto_link_on_save
        try:
            _auto_link_on_save(bp, task_id)
        except Exception:
            pass

    _enqueue_linear_push_if_synced(task_id, task=task)

    return f"Updated `{task_id}` field `{field}` → {value}"


@mcp.tool()
def backlog_record_gate(
    task_id: str,
    gate: str,
    verdict: str = "",
    status: str = "",
    commit_sha: str = "",
    spec_path: str = "",
    codex_used: bool = False,
    critical_count: int = 0,
    important_count: int = 0,
) -> str:
    """Record the outcome of a pipeline gate on a task. Generalizes spec-review to
    every gate (spec, plan, tests, impl, spec-review, plan-review, design-review,
    review-gate). Status gates take status="done"; review gates take a verdict in
    pass/warn/fail. Overwrites any prior record for that gate.

    Enforces ordering: for a task WITH a lane, an earlier required gate must be
    satisfied (pass/done/skipped) before a later one is recorded.

    Args:
        task_id: Task id.
        gate: One of spec|plan|tests|impl|spec-review|plan-review|design-review|review-gate.
        verdict: pass|warn|fail (for review gates).
        status: done (for status gates).
        commit_sha, spec_path, codex_used, critical_count, important_count: optional meta.
    """
    if gate not in _VALID_GATES:
        return f"Error: invalid gate `{gate}`. Valid: {', '.join(_VALID_GATES)}"

    is_verdict = gate in _VERDICT_GATES
    if is_verdict:
        if verdict not in _VALID_GATE_VERDICTS:
            return (f"Error: gate `{gate}` requires verdict in "
                    f"{', '.join(_VALID_GATE_VERDICTS)}, got `{verdict or '(none)'}`")
    else:
        if status != "done":
            return f"Error: status gate `{gate}` requires status=\"done\", got `{status or '(none)'}`"

    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, _epic = result
    _touch_task(task)

    lane = task.get("lane")
    if lane and gate in _VERDICT_GATES:
        req = _blocking_gates(lane)
        gates_now = task.get("gates") or {}
        if gate in req:
            idx = req.index(gate)
            for earlier in req[:idx]:
                if not _gate_satisfied(gates_now.get(earlier)):
                    return (f"Error: cannot record `{gate}` for `{task_id}` — "
                            f"earlier required gate `{earlier}` is not satisfied "
                            f"(pass/skipped). Record or skip it first.")

    rec = {"at": _now()}
    if is_verdict:
        rec["verdict"] = verdict
        if commit_sha:
            rec["commit_sha"] = commit_sha
        if spec_path:
            rec["spec_path"] = spec_path
        rec["codex_used"] = bool(codex_used)
        rec["critical_count"] = int(critical_count)
        rec["important_count"] = int(important_count)
    else:
        rec["status"] = "done"
        if commit_sha:
            rec["commit_sha"] = commit_sha

    task.setdefault("gates", {})[gate] = rec
    task["gate_state"] = _compute_gate_state(task)
    _mutate_and_save(data)
    outcome = verdict if is_verdict else "done"
    return f"Recorded gate `{gate}` = {outcome} for `{task_id}` (state: {task['gate_state'] or 'laneless'})"


def _resolved_merge_targets() -> list[dict]:
    try:
        m = load_project_manifest(_project_root_or_cwd())
        if m is not None:
            return m.merge_targets_resolved()
    except Exception:
        pass
    from project import DEFAULT_MERGE_TARGETS
    return [dict(d) for d in DEFAULT_MERGE_TARGETS]


@mcp.tool()
def backlog_record_merge(task_id: str, rung: str, sha: str, merged_at: str = "") -> str:
    """Stamp a merge rung on a task: records that the task's branch landed at `rung`
    (e.g. develop|stage|master) at merge commit `sha`. Idempotent overwrite per rung.
    The manual fallback for the PostToolUse merge-recorder hook. Not a pipeline gate —
    no ordering is enforced; a rung label outside the ladder (e.g. "branch:<name>") is
    still recorded to preserve the audit trail.

    Args:
        task_id: Task id.
        rung: Rung label (ladder label or "branch:<name>" for untracked targets).
        sha: Merge commit SHA.
        merged_at: ISO timestamp; defaults to now.
    """
    if not (rung or "").strip():
        return "Error: rung is required"
    if not (sha or "").strip():
        return "Error: sha is required"
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, _epic = result
    _touch_task(task)
    task.setdefault("merge_status", {})[rung] = {
        "merged_at": merged_at or _now(), "merge_commit": sha,
    }
    task["merge_gate_state"] = _compute_merge_gate_state(task, _resolved_merge_targets())
    _mutate_and_save(data)
    return f"Recorded merge for rung `{rung}` on `{task_id}` (sha={sha[:7]}, ladder: {task['merge_gate_state'] or 'none'})"


@mcp.tool()
def backlog_skip_gate(task_id: str, gate: str, reason: str, by: str = "claude") -> str:
    """Record an explicit, audited skip of a pipeline gate — the ONLY way past a
    required gate without satisfying it. Always succeeds (for a valid gate+reason)
    and is flagged on the dashboard. No silent skips exist anywhere else.

    Args:
        task_id: Task id.
        gate: The gate to skip (see backlog_record_gate for valid names).
        reason: Required non-empty justification (becomes the paper trail).
        by: "claude" or "user".
    """
    if gate not in _VALID_GATES:
        return f"Error: invalid gate `{gate}`. Valid: {', '.join(_VALID_GATES)}"
    if not (reason or "").strip():
        return f"Error: skip_gate requires a non-empty reason (this is the audit trail)."

    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, _epic = result
    _touch_task(task)

    task.setdefault("gates", {})[gate] = {
        "skipped": True, "reason": reason.strip(), "by": by, "at": _now(),
    }
    task["gate_state"] = _compute_gate_state(task)
    _mutate_and_save(data)
    return f"⚠ Skipped gate `{gate}` for `{task_id}` — reason: {reason.strip()} (by {by})"


@mcp.tool()
def backlog_clear_gate(task_id: str, gate: str) -> str:
    """Remove a single gate record from a task and recompute gate_state.

    Args:
        task_id: Task id.
        gate: The gate to clear (see backlog_record_gate for valid names).
    """
    if gate not in _VALID_GATES:
        return f"Error: invalid gate `{gate}`. Valid: {', '.join(_VALID_GATES)}"
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, _ = result
    _touch_task(task)
    gates = task.get("gates") or {}
    if gate not in gates:
        return f"`{task_id}` had no `{gate}` gate record"
    del gates[gate]
    task["gates"] = gates
    task["gate_state"] = _compute_gate_state(task)
    _mutate_and_save(data)
    return f"Cleared gate `{gate}` on `{task_id}`"


@mcp.tool()
def backlog_task_pipeline(task_id: str) -> str:
    """Show a task's lane, its required gate pipeline, and each gate's recorded
    state (pass/done/skipped/fail/pending), plus the one-line gate_state and the
    list of outstanding gates blocking `done`.
    """
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, _ = result
    lane = task.get("lane")
    if not lane:
        return f"`{task_id}` is laneless (pre-protocol) — no pipeline enforced."
    gates = task.get("gates") or {}
    lines = [f"## Pipeline `{task_id}` — lane: **{lane}**",
             f"gate_state: `{task.get('gate_state') or '(none)'}`", ""]
    for g in _required_gates(lane):
        rec = gates.get(g)
        if not rec:
            mark = "○ pending"
        elif rec.get("skipped"):
            mark = f"⚠ skipped — {rec.get('reason', '')}"
        elif rec.get("verdict"):
            mark = f"{rec['verdict']}"
        else:
            mark = rec.get("status", "?")
        lines.append(f"- `{g}`: {mark}")
    outstanding = _outstanding_required_gates(task)
    lines.append("")
    lines.append("**Outstanding:** " + (", ".join(outstanding) if outstanding else "none — ready for done ✓"))
    return "\n".join(lines)


VALID_SPEC_REVIEW_VERDICTS = {"pass", "warn", "fail"}


@mcp.tool()
def backlog_set_spec_review(
    task_id: str,
    verdict: str,
    spec_path: str,
    codex_used: bool = False,
    critical_count: int = 0,
    important_count: int = 0,
) -> str:
    """Record a spec-review pass on a task. Thin alias over backlog_record_gate(gate="spec-review");
    also keeps the legacy `spec_review` dict for back-compat. Overwrites any prior record.

    Args:
        task_id: The task ID (e.g., "auth-003")
        verdict: One of "pass", "warn", "fail"
        spec_path: Path to the spec/plan file that was reviewed
        codex_used: True if the optional Codex adversarial pass was run
        critical_count: Number of critical findings
        important_count: Number of important findings
    """
    if verdict not in VALID_SPEC_REVIEW_VERDICTS:
        return (
            f"Error: invalid verdict `{verdict}`. "
            f"Valid: {', '.join(sorted(VALID_SPEC_REVIEW_VERDICTS))}"
        )
    out = backlog_record_gate(
        task_id, "spec-review", verdict=verdict, spec_path=spec_path,
        codex_used=codex_used, critical_count=critical_count,
        important_count=important_count,
    )
    if "Error" in out:
        return out
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, _ = result
    task["spec_review"] = {
        "timestamp": _now(),
        "verdict": verdict,
        "codex_used": bool(codex_used),
        "critical_count": int(critical_count),
        "important_count": int(important_count),
        "spec_path": spec_path,
    }
    _mutate_and_save(data)
    return (
        f"Recorded spec-review for `{task_id}`: {verdict} "
        f"(codex={codex_used}, critical={critical_count}, important={important_count})"
    )


@mcp.tool()
def backlog_clear_spec_review(task_id: str) -> str:
    """Remove the spec-review gate (and legacy spec_review mirror) from a task.
    Use when the spec was significantly revised and the prior review is no longer valid.

    Args:
        task_id: The task ID (e.g., "auth-003")
    """
    out = backlog_clear_gate(task_id, "spec-review")
    data = _load()
    result = _find_task(data, task_id)
    if result:
        task, _ = result
        if "spec_review" in task:
            del task["spec_review"]
            _mutate_and_save(data)
    return out


VALID_EPIC_STATUSES = {"active", "planned", "done", "archived"}
ALLOWED_EPIC_FIELDS = {"name", "status", "description", "docs", "components", "design_status"}
VALID_DESIGN_STATUSES = {"exploring", "proposed", "locked", "revising"}


def _validate_components(components: dict) -> str:
    """Return '' if the components block is well-formed, else an error string.

    Shape: { <key>: { "title": str, "after": [<other keys>] } }.
    `after` edges must reference declared component keys (DAG not enforced here).
    """
    if not isinstance(components, dict):
        return "Error: components must be a JSON object {key: {title, after}}"
    keys = set(components)
    for key, spec in components.items():
        if key == "_unassigned":
            return "Error: `_unassigned` is a reserved component key"
        if key.lower() == "none":
            return "Error: `none` (case-insensitive) is a reserved component key"
        if not isinstance(spec, dict):
            return f"Error: component `{key}` must be an object with title/after"
        if "title" in spec and not isinstance(spec["title"], str):
            return f"Error: component `{key}` title must be a string"
        after = spec.get("after", [])
        if not isinstance(after, list):
            return f"Error: component `{key}` after must be a list of component keys"
        for ref in after:
            if ref == key:
                return f"Error: component `{key}` cannot reference itself in `after`"
            if ref not in keys:
                return f"Error: component `{key}` after references unknown component `{ref}`"
    return ""


@mcp.tool()
def backlog_update_epic(epic_id: str, field: str, value: str) -> str:
    """Update a single field on an epic.

    Args:
        epic_id: The epic ID (e.g., "cpp-parser", "ue-plugin", "infra")
        field: Field to update — one of: name, status, description, docs
        value: New value. For status, one of: active, planned, done.
            For docs, use "key:path" format (e.g., "design:docs/design/foo.md").
            Valid doc keys mirror task docs: plan, spec, roadmap, design, analysis.
            Pass "key:" (empty path) to remove a single doc entry.
    """
    if field not in ALLOWED_EPIC_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(ALLOWED_EPIC_FIELDS))}"

    data = _load()
    epic = _find_epic(data, epic_id)
    if not epic:
        return f"Error: epic `{epic_id}` not found"

    if field == "status":
        if value == "archived":
            return "Error: use `backlog_archive_epic` to archive an epic (it cascades to tasks)"
        if value not in VALID_EPIC_STATUSES:
            return f"Error: invalid epic status `{value}`. Valid: {', '.join(sorted(VALID_EPIC_STATUSES))}"

    if field == "docs":
        # Mirror task docs: "key:path" format, sharing VALID_DOC_KEYS.
        if ":" not in value:
            return (
                f"Error: docs value must be `key:path` format "
                f"(e.g., `design:docs/design/foo.md`). "
                f"Valid keys: {', '.join(sorted(VALID_DOC_KEYS))}"
            )
        doc_key, doc_path = value.split(":", 1)
        doc_key = doc_key.strip()
        doc_path = doc_path.strip()
        if doc_key not in VALID_DOC_KEYS:
            return f"Error: invalid docs key `{doc_key}`. Valid: {', '.join(sorted(VALID_DOC_KEYS))}"
        if "docs" not in epic or not isinstance(epic.get("docs"), dict):
            epic["docs"] = {}
        if doc_path == "":
            epic["docs"].pop(doc_key, None)
            if not epic["docs"]:
                epic.pop("docs", None)
            _mutate_and_save(data)
            return f"Cleared epic `{epic_id}` doc key `{doc_key}`"
        epic["docs"][doc_key] = doc_path
        _mutate_and_save(data)
        return f"Updated epic `{epic_id}` doc `{doc_key}` → `{doc_path}`"

    if field == "components":
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return "Error: components value must be a JSON object {key: {title, after}}"
        err = _validate_components(parsed)
        if err:
            return err
        epic["components"] = parsed
        _mutate_and_save(data)
        return f"Updated epic `{epic_id}` components ({len(parsed)} declared)"

    if field == "design_status":
        if value not in VALID_DESIGN_STATUSES:
            return f"Error: invalid design_status `{value}`. Valid: {', '.join(sorted(VALID_DESIGN_STATUSES))}"
        epic["design_status"] = value
        _mutate_and_save(data)
        return f"Updated epic `{epic_id}` design_status → `{value}`"

    old_value = epic.get(field, "")
    epic[field] = value
    _mutate_and_save(data)
    return f"Updated epic `{epic_id}` field `{field}`: `{old_value}` → `{value}`"


@mcp.tool()
def backlog_archive_epic(epic_id: str, reason: str = "done") -> str:
    """Archive an epic and all its tasks — hides the epic from the board and default listings.
    Cascades: every non-archived task in the epic is also archived with the same reason.

    Args:
        epic_id: The epic ID (e.g., "features", "infra")
        reason: Why the epic is being archived. One of: done, deprecated, duplicate, wont-fix, superseded. Default: done.
    """
    if reason not in VALID_ARCHIVE_REASONS:
        return f"Error: invalid reason `{reason}`. Valid: {', '.join(sorted(VALID_ARCHIVE_REASONS))}"

    data = _load()
    epic = _find_epic(data, epic_id)
    if not epic:
        return f"Error: epic `{epic_id}` not found"

    if epic.get("status") == "archived":
        return f"Error: epic `{epic_id}` is already archived"

    now = _now()
    epic["status"] = "archived"
    epic["archive_reason"] = reason
    epic["archived"] = now

    cascaded = 0
    for task in epic.get("tasks", []):
        if task.get("status") != "archived":
            task["status"] = "archived"
            task["archive_reason"] = reason
            task["archived"] = now
            task.pop("locked_by", None)
            cascaded += 1

    _mutate_and_save(data)
    return f"Archived epic `{epic_id}` — {epic.get('name', epic_id)} ({cascaded} tasks cascaded, reason: {reason})"


@mcp.tool()
def backlog_add_epic(
    epic_id: str, name: str, description: str = "", status: str = "planned",
) -> str:
    """Create a new epic. Epics group related tasks into workstreams.

    Args:
        epic_id: Short kebab-case identifier (e.g., "auth-system", "perf-opt"). Must be unique. Used as prefix for task IDs.
        name: Human-readable name (e.g., "Authentication System", "Performance Optimization")
        description: Brief description of the epic's scope and goals
        status: Initial status — one of: active, planned (default: planned)
    """
    if status not in VALID_EPIC_STATUSES:
        return f"Error: invalid status `{status}`. Valid: {', '.join(sorted(VALID_EPIC_STATUSES))}"

    # Validate epic_id format: lowercase, kebab-case
    if not epic_id or not all(c.isalnum() or c == "-" for c in epic_id) or epic_id != epic_id.lower():
        return f"Error: epic_id must be lowercase kebab-case (e.g., 'auth-system'), got `{epic_id}`"

    data = _load()

    # Check for duplicate
    if _find_epic(data, epic_id):
        return f"Error: epic `{epic_id}` already exists"

    new_epic = {
        "id": epic_id,
        "name": name,
        "status": status,
        "description": description,
        "created": _now(),
        "tasks": [],
    }

    data["epics"].append(new_epic)
    _mutate_and_save(data)
    return f"Created epic `{epic_id}` — {name} ({status})"


# ── Phase Tools ──────────────────────────────────────


VALID_PHASE_STATUSES = {"planned", "active", "done", "archived"}
ALLOWED_PHASE_FIELDS = {"name", "status", "description", "order", "target_date", "start_date", "deliverables", "docs"}


@mcp.tool()
def backlog_add_phase(
    phase_id: str, name: str, description: str = "", order: int | None = None,
    target_date: str = "", start_date: str = "",
) -> str:
    """Create a new phase. Phases are temporal attention scopes for sequential ordering, NOT feature
    groupings — features belong in epics. Only one phase is active at a time; tasks are assigned
    to phases to control focus.

    Args:
        phase_id: Short kebab-case identifier (e.g., "foundation", "mvp", "polish"). Must be unique. Avoid "p1"/"p2" — too similar to priority names.
        name: Human-readable name (e.g., "Foundation", "Core Features", "Polish & Launch")
        description: Brief description of the phase's goals
        order: Position in the sequence (1, 2, 3...). Auto-assigned if omitted.
        target_date: Optional target completion date (YYYY-MM-DD format)
        start_date: Optional start date (YYYY-MM-DD format). Auto-set to today if status is active and omitted.
    """
    # Validate ID format
    if not phase_id or not all(c.isalnum() or c == "-" for c in phase_id) or phase_id != phase_id.lower():
        return f"Error: phase_id must be lowercase kebab-case (e.g., 'foundation', 'mvp'), got `{phase_id}`"

    data = _load()

    # Exact ID match only — fuzzy matching would cause false positives
    if any(ph["id"] == phase_id for ph in data.get("phases", [])):
        return f"Error: phase `{phase_id}` already exists"

    if "phases" not in data:
        data["phases"] = []

    # Auto-assign order
    if order is None:
        existing_orders = [ph.get("order", 0) for ph in data["phases"]]
        order = max(existing_orders, default=0) + 1

    # Validate dates if provided
    if target_date and not _validate_date(target_date):
        return f"Error: target_date must be YYYY-MM-DD format, got `{target_date}`"
    if start_date and not _validate_date(start_date):
        return f"Error: start_date must be YYYY-MM-DD format, got `{start_date}`"

    # Auto-activate if this is the first phase
    status = "planned"
    if not any(ph.get("status") == "active" for ph in data["phases"]):
        status = "active"

    new_phase = {
        "id": phase_id,
        "name": name,
        "status": status,
        "description": description,
        "order": order,
        "created": _now(),
    }
    if target_date:
        new_phase["target_date"] = target_date
    if start_date:
        new_phase["start_date"] = start_date
    elif status == "active":
        new_phase["start_date"] = _today()

    data["phases"].append(new_phase)
    _mutate_and_save(data)

    status_note = f" (auto-activated — first phase)" if status == "active" else ""
    return f"Created phase `{phase_id}` — {name} (order: {order}){status_note}"


@mcp.tool()
def backlog_update_phase(phase_id: str, field: str, value: str) -> str:
    """Update a single field on a phase.

    Args:
        phase_id: The phase ID (e.g., "foundation", "mvp")
        field: Field to update — one of: name, status, description, order, target_date, start_date
        value: New value. For status: planned, active, done, archived. For order: integer. For dates: YYYY-MM-DD or empty to clear.
    """
    if field not in ALLOWED_PHASE_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(ALLOWED_PHASE_FIELDS))}"

    data = _load()
    ph = _find_phase(data, phase_id)
    if not ph:
        return f"Error: phase `{phase_id}` not found"

    if field == "status":
        if value not in VALID_PHASE_STATUSES:
            return f"Error: invalid status `{value}`. Valid: {', '.join(sorted(VALID_PHASE_STATUSES))}"
        # If activating, deactivate any currently active phase
        if value == "active":
            for other_ph in data.get("phases", []):
                if other_ph["id"] != phase_id and other_ph.get("status") == "active":
                    other_ph["status"] = "planned"
            if not ph.get("start_date"):
                ph["start_date"] = _today()
        if value == "done":
            ph["completed"] = _now()
        ph["status"] = value
    elif field == "order":
        try:
            ph["order"] = int(value)
        except ValueError:
            return f"Error: order must be an integer, got `{value}`"
    elif field in ("target_date", "start_date"):
        if value == "":
            ph.pop(field, None)
        else:
            if not _validate_date(value):
                return f"Error: {field} must be YYYY-MM-DD format, got `{value}`"
            ph[field] = value
    elif field == "deliverables":
        # value is a JSON string: {"action": "add"|"remove"|"toggle"|"set", ...}
        try:
            cmd = json.loads(value)
        except (ValueError, TypeError):
            return "Error: deliverables value must be JSON — {\"action\": \"add\", \"text\": \"...\"}"

        deliverables = ph.setdefault("deliverables", [])
        action = cmd.get("action", "")

        if action == "add":
            text = cmd.get("text", "").strip()
            if not text:
                return "Error: deliverable text is required"
            deliverables.append({"text": text, "done": False})
        elif action == "remove":
            idx = cmd.get("index")
            if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(deliverables):
                return f"Error: invalid index {idx} — phase has {len(deliverables)} deliverables"
            deliverables.pop(idx)
        elif action == "toggle":
            idx = cmd.get("index")
            if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(deliverables):
                return f"Error: invalid index {idx} — phase has {len(deliverables)} deliverables"
            deliverables[idx]["done"] = not deliverables[idx]["done"]
        elif action == "set":
            items = cmd.get("items", [])
            ph["deliverables"] = [{"text": str(d.get("text", "")), "done": bool(d.get("done", False))} for d in items]
        else:
            return f"Error: unknown deliverables action `{action}`. Use: add, remove, toggle, set"
    elif field == "docs":
        if ":" not in value:
            return (f"Error: docs value must be `key:path` format "
                    f"(e.g. `design:docs/design/ship.md`). Valid keys: {', '.join(sorted(VALID_DOC_KEYS))}")
        doc_key, doc_path = (s.strip() for s in value.split(":", 1))
        if doc_key not in VALID_DOC_KEYS:
            return f"Error: invalid docs key `{doc_key}`. Valid: {', '.join(sorted(VALID_DOC_KEYS))}"
        if not isinstance(ph.get("docs"), dict):
            ph["docs"] = {}
        if doc_path == "":
            ph["docs"].pop(doc_key, None)
            if not ph["docs"]:
                ph.pop("docs", None)
        else:
            ph["docs"][doc_key] = doc_path
    else:
        ph[field] = value

    _mutate_and_save(data)
    return f"Updated phase `{phase_id}` field `{field}` → {value}"


@mcp.tool()
def backlog_phase_status(phase_id: str = "") -> str:
    """Show detailed progress for a phase. Defaults to the active phase.

    Args:
        phase_id: Phase ID. If omitted, shows the active phase.
    """
    data = _load()

    if phase_id:
        ph = _find_phase(data, phase_id)
        if not ph:
            return f"Error: phase `{phase_id}` not found"
    else:
        ph = _active_phase(data)
        if not ph:
            return "No active phase. Create one with `backlog_add_phase`."

    stats = _phase_stats(data, ph["id"])

    # Get all phases sorted by order for sequential context
    all_phases = sorted(data.get("phases", []), key=lambda p: p.get("order", 999))
    current_idx = next((i for i, p in enumerate(all_phases) if p["id"] == ph["id"]), -1)
    prev_phase = all_phases[current_idx - 1] if current_idx > 0 else None
    next_phase = all_phases[current_idx + 1] if current_idx < len(all_phases) - 1 else None

    # Phase sequence header
    phase_num = current_idx + 1
    total_phases = len(all_phases)
    lines = [f"## Phase {phase_num}/{total_phases}: {ph['name']}\n"]

    if ph.get("description"):
        lines.append(f"{ph['description']}\n")

    # Previous/next phase context
    if prev_phase:
        prev_status = "completed" if prev_phase.get("status") == "done" else prev_phase.get("status", "planned")
        lines.append(f"**Previous:** {prev_phase['name']} ({prev_status})")
    if next_phase:
        lines.append(f"**Next up:** {next_phase['name']} — {next_phase.get('description', 'no description')}")
    if prev_phase or next_phase:
        lines.append("")

    # Retrospective for done phases
    if ph.get("status") == "done":
        lines.append("**Status:** Completed")
        # Duration
        if ph.get("start_date") and ph.get("completed"):
            try:
                start = datetime.strptime(str(ph["start_date"]), "%Y-%m-%d").date()
                comp_str = str(ph["completed"])
                comp = datetime.fromisoformat(comp_str).date() if "T" in comp_str else datetime.strptime(comp_str, "%Y-%m-%d").date()
                duration_days = (comp - start).days
                lines.append(f"**Duration:** {duration_days} days ({ph['start_date']} → {comp_str[:10]})")
            except ValueError:
                if ph.get("completed"):
                    lines.append(f"**Completed:** {str(ph['completed'])[:10]}")
        elif ph.get("completed"):
            lines.append(f"**Completed:** {str(ph['completed'])[:10]}")
        # On-time analysis
        if ph.get("target_date") and ph.get("completed"):
            try:
                target = datetime.strptime(str(ph["target_date"]), "%Y-%m-%d").date()
                comp_str = str(ph["completed"])
                comp = datetime.fromisoformat(comp_str).date() if "T" in comp_str else datetime.strptime(comp_str, "%Y-%m-%d").date()
                delta = (comp - target).days
                if delta < 0:
                    lines.append(f"**On-time:** Yes ({abs(delta)}d early)")
                elif delta == 0:
                    lines.append("**On-time:** Yes (exact)")
                else:
                    lines.append(f"**On-time:** No ({delta}d late)")
            except ValueError:
                pass
        # Count archived tasks as completed work
        total_completed = stats["done"] + stats["archived"]
        lines.append(f"**Tasks completed & archived:** {total_completed}")
        lines.append("")
    else:
        lines.append(f"**Status:** {ph['status']} | **Order:** {ph.get('order', '?')}")
        # Date info
        date_parts = []
        if ph.get("start_date"):
            date_parts.append(f"Started: {ph['start_date']}")
        if ph.get("target_date"):
            date_parts.append(f"Target: {ph['target_date']}")
            remaining = _time_remaining(ph["target_date"])
            if remaining:
                date_parts.append(f"**{remaining}**")
        if date_parts:
            lines.append(" | ".join(date_parts))
        lines.append(f"**Progress:** {stats['done']}/{stats['total']} done")
        if stats["total"] > 0:
            pct = int(stats["done"] / stats["total"] * 100)
            bar_filled = pct // 5
            bar_empty = 20 - bar_filled
            lines.append(f"[{'█' * bar_filled}{'░' * bar_empty}] {pct}%")
        lines.append("")

        # Breakdown by status
        lines.append(f"Done: {stats['done']} | In Progress: {stats['in-progress']} | In Review: {stats['in-review']} | Todo: {stats['todo']} | Blocked: {stats['blocked']}")
        lines.append("")

    # Deliverables checklist
    deliverables = ph.get("deliverables", [])
    if deliverables:
        lines.append("")
        lines.append("**Deliverables:**")
        for i, d in enumerate(deliverables):
            check = "x" if d.get("done") else " "
            lines.append(f"  - [{check}] {d['text']}")
        done_count = sum(1 for d in deliverables if d.get("done"))
        lines.append(f"  ({done_count}/{len(deliverables)} complete)")
        lines.append("")

    # List tasks in this phase grouped by status
    status_groups = ["in-progress", "in-review", "todo", "blocked", "done"]
    if ph.get("status") == "done":
        status_groups.append("archived")
    for status_group in status_groups:
        group_tasks = []
        for epic in data["epics"]:
            for t in epic.get("tasks", []):
                if t.get("phase") == ph["id"] and t.get("status") == status_group:
                    group_tasks.append((t, epic))

        if group_tasks:
            label = "Completed (Archived)" if status_group == "archived" else status_group.replace("-", " ").title()
            lines.append(f"**{label}:**")
            for t, epic in group_tasks:
                pri = t.get("priority", "medium")
                lines.append(f"- `{t['id']}` — {t['title']} ({pri}, {epic['id']})")
            lines.append("")

    # Unassigned tasks hint
    unassigned_count = 0
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t.get("status") not in ("done", "archived") and not t.get("phase"):
                unassigned_count += 1
    if unassigned_count:
        lines.append(f"*{unassigned_count} active tasks are not assigned to any phase.*")

    return "\n".join(lines)


def _epic_stats(data: dict, epic_id: str) -> dict:
    """Status counts for an epic's tasks.

    Unlike _phase_stats (which subtracts archived from total to give an
    active-scope denominator), this keeps archived tasks in `total` so that
    the `done + archived` numerator never exceeds it — epic-level lifetime
    progress, not "what's left to do".
    """
    stats = {"total": 0, "done": 0, "in-progress": 0, "in-review": 0,
             "todo": 0, "blocked": 0, "archived": 0}
    epic = _find_epic(data, epic_id)
    for t in (epic.get("tasks", []) if epic else []):
        st = t.get("status", "todo")
        stats["total"] += 1
        if st in stats:
            stats[st] += 1
    return stats


@mcp.tool()
def backlog_epic_status(epic_id: str) -> str:
    """Show progress for an epic: status counts, per-component rollup, and the
    design-maturity lock. Derived on read from the epic's tasks — no stored rollup.

    Args:
        epic_id: The epic ID (e.g. "asset-engine").
    """
    data = _load()
    epic = _find_epic(data, epic_id)
    if not epic:
        return f"Error: epic `{epic_id}` not found"

    stats = _epic_stats(data, epic_id)
    roll = _component_rollup(data, epic_id)
    lines = [f"## Epic `{epic_id}` — {epic.get('name', '')}\n"]

    design = epic.get("design_status", "exploring")
    lock = " (locked)" if design == "locked" else ""
    lines.append(f"**Design:** {design}{lock}")
    lines.append(f"**Status:** {epic.get('status', 'active')}")

    done = stats["done"] + stats["archived"]
    if stats["total"]:
        pct = int(done / stats["total"] * 100)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        lines.append(f"**Progress:** {done}/{stats['total']} done")
        lines.append(f"[{bar}] {pct}%")
    lines.append(
        f"Done: {stats['done']} | Archived: {stats['archived']} | "
        f"In Progress: {stats['in-progress']} | "
        f"In Review: {stats['in-review']} | Todo: {stats['todo']} | Blocked: {stats['blocked']}"
    )

    glyph = {"done": "█", "in-progress": "▨", "blocked": "✗", "todo": "□"}
    lines.append("\n**Components:**")
    for key, spec in (epic.get("components") or {}).items():
        b = roll.get(key, {})
        title = (spec or {}).get("title", key)
        lines.append(f"- {glyph.get(b.get('status'), '□')} {title} "
                     f"({b.get('done', 0)}/{b.get('total', 0)})")
    if roll.get("_unassigned", {}).get("total"):
        u = roll["_unassigned"]
        lines.append(f"- · unassigned ({u['done']}/{u['total']})")

    # Risk / attention surface (derived). Decision + failed-gate bubbling
    # is added in Spec A / when decisions gain an epic link (extension point).
    attention = []
    for t in epic.get("tasks", []):
        if t.get("status") == "blocked":
            why = t.get("blockers")
            attention.append(f"⏸ {t['id']} blocked" + (f": {why}" if why else ""))
        elif t.get("blockers"):
            attention.append(f"⚠ {t['id']}: {t['blockers']}")
    if attention:
        lines.append("\n**Attention:**")
        lines.extend(f"- {a}" for a in attention)

    return "\n".join(lines)


@mcp.tool()
def backlog_advance_phase(force: bool = False) -> str:
    """Complete the active phase and activate the next one in sequence.
    Archives all 'done' tasks in the completed phase. Activates the next 'planned' phase by order.
    Blocks if phase has unchecked deliverables unless force=True.

    Args:
        force: If True, advance even if deliverables are incomplete.
    """
    data = _load()
    active_ph = _active_phase(data)
    if not active_ph:
        return "No active phase to advance."

    ph_stats = _phase_stats(data, active_ph["id"])

    # Block if deliverables are incomplete (unless force=True)
    deliverables = active_ph.get("deliverables", [])
    unchecked = [d for d in deliverables if not d.get("done")]
    if unchecked and not force:
        items = "\n".join(f"  - [ ] {d['text']}" for d in unchecked)
        return (
            f"**Blocked:** {len(unchecked)} unchecked deliverable(s) in phase "
            f"**{active_ph['name']}**:\n{items}\n\n"
            f"Check them off with `backlog_update_phase(phase_id=\"{active_ph['id']}\", "
            f"field=\"deliverables\", value='{{\"action\":\"toggle\",\"index\":N}}')` "
            f"or advance with force=True."
        )

    # Warn if there are incomplete tasks
    incomplete = ph_stats["todo"] + ph_stats["in-progress"] + ph_stats["in-review"] + ph_stats["blocked"]
    warning = ""
    if incomplete > 0:
        warning = (
            f"\n\n**Warning:** {incomplete} tasks in this phase are not done "
            f"(todo: {ph_stats['todo']}, in-progress: {ph_stats['in-progress']}, "
            f"in-review: {ph_stats['in-review']}, blocked: {ph_stats['blocked']}). "
            f"They will remain in their current status but the phase will be marked done."
        )

    # Mark active phase as done
    active_ph["status"] = "done"
    active_ph["completed"] = _now()

    # Archive done tasks in this phase
    archived_count = 0
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t.get("phase") == active_ph["id"] and t.get("status") == "done":
                t["status"] = "archived"
                t["archive_reason"] = "done"
                t["archived"] = _now()
                archived_count += 1

    # Find and activate next planned phase by order
    planned = [ph for ph in data.get("phases", []) if ph.get("status") == "planned"]
    planned.sort(key=lambda m: m.get("order", 999))
    next_ph = planned[0] if planned else None

    if next_ph:
        next_ph["status"] = "active"
        if not next_ph.get("start_date"):
            next_ph["start_date"] = _today()

    _mutate_and_save(data)

    result = f"Completed phase **{active_ph['name']}** — archived {archived_count} done tasks."
    if active_ph.get("start_date"):
        try:
            start = datetime.strptime(str(active_ph["start_date"]), "%Y-%m-%d").date()
            duration = (date.today() - start).days
            result += f" Duration: {duration}d."
        except ValueError:
            pass
    if active_ph.get("target_date"):
        try:
            target = datetime.strptime(str(active_ph["target_date"]), "%Y-%m-%d").date()
            delta = (date.today() - target).days
            if delta <= 0:
                result += " Completed on time."
            else:
                result += f" Completed {delta}d past target."
        except ValueError:
            pass
    if next_ph:
        next_stats = _phase_stats(data, next_ph["id"])
        result += f"\n\nActivated next phase: **{next_ph['name']}** ({next_stats['total']} tasks, order: {next_ph.get('order', '?')})"
        if next_ph.get("description"):
            result += f"\n{next_ph['description']}"
    else:
        result += "\n\nNo more planned phases. Create one with `backlog_add_phase`."

    return result + warning


@mcp.tool()
def backlog_batch_update(operations: str) -> str:
    """Apply multiple task/epic updates in a single atomic operation. One load/save cycle.

    Use this instead of calling backlog_update_task repeatedly — it's faster and atomic.

    Args:
        operations: Newline-separated list of operations. Each line format:
            "update {task_id} {field} {value}" — update a task field
            "status {task_id} {new_status}" — shorthand for status changes
            "complete {task_id}" — mark task done (shorthand for status done)
            "archive {task_id} [reason]" — archive a task (default reason: done)
            "pick {task_id}" — set task to in-progress with started timestamp
            "update_epic {epic_id} {field} {value}" — update an epic field
    """
    data = _load()
    results: list[str] = []
    errors: list[str] = []
    changed = False

    for line in operations.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 3)  # split into max 4 parts
        if len(parts) < 2:
            errors.append(f"Skipped malformed line: `{line}`")
            continue

        op = parts[0].lower()

        if op == "update" and len(parts) >= 4:
            task_id, field, value = parts[1], parts[2], parts[3]
            if field not in ALLOWED_FIELDS:
                errors.append(f"`{task_id}`: field `{field}` not allowed")
                continue
            result = _find_task(data, task_id)
            if not result:
                errors.append(f"`{task_id}`: not found")
                continue
            task, epic = result
            # Apply field update using same logic as backlog_update_task
            if field == "status":
                if value not in VALID_STATUSES:
                    errors.append(f"`{task_id}`: invalid status `{value}`")
                    continue
                task["status"] = value
                if value == "in-progress" and not task.get("started"):
                    task["started"] = _now()
                elif value == "done" and not task.get("completed"):
                    task["completed"] = _now()
                if value not in ("in-progress",):
                    task.pop("locked_by", None)
            elif field == "priority":
                value = _normalize_priority(value)
                if value not in VALID_PRIORITIES:
                    errors.append(f"`{task_id}`: invalid priority `{value}`")
                    continue
                task["priority"] = value
            elif field == "docs":
                if ":" not in value:
                    errors.append(f"`{task_id}`: docs must be `key:path` format")
                    continue
                doc_key, doc_path = value.split(":", 1)
                if doc_key.strip() not in VALID_DOC_KEYS:
                    errors.append(f"`{task_id}`: invalid docs key `{doc_key.strip()}`")
                    continue
                if "docs" not in task or not isinstance(task.get("docs"), dict):
                    task["docs"] = {}
                task["docs"][doc_key.strip()] = doc_path.strip()
            elif field == "depends_on":
                dep_ids = [d.strip() for d in value.split(",") if d.strip()]
                bad = [d for d in dep_ids if not _find_task(data, d)]
                if bad:
                    errors.append(f"`{task_id}`: dependencies not found: {', '.join(bad)}")
                    continue
                task["depends_on"] = dep_ids
            elif field == "stage":
                try:
                    task["stage"] = int(value)
                except ValueError:
                    errors.append(f"`{task_id}`: stage must be integer")
                    continue
            elif field == "locked_by":
                if value == "" or value.lower() == "none":
                    task.pop("locked_by", None)
                else:
                    task["locked_by"] = value
            elif field == "phase":
                if value == "" or value.lower() == "none":
                    task.pop("phase", None)
                else:
                    if not _find_phase(data, value):
                        errors.append(f"`{task_id}`: phase `{value}` not found")
                        continue
                    task["phase"] = _find_phase(data, value)["id"]
            elif field == "lane":
                # I2: validate lane and recompute gate_state mirror, mirroring
                # backlog_update_task's lane branch exactly.
                if value not in _VALID_LANES:
                    errors.append(
                        f"`{task_id}`: invalid lane `{value}`. "
                        f"Valid: {', '.join(_VALID_LANES)}"
                    )
                    continue
                task["lane"] = value
                task["gate_state"] = _compute_gate_state(task)
            else:
                task[field] = value
            results.append(f"`{task_id}`.{field} → {value}")
            changed = True

        elif op == "status" and len(parts) >= 3:
            task_id, new_status = parts[1], parts[2]
            if new_status not in VALID_STATUSES:
                errors.append(f"`{task_id}`: invalid status `{new_status}`")
                continue
            result = _find_task(data, task_id)
            if not result:
                errors.append(f"`{task_id}`: not found")
                continue
            task, epic = result
            if new_status == "done":
                # Same lifecycle guard + close-gate as backlog_complete_task (B-049).
                cur_status = task.get("status", "todo")
                if cur_status not in ("in-progress", "in-review", "blocked"):
                    errors.append(f"`{task_id}`: cannot complete from `{cur_status}` (expected in-progress/in-review/blocked)")
                    continue
                open_bugs, _ = _open_bugs_for_task(_backlog_path(), task_id)
                if open_bugs:
                    errors.append(f"`{task_id}`: {len(open_bugs)} open bug(s) linked via found_in: {', '.join(open_bugs)}")
                    continue
                # I1: apply Spec-A completion gate (lane'd tasks must satisfy
                # all blocking review gates before reaching done).
                block = _completion_block_reason(task)
                if block:
                    errors.append(f"`{task_id}`: {block}")
                    continue
            task["status"] = new_status
            if new_status == "in-progress" and not task.get("started"):
                task["started"] = _now()
            elif new_status == "done":
                task["started"] = task.get("started") or _now()
                if not task.get("completed"):
                    task["completed"] = _now()
            if new_status not in ("in-progress",):
                task.pop("locked_by", None)
            results.append(f"`{task_id}` → {new_status}")
            changed = True

        elif op == "complete" and len(parts) >= 2:
            task_id = parts[1]
            result = _find_task(data, task_id)
            if not result:
                errors.append(f"`{task_id}`: not found")
                continue
            task, epic = result
            # Honor the lifecycle guard + close-gate as backlog_complete_task,
            # backfill `started` so duration analytics stay correct (B-049),
            # and apply the Spec-A completion gate so lane'd tasks with
            # outstanding review gates are rejected (I1).
            cur_status = task.get("status", "todo")
            if cur_status not in ("in-progress", "in-review", "blocked"):
                errors.append(f"`{task_id}`: cannot complete from `{cur_status}` (expected in-progress/in-review/blocked)")
                continue
            open_bugs, _ = _open_bugs_for_task(_backlog_path(), task_id)
            if open_bugs:
                errors.append(f"`{task_id}`: {len(open_bugs)} open bug(s) linked via found_in: {', '.join(open_bugs)}")
                continue
            # I1: apply Spec-A completion gate (lane'd tasks must satisfy
            # all blocking review gates before reaching done).
            block = _completion_block_reason(task)
            if block:
                errors.append(f"`{task_id}`: {block}")
                continue
            task["status"] = "done"
            task["started"] = task.get("started") or _now()
            if not task.get("completed"):
                task["completed"] = _now()
            task.pop("locked_by", None)
            results.append(f"`{task_id}` → done")
            changed = True

        elif op == "archive" and len(parts) >= 2:
            task_id = parts[1]
            reason = parts[2] if len(parts) > 2 else "done"
            result = _find_task(data, task_id)
            if not result:
                errors.append(f"`{task_id}`: not found")
                continue
            task, epic = result
            task["status"] = "archived"
            task["archive_reason"] = reason
            task.pop("locked_by", None)
            results.append(f"`{task_id}` → archived ({reason})")
            changed = True

        elif op == "pick" and len(parts) >= 2:
            task_id = parts[1]
            result = _find_task(data, task_id)
            if not result:
                errors.append(f"`{task_id}`: not found")
                continue
            task, epic = result
            task["status"] = "in-progress"
            if not task.get("started"):
                task["started"] = _now()
            results.append(f"`{task_id}` → in-progress")
            changed = True

        elif op == "update_epic" and len(parts) >= 4:
            epic_id, field, value = parts[1], parts[2], parts[3]
            if field not in ALLOWED_EPIC_FIELDS:
                errors.append(f"epic `{epic_id}`: field `{field}` not allowed")
                continue
            epic = _find_epic(data, epic_id)
            if not epic:
                errors.append(f"epic `{epic_id}`: not found")
                continue
            if field == "status" and value not in VALID_EPIC_STATUSES:
                errors.append(f"epic `{epic_id}`: invalid status `{value}`")
                continue
            epic[field] = value
            results.append(f"epic `{epic_id}`.{field} → {value}")
            changed = True

        else:
            errors.append(f"Unknown or malformed: `{line}`")

    if changed:
        _mutate_and_save(data)

    summary = f"**Batch update:** {len(results)} applied"
    if errors:
        summary += f", {len(errors)} errors"
    summary += "\n\n"

    if results:
        summary += "**Applied:**\n" + "\n".join(f"- {r}" for r in results) + "\n"
    if errors:
        summary += "\n**Errors:**\n" + "\n".join(f"- {e}" for e in errors) + "\n"

    return summary


@mcp.tool()
def backlog_batch_preview(operations: str) -> str:
    """Preview what a batch of task operations would do without writing to disk.

    Distinct from `backlog_snapshot` (which captures backlog state for `recap`
    diffing) — this is a planning aid for batch operations.

    Args:
        operations: Newline-separated list of operations to preview. Each line is:
            "complete {task_id}" — preview marking a task as done
            "archive {task_id}" — preview archiving a task
            "pick {task_id}" — preview picking a task
            "status {task_id} {new_status}" — preview a status change
    """
    data = _load()
    previews: list[str] = []

    for line in operations.strip().split("\n"):
        parts = line.strip().split()
        if len(parts) < 2:
            previews.append(f"- Skipped malformed line: `{line.strip()}`")
            continue

        op = parts[0].lower()
        task_id = parts[1]
        result = _find_task(data, task_id)

        if not result:
            previews.append(f"- `{task_id}`: NOT FOUND")
            continue

        task, epic = result
        current_status = task.get("status", "todo")

        if op == "complete":
            if current_status in ("in-progress", "in-review", "blocked"):
                previews.append(f"- `{task_id}` ({current_status} → done): {task['title']}")
            else:
                previews.append(f"- `{task_id}`: Cannot complete — currently `{current_status}`")

        elif op == "archive":
            if current_status in ("done", "blocked", "todo"):
                reason = parts[2] if len(parts) > 2 else ("done" if current_status == "done" else "deprecated")
                previews.append(f"- `{task_id}` ({current_status} → archived, reason: {reason}): {task['title']}")
            else:
                previews.append(f"- `{task_id}`: Cannot archive — currently `{current_status}`")

        elif op == "pick":
            if current_status in ("todo", "in-review"):
                previews.append(f"- `{task_id}` ({current_status} → in-progress): {task['title']}")
                # Check dependencies
                deps = task.get("depends_on", [])
                if isinstance(deps, str):
                    deps = [deps]
                unmet = [d for d in deps if _find_task(data, d) and _find_task(data, d)[0].get("status") != "done"]
                if unmet:
                    previews.append(f"  ⚠ Unmet dependencies: {', '.join(f'`{d}`' for d in unmet)}")
            elif current_status == "in-progress":
                previews.append(f"- `{task_id}`: Already in-progress (idempotent)")
            else:
                previews.append(f"- `{task_id}`: Cannot pick — currently `{current_status}`")

        elif op == "status":
            if len(parts) < 3:
                previews.append(f"- `{task_id}`: Missing target status for `status` operation")
                continue
            new_status = parts[2]
            if new_status not in VALID_STATUSES:
                previews.append(f"- `{task_id}`: Invalid status `{new_status}`")
            else:
                previews.append(f"- `{task_id}` ({current_status} → {new_status}): {task['title']}")

        else:
            previews.append(f"- Unknown operation `{op}` for `{task_id}`")

    header = f"**Dry-run preview** ({len(previews)} operations):\n"
    footer = "\n\n*No changes written. Use the actual tools to apply.*"
    return header + "\n".join(previews) + footer


# ── Blast Radius Analysis ───────────────────────────────────


@mcp.tool()
def backlog_blast_radius(task_id: str, mode: str = "predictive", depth_override: str = "") -> str:
    """Analyze the blast radius (impact footprint) of a task.

    Two modes:
    - predictive: metadata-only analysis for pick-task time. Fast, no code tracing.
    - evidence: code-level impact analysis for review-gate time. Traces imports, builds dependency graph.

    Args:
        task_id: The task ID to analyze (e.g., "auth-005")
        mode: Analysis mode — "predictive" or "evidence"
        depth_override: Optional depth override — "shallow" (0-1 hop) or "deep" (2 hops). Overrides the adaptive heuristic.
    """
    if mode not in ("predictive", "evidence"):
        return f"Error: mode must be 'predictive' or 'evidence', got '{mode}'"

    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"
    task, epic = result

    # Collect all tasks for overlap detection
    all_tasks: list[dict] = []
    for e in data.get("epics", []):
        all_tasks.extend(e.get("tasks", []))

    config = load_config(data.get("meta", {}))
    override = depth_override if depth_override in ("shallow", "deep") else None

    if mode == "predictive":
        analysis = analyze_predictive(task, all_tasks)
        return _format_predictive(analysis, task.get("priority", "medium"))
    else:
        # Resolve project root for code analysis
        sub_repo = task.get("sub_repo")
        worktree = task.get("worktree")
        if worktree:
            project_root = Path(worktree).resolve() if Path(worktree).is_absolute() else (ROOT / worktree).resolve()
        else:
            project_root = ROOT

        analysis = analyze_evidence(
            task=task,
            all_tasks=all_tasks,
            project_root=project_root,
            config=config,
            base_branch="main",
            depth_override=override,
        )
        return _format_evidence(analysis)


def _format_predictive(analysis: dict, priority: str) -> str:
    """Format predictive analysis results as markdown."""
    lines: list[str] = []

    anchored = analysis["anchored_areas"]
    overlaps = analysis["overlapping_tasks"]

    if priority in ("critical", "high"):
        lines.append("── Predicted Blast Radius ──────────────────────")
        if anchored:
            lines.append("**Anchored areas:**")
            for a in anchored:
                lines.append(f"  - `{a}`")
        else:
            lines.append("**Anchored areas:** None set")

        if overlaps:
            lines.append("")
            lines.append("**Related active work:**")
            for o in overlaps:
                paths = ", ".join(f"`{p}`" for p in o["shared_paths"][:3])
                lines.append(f"  - `{o['task_id']}` \"{o['title']}\" ({o['status']}) — shares {paths}")
        else:
            lines.append("")
            lines.append("**Related active work:** None detected")

        lines.append("────────────────────────────────────────────────")
    else:
        if overlaps:
            overlap_strs = [f"{o['task_id']} ({o['status']})" for o in overlaps[:3]]
            lines.append(f"Blast radius: Overlaps with {', '.join(overlap_strs)}")
        else:
            lines.append("Blast radius: No overlap with active tasks.")

    return "\n".join(lines)


def _format_evidence(analysis: dict) -> str:
    """Format evidence analysis results as markdown."""
    lines: list[str] = []
    stats = analysis["summary_stats"]

    # Summary line for gate table
    parts = []
    if stats["files_changed"]:
        parts.append(f"{stats['total_dependents']} dependents")
    if stats["overlap_count"]:
        parts.append(f"{stats['overlap_count']} overlapping task{'s' if stats['overlap_count'] != 1 else ''}")
    summary = ", ".join(parts) if parts else "no impact detected"
    lines.append(f"**Gate 4 summary:** {summary}")

    if not analysis["changed_files"]:
        lines.append("")
        lines.append("No changed files detected — nothing to analyze.")
        return "\n".join(lines)

    # Detailed report
    lines.append("")
    lines.append("── Blast Radius Report ─────────────────────────")

    # Changed files with fan-out
    lines.append(f"**Changed files ({stats['files_changed']}):**")
    for f in analysis["changed_files"]:
        fan = analysis["fan_out_scores"].get(f, 0)
        depth = analysis["depth_used"].get(f, 0)
        depth_label = {0: "leaf, no trace", 1: "1 hop", 2: "2 hops"}.get(depth, f"{depth} hops")
        lines.append(f"  `{f}` — {fan} dependents ({depth_label})")

    # Dependency graph (affected modules)
    if analysis["dependency_graph"]:
        lines.append("")
        lines.append("**Affected modules:**")
        dir_counts: dict[str, int] = {}
        for deps in analysis["dependency_graph"].values():
            for dep in deps:
                parent = str(Path(dep).parent)
                dir_counts[parent] = dir_counts.get(parent, 0) + 1
        for d, count in sorted(dir_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  - `{d}/` ({count} file{'s' if count != 1 else ''})")

    # Overlapping tasks
    overlaps = analysis["overlapping_tasks"]
    if overlaps:
        lines.append("")
        lines.append("**Overlapping tasks:**")
        for o in overlaps:
            is_in_progress = o["status"] == "in-progress"
            marker = "!!" if is_in_progress else "-"
            lines.append(f"  {marker} `{o['task_id']}` \"{o['title']}\" ({o['status']})")
            paths = ", ".join(f"`{p}`" for p in o["shared_paths"][:5])
            lines.append(f"    Shared paths: {paths}")
            if is_in_progress:
                lines.append(f"    **Risk: Both tasks modifying the same files concurrently**")

    if analysis["truncated"]:
        lines.append("")
        lines.append("*Note: File scan was truncated — results may be incomplete.*")

    lines.append("────────────────────────────────────────────────")

    return "\n".join(lines)


def _compute_recent_events(since_iso: str) -> list:
    """Synthesize a 'since you last looked' event stream from the backlog.

    Plan 4 stub: derive events from backlog state. Plan 5+ may swap in a
    persisted event log.
    Event shape: {kind, at, summary, ref?}
    Kinds: task_closed, task_moved, issue_opened, lesson_promoted, phase_advanced.
    """
    from datetime import datetime
    try:
        since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    except Exception as e:
        raise ValueError(f"invalid since: {e}")

    backlog = yaml.safe_load(_backlog_path().read_text(encoding="utf-8")) or {}  # existing helper from Plan 1
    events: list = []

    def _parse(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            return None

    for t in (backlog.get("tasks") or []):
        completed = _parse(t.get("completed"))
        if completed and completed >= since and t.get("status") in ("done", "completed"):
            events.append({
                "kind": "task_closed",
                "at": t["completed"],
                "summary": f"{t.get('id','')}: {t.get('title','')}",
                "ref": t.get("id"),
            })
        started = _parse(t.get("started"))
        if started and started >= since and t.get("status") in ("in-progress", "in_progress"):
            events.append({
                "kind": "task_moved",
                "at": t["started"],
                "summary": f"{t.get('id','')} → in progress",
                "ref": t.get("id"),
            })

    for ph in (backlog.get("phases") or []):
        advanced = _parse(ph.get("advanced_at") or ph.get("started"))
        if advanced and advanced >= since and ph.get("status") == "active":
            events.append({
                "kind": "phase_advanced",
                "at": ph.get("advanced_at") or ph.get("started"),
                "summary": f"phase {ph.get('id','')}: {ph.get('name','')}",
                "ref": ph.get("id"),
            })

    # Sort newest first, drop None ats.
    events = [e for e in events if e.get("at")]
    events.sort(key=lambda e: e["at"], reverse=True)
    return events


def _load_task_full(task_id: str) -> dict | None:
    """Merge backlog.yaml index entry with the per-task markdown file body.
    Returns None if the task id is not in the index.
    """
    import re
    import yaml

    backlog_path = _backlog_path()
    if not backlog_path.exists():
        return None
    backlog = yaml.safe_load(backlog_path.read_text(encoding="utf-8")) or {}
    tasks = backlog.get("tasks")
    if not isinstance(tasks, list):
        tasks = [
            {**t, "epic": t.get("epic", e.get("id"))}
            for e in (backlog.get("epics") or [])
            for t in (e.get("tasks") or [])
        ]
    index_entry = next((t for t in tasks if t.get("id") == task_id), None)
    if index_entry is None:
        return None

    out = dict(index_entry)
    out.setdefault("docs", {})
    out.setdefault("description", "")
    out.setdefault("notes", "")
    out.setdefault("review_instructions", "")
    out.setdefault("_body", "")

    md_path = backlog_path.parent / "tasks" / f"{task_id}.md"
    if md_path.exists():
        raw = md_path.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.DOTALL)
        if fm_match:
            try:
                fm = yaml.safe_load(fm_match.group(1)) or {}
            except Exception:
                fm = {}
            body = fm_match.group(2)
            for k in (*_HEAVY_FIELDS, "patchnote", "release",
                      "worktree", "spec_review", "locked_by"):
                if k in fm:
                    out[k] = fm[k]
        else:
            body = raw
        out["_body"] = body

        sections: dict[str, list[str]] = {}
        current: str | None = None
        for line in body.splitlines():
            m = re.match(r"^## +(.+?)\s*$", line)
            if m:
                current = m.group(1).strip().lower()
                sections[current] = []
                continue
            if current is not None:
                sections[current].append(line)
        for key in ("description", "notes", "specification", "plan",
                    "review instructions", "activity", "patchnote"):
            if key in sections:
                out_key = key.replace(" ", "_")
                out[out_key] = "\n".join(sections[key]).strip()
    return out


def _load_epic_full(epic_id: str) -> dict | None:
    """Epic with heavy fields (description/docs/components) merged from
    epics/<id>.md via load_v3, plus derived status counts, per-component
    rollup, a blocked/blockers attention list, and a slim task list.

    Returns None if the epic id is unknown. Mirrors _load_task_full but
    routes through _load() (which calls _load_v3) so heavy fields that
    /api/backlog strips are present here.
    """
    if not _backlog_path().exists():
        return None
    data = _load()
    epic = _find_epic(data, epic_id)
    if epic is None:
        return None

    out = {k: v for k, v in epic.items() if k != "tasks"}
    out.setdefault("description", "")
    out.setdefault("docs", {})
    out.setdefault("components", {})
    out.setdefault("design_status", "exploring")
    out["stats"] = _epic_stats(data, epic_id)
    out["component_rollup"] = _component_rollup(data, epic_id)

    attention = []
    for t in epic.get("tasks", []):
        if t.get("status") == "blocked":
            attention.append({"id": t.get("id"), "title": t.get("title"),
                              "blocked": True, "why": t.get("blockers", "")})
        elif t.get("blockers"):
            attention.append({"id": t.get("id"), "title": t.get("title"),
                              "blocked": False, "why": t.get("blockers")})
    out["attention"] = attention

    out["tasks"] = [
        {"id": t.get("id"), "title": t.get("title"),
         "status": t.get("status", "todo"), "component": t.get("component"),
         "priority": t.get("priority"), "phase": t.get("phase"),
         "design_change": t.get("design_change")}
        for t in epic.get("tasks", [])
    ]
    return out


def _load_related_for_task(task_id: str) -> dict | None:
    """Build the related-entities payload for a task: lessons (anchor-matched),
    handovers (task_ids), issues (task_ids), forward deps, reverse deps.
    Returns None if the task is unknown.
    """
    import fnmatch
    import re
    import yaml

    backlog_path = _backlog_path()
    if not backlog_path.exists():
        return None
    backlog = yaml.safe_load(backlog_path.read_text(encoding="utf-8")) or {}
    tasks = backlog.get("tasks")
    if not isinstance(tasks, list):
        tasks = [
            {**t, "epic": t.get("epic", e.get("id"))}
            for e in (backlog.get("epics") or [])
            for t in (e.get("tasks") or [])
        ]
    me = next((t for t in tasks if t.get("id") == task_id), None)
    if me is None:
        return None

    my_anchors = list(me.get("anchors") or [])

    def _read_fm(p: Path) -> tuple[dict, str]:
        raw = p.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.DOTALL)
        if not m:
            return {}, raw
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except Exception:
            fm = {}
        return fm, m.group(2)

    def _anchors_overlap(a: list, b: list) -> bool:
        for pat in a:
            for other in b:
                if fnmatch.fnmatch(other, pat) or fnmatch.fnmatch(pat, other):
                    return True
                if pat == other:
                    return True
        return False

    sidecar_root = backlog_path.parent

    lessons: list[dict] = []
    lessons_dir = sidecar_root / "lessons"
    if lessons_dir.is_dir():
        for f in sorted(lessons_dir.glob("*.md")):
            fm, body = _read_fm(f)
            their_anchors = list(fm.get("anchors") or [])
            if _anchors_overlap(my_anchors, their_anchors):
                lessons.append({
                    "id": fm.get("id") or f.stem,
                    "kind": fm.get("kind"),
                    "title": fm.get("title") or "",
                    "anchors": their_anchors,
                    "summary": body.strip().splitlines()[0] if body.strip() else "",
                    "_path": str(f),
                })

    handovers: list[dict] = []
    handovers_dir = sidecar_root / "handovers"
    if handovers_dir.is_dir():
        for f in sorted(handovers_dir.glob("*.md")):
            fm, body = _read_fm(f)
            tids = list(fm.get("task_ids") or [])
            if task_id in tids:
                handovers.append({
                    "id": fm.get("id") or f.stem,
                    "kind": fm.get("kind"),
                    "session": fm.get("session"),
                    "created": fm.get("created"),
                    "status": fm.get("status", "todo"),
                    "quote": body.strip().splitlines()[0] if body.strip() else "",
                    "_path": str(f),
                })

    issues: list[dict] = []
    issues_dir = sidecar_root / "issues"
    if issues_dir.is_dir():
        for f in sorted(issues_dir.glob("*.md")):
            fm, body = _read_fm(f)
            tids = list(fm.get("task_ids") or [])
            if task_id in tids:
                issues.append({
                    "id": fm.get("id") or f.stem,
                    "severity": fm.get("severity"),
                    "status": fm.get("status"),
                    "title": fm.get("title") or "",
                    "_path": str(f),
                })

    dep_ids = list(me.get("depends_on") or [])
    dependencies = [
        {"id": t["id"], "title": t.get("title", ""), "status": t.get("status", "")}
        for t in tasks if t.get("id") in dep_ids
    ]
    unblocks = [
        {"id": t["id"], "title": t.get("title", ""), "status": t.get("status", "")}
        for t in tasks if task_id in (t.get("depends_on") or [])
    ]

    return {
        "task_id": task_id,
        "lessons": lessons,
        "handovers": handovers,
        "issues": issues,
        "dependencies": dependencies,
        "unblocks": unblocks,
    }


# ── Project Structure helpers ────────────────────────────────────────────
#
# These power backlog_project_structure() and its HTTP companion.
# Helpers shell out to `git` via _run_git, which never raises on non-zero
# (treat git-feature-unavailable the same as data-missing).

import re as _re_ps  # local alias so we don't shadow uses elsewhere in this file

_INTEGRATION_BRANCH_RE = _re_ps.compile(
    r"^(?:origin/)?(master|main|stage|dev|work|\d+\.\d+\.\d+(?:\.\d+)?)$"
)


def _run_git(args: list[str], *, cwd: Path, timeout: float = 10.0) -> str:
    """Run `git <args>` in `cwd`. Return stdout text on success, '' on any failure.

    Failure is intentionally swallowed: this helper drives feature-detection
    logic (e.g. 'is X merged into Y'), where a non-zero exit means 'no' or
    'unknown', not 'crash the request'. The caller decides how to interpret ''.

    `--no-optional-locks` is mandatory: this helper is read-only, but commands
    like `git status` otherwise take `.git/index.lock` to refresh the stat-cache.
    On a slow repo the call can exceed `timeout`, and when Python kills the child
    on TimeoutExpired git leaves a 0-byte `index.lock` orphan behind that blocks
    every future commit until a human clears it. The flag stops git from taking
    that lock at all, killing the whole class of leak (see bug B-059).
    """
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", *args],
            cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _rank_integration_branch(name: str) -> tuple[int, tuple[int, ...]]:
    """Order: work < dev < stage < <version> < master|main.

    Returns a sort key suitable for `sorted(..., key=_rank_integration_branch)`.
    Tier index goes first; for version branches the parsed numeric tuple
    is the secondary key so 1.10.0 sorts above 1.3.1 (not lexicographic).
    """
    if name in ("master", "main"):
        return (4, ())
    if _re_ps.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", name):
        parts = tuple(int(p) for p in name.split("."))
        return (3, parts)
    tier = {"work": 0, "dev": 1, "stage": 2}.get(name)
    if tier is None:
        return (-1, ())  # unknown — sorts below everything
    return (tier, ())


def _discover_sub_repos(project_root: Path) -> list[dict]:
    """Return a list of sub-repo descriptors found under project_root.

    Two sources, merged by path (submodule wins over embedded for the kind tag):
      1. Filesystem scan for nested `.git` (file or dir) at depth 1 or 2.
      2. Parse of `.gitmodules` at project_root.

    Each descriptor is a dict with keys:
        path                 — POSIX-style path relative to project_root
        kind                 — 'embedded' | 'submodule'
        current_branch       — None at this stage (filled by tool)
        integration_branches — [] at this stage (filled by tool)
        submodule_info       — None at this stage (filled by tool when refresh_git)
        worktrees            — [] at this stage (filled by tool)
    """
    descriptors: dict[str, dict] = {}

    # 1. Filesystem scan — depth 1 and 2 only.
    if project_root.exists():
        for depth1 in project_root.iterdir():
            if not depth1.is_dir() or depth1.name == ".git":
                continue
            if (depth1 / ".git").exists():
                rel = depth1.name.replace("\\", "/")
                descriptors[rel] = {
                    "path": rel, "kind": "embedded",
                    "current_branch": None, "integration_branches": [],
                    "submodule_info": None, "worktrees": [],
                }
                continue
            # Depth 2 scan only if depth1 is itself NOT a repo.
            try:
                inner = list(depth1.iterdir())
            except OSError:
                continue
            for depth2 in inner:
                if not depth2.is_dir() or depth2.name == ".git":
                    continue
                if (depth2 / ".git").exists():
                    rel = f"{depth1.name}/{depth2.name}".replace("\\", "/")
                    descriptors[rel] = {
                        "path": rel, "kind": "embedded",
                        "current_branch": None, "integration_branches": [],
                        "submodule_info": None, "worktrees": [],
                    }

    # 2. .gitmodules parse — submodule entries take precedence on `kind`.
    gitmodules = project_root / ".gitmodules"
    if gitmodules.exists():
        cur_path: str | None = None
        for raw in gitmodules.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("[submodule"):
                cur_path = None
            elif line.startswith("path") and "=" in line:
                cur_path = line.split("=", 1)[1].strip().replace("\\", "/")
                existing = descriptors.get(cur_path)
                if existing is not None:
                    existing["kind"] = "submodule"
                else:
                    descriptors[cur_path] = {
                        "path": cur_path, "kind": "submodule",
                        "current_branch": None, "integration_branches": [],
                        "submodule_info": None, "worktrees": [],
                    }

    return sorted(descriptors.values(), key=lambda d: d["path"])


def _discover_integration_branches(repo_path: Path) -> list[str]:
    """Return integration branch names found in repo_path, ordered by rank.

    Matches names like `master`, `main`, `stage`, `dev`, `work`, or
    semver-ish `1.3.1` / `1.3.1.2`. Strips an `origin/` prefix before
    matching, then dedupes so a branch present locally and on origin
    appears once. Empty list if repo_path isn't a git repo.
    """
    raw = _run_git(["branch", "-a", "--format=%(refname:short)"], cwd=repo_path)
    if not raw:
        return []
    seen: set[str] = set()
    for line in raw.splitlines():
        name = line.strip()
        if not name:
            continue
        # Strip the `origin/` (or any single-segment remote) prefix for dedup.
        stripped = _re_ps.sub(r"^[^/]+/", "", name) if "/" in name else name
        candidate = stripped if _INTEGRATION_BRANCH_RE.match(stripped) else None
        if candidate is None and _INTEGRATION_BRANCH_RE.match(name):
            candidate = name
        if candidate:
            seen.add(candidate)
    return sorted(seen, key=_rank_integration_branch)


def _list_worktrees(repo_path: Path) -> list[dict]:
    """Parse `git worktree list --porcelain` into a list of dicts.

    Each dict has keys:
        path   — absolute filesystem path (str)
        branch — short branch name or None if detached HEAD
        head   — commit SHA

    Empty list if repo_path isn't a git repo.
    """
    raw = _run_git(["worktree", "list", "--porcelain"], cwd=repo_path)
    if not raw:
        return []
    worktrees: list[dict] = []
    cur: dict[str, object] = {}
    for line in raw.splitlines():
        if line.startswith("worktree "):
            if cur:
                worktrees.append(_finalize_worktree(cur))
            cur = {"path": line[len("worktree "):]}
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            # refs/heads/feature/foo → feature/foo
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            cur["branch"] = None
        # blank line = entry separator; handled by next 'worktree ' or final flush
    if cur:
        worktrees.append(_finalize_worktree(cur))
    return worktrees


def _finalize_worktree(d: dict) -> dict:
    return {
        "path": d.get("path", ""),
        "branch": d.get("branch"),  # None if detached
        "head": d.get("head", ""),
    }


def _compute_worktree_git_state(
    repo_path: Path,
    *,
    branch: str | None,
    integration_branches: list[str],
    worktree_path: Path,
    base: str | None = None,
) -> dict:
    """Compute merge ladder + ahead/behind + dirty-file count for one worktree.

    Args:
      repo_path: the sub-repo's main checkout (where branches live).
      branch: the worktree's branch (or None for detached HEAD).
      integration_branches: the rank-ordered list to test merge containment against.
      worktree_path: where the working tree actually lives (for dirty-file count).
      base: integration branch to use as the ahead/behind reference. If None,
            defaults to the highest-ranked integration branch found.

    Returns:
      {
        "merge_ladder": { branch_name: bool, ... },  # ordered like integration_branches
        "ahead": int, "behind": int, "dirty_files": int,
        "base": str | None,
      }
    """
    if branch is None:
        return {"merge_ladder": {}, "ahead": 0, "behind": 0,
                "dirty_files": 0, "base": None}

    ladder: dict[str, bool] = {}
    for int_branch in integration_branches:
        # `git merge-base --is-ancestor X Y` exits 0 if X is reachable from Y.
        # _run_git returns "" on any non-zero, including the "no" answer, so we
        # need the raw exit code here. Use subprocess directly for this one call.
        try:
            rc = subprocess.run(
                ["git", "merge-base", "--is-ancestor", branch, int_branch],
                cwd=str(repo_path), capture_output=True, text=True, timeout=10,
            ).returncode
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            rc = 1
        ladder[int_branch] = (rc == 0)

    # Pick base for ahead/behind.
    if base is None:
        base = integration_branches[-1] if integration_branches else None
    ahead = behind = 0
    if base:
        out = _run_git(
            ["rev-list", "--left-right", "--count", f"{base}...{branch}"],
            cwd=repo_path,
        ).strip()
        if out:
            try:
                left, right = out.split()
                behind, ahead = int(left), int(right)
            except ValueError:
                pass

    # Dirty file count from the actual worktree directory.
    status = _run_git(["status", "--porcelain"], cwd=worktree_path)
    dirty_files = sum(1 for line in status.splitlines() if line.strip())

    return {
        "merge_ladder": ladder,
        "ahead": ahead,
        "behind": behind,
        "dirty_files": dirty_files,
        "base": base,
    }


def _canonical_path(p: str) -> str:
    """POSIX-style, no trailing slash. Used for cross-platform path comparisons."""
    if not p:
        return ""
    return p.replace("\\", "/").rstrip("/")


def _link_worktrees_to_tasks(
    worktrees: list[dict], tasks: list[dict],
) -> dict[str, list[dict]]:
    """Bucket tasks under the worktree they belong to (by task.worktree field).

    Returns {worktree_path: [task, ...]}. Worktrees with no tasks get an
    empty list (the key is always present). Tasks with no worktree, or whose
    worktree doesn't match any known worktree, are silently dropped.
    """
    by_canonical: dict[str, str] = {
        _canonical_path(w["path"]): w["path"] for w in worktrees
    }
    out: dict[str, list[dict]] = {w["path"]: [] for w in worktrees}
    for t in tasks:
        tw = t.get("worktree")
        if not tw:
            continue
        key = by_canonical.get(_canonical_path(tw))
        if key is not None:
            out[key].append(t)
    return out


def _link_handovers_to_worktrees(
    worktree_task_map: dict[str, list[dict]], handovers: list[dict],
) -> dict[str, list[dict]]:
    """Bucket handovers under worktree via handover.task_ids[] → task → worktree.

    Returns {worktree_path: [handover, ...]}. A handover that references
    multiple tasks across multiple worktrees lands under each. Order is the
    input order of `handovers` (callers may sort).
    """
    # Build task_id → list of worktree paths.
    task_to_wts: dict[str, list[str]] = {}
    for wt_path, tasks in worktree_task_map.items():
        for t in tasks:
            task_to_wts.setdefault(t["id"], []).append(wt_path)

    out: dict[str, list[dict]] = {wt: [] for wt in worktree_task_map}
    for h in handovers:
        seen_wts: set[str] = set()
        for tid in h.get("task_ids", []) or []:
            for wt in task_to_wts.get(tid, []):
                if wt not in seen_wts:
                    out[wt].append(h)
                    seen_wts.add(wt)
    return out


# Cache keyed by (project_root_str, gitmodules_mtime, refresh_git). LRU sized
# at 4 — the cost of recomputing is the slow path (~seconds for 10 sub-repos
# with refresh_git=True), not memory.
from functools import lru_cache as _lru_cache_ps


@_lru_cache_ps(maxsize=4)
def _cached_project_structure(
    project_root_str: str,
    gitmodules_mtime: float,  # only part of the key — invalidates on change
    refresh_git: bool,
) -> str:
    """Cached worker. Returns a JSON string so the @lru_cache result is
    trivially immutable and safe to share across callers."""
    import json as _json
    from datetime import datetime, timezone

    project_root = Path(project_root_str)

    # Pull tasks + handovers from the live backlog so links reflect the
    # current state. Use the existing _load + handover discovery surface.
    try:
        data = _load()
        tasks: list[dict] = []
        for epic in data.get("epics", []):
            for t in epic.get("tasks", []):
                tasks.append(t)
    except Exception:
        tasks = []

    # Handovers: use the existing imports already at the top of this module
    # (_list_handover_ids + _read_handover). bp is the backlog.yaml path.
    handovers: list[dict] = []
    try:
        bp = _backlog_path()
        for hid in _list_handover_ids(bp):
            try:
                fm, _body = _read_handover(bp, hid)
                fm = dict(fm)  # don't mutate the caller's dict
                fm.setdefault("id", hid)
                handovers.append(fm)
            except Exception:
                continue
    except Exception:
        handovers = []

    sub_repos = _discover_sub_repos(project_root)

    for sr in sub_repos:
        repo_path = project_root / sr["path"]
        # Submodule whose working tree isn't checked out yet — skip git work.
        if not (repo_path / ".git").exists():
            sr["worktrees"] = []
            continue

        sr["integration_branches"] = _discover_integration_branches(repo_path)
        sr["current_branch"] = (
            _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path).strip()
            or None
        )

        wts = _list_worktrees(repo_path)
        wt_task_map = _link_worktrees_to_tasks(wts, tasks)
        wt_ho_map = _link_handovers_to_worktrees(wt_task_map, handovers)

        worktree_blocks: list[dict] = []
        for w in wts:
            block = {
                "path": w["path"],
                "branch": w["branch"],
                "git_state": None,
                "tasks": wt_task_map.get(w["path"], []),
                "handovers": wt_ho_map.get(w["path"], []),
            }
            if refresh_git:
                block["git_state"] = _compute_worktree_git_state(
                    repo_path,
                    branch=w["branch"],
                    integration_branches=sr["integration_branches"],
                    worktree_path=Path(w["path"]),
                )
            worktree_blocks.append(block)
        sr["worktrees"] = worktree_blocks

        # Submodule drift info — only when refresh_git.
        if sr["kind"] == "submodule" and refresh_git:
            pinned = _run_git(
                ["ls-tree", "HEAD", sr["path"]], cwd=project_root,
            ).strip()
            pinned_sha = pinned.split()[2] if len(pinned.split()) >= 3 else None
            ahead = behind = 0
            if pinned_sha:
                rl = _run_git(
                    ["rev-list", "--left-right", "--count",
                     f"{pinned_sha}...HEAD"],
                    cwd=repo_path,
                ).strip()
                if rl:
                    try:
                        l, r = rl.split()
                        behind, ahead = int(l), int(r)
                    except ValueError:
                        pass
            sr["submodule_info"] = {
                "pinned_sha": pinned_sha,
                "drift_ahead": ahead,
                "drift_behind": behind,
            }

    project_default_branch = (
        _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=project_root).strip()
        or "master"
    )
    payload = {
        "project": {
            "root": str(project_root),
            "default_branch": project_default_branch,
        },
        "sub_repos": sub_repos,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_state_included": refresh_git,
    }
    return _json.dumps(payload)


@mcp.tool()
def backlog_project_structure(refresh_git: bool = False) -> str:
    """Return the monorepo → sub-repos → worktrees → tasks + handovers tree.

    Cheap fields (paths, branch names, tasks, handovers) are always
    populated. `git_state` per worktree and `submodule_info.drift_*` are
    null unless `refresh_git=True`.

    Args:
        refresh_git: When True, runs `git merge-base`, `rev-list`, and
            `status` per worktree (slow — seconds per sub-repo). When
            False (default), returns cheap structural data only.
    """
    project_root = ROOT
    gitmodules = project_root / ".gitmodules"
    mtime = gitmodules.stat().st_mtime if gitmodules.exists() else 0.0
    return _cached_project_structure(str(project_root), mtime, refresh_git)


# ── HTTP Viewer Server ───────────────────────────────────


class ViewerHandler(BaseHTTPRequestHandler):
    """Serves the backlog viewer HTML and YAML data."""

    def do_GET(self) -> None:
        import re
        from urllib.parse import unquote, urlparse
        parsed = urlparse(self.path)
        clean_path = unquote(parsed.path)

        if clean_path in ("/", "/index.html"):
            try:
                prefs = load_viewer_prefs()
                if prefs.get("use_v3"):
                    self.path = "/v3"
                    return self.do_GET()
            except Exception:
                pass
            self._serve_file(VIEWER_PATH, "text/html")
        elif clean_path in ("/v3", "/v3/", "/v3/index.html"):
            viewer_root = Path(__file__).parent / "viewer"
            idx = viewer_root / "index.html"
            if not idx.exists():
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            html = idx.read_text(encoding="utf-8")
            # Make relative asset refs absolute under /static/v3/.
            html = html.replace('href="css/', 'href="/static/v3/css/')
            html = html.replace('src="js/', 'src="/static/v3/js/')
            html = html.replace('src="vendor/', 'src="/static/v3/vendor/')
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        elif clean_path.startswith("/static/v3/"):
            from urllib.parse import unquote as _unquote
            rel = _unquote(clean_path[len("/static/v3/"):])
            viewer_root = (Path(__file__).parent / "viewer").resolve()
            target = (viewer_root / rel).resolve()
            if not str(target).startswith(str(viewer_root) + os.sep) and target != viewer_root:
                self.send_response(400); self.send_header("Content-Length", "0"); self.end_headers(); return
            if not target.is_file():
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            ext = target.suffix.lower()
            ctype = {
                ".html":  "text/html; charset=utf-8",
                ".css":   "text/css; charset=utf-8",
                ".js":    "application/javascript; charset=utf-8",
                ".json":  "application/json; charset=utf-8",
                ".svg":   "image/svg+xml",
                ".woff2": "font/woff2",
                ".woff":  "font/woff",
                ".png":   "image/png",
                ".ico":   "image/x-icon",
            }.get(ext, "application/octet-stream")
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        elif clean_path == "/v3/dev/edit-demo" or clean_path == "/v3/dev/edit-demo/":
            viewer_root = Path(__file__).parent / "viewer"
            self._serve_file(viewer_root / "dev" / "edit-demo.html", "text/html")
        elif clean_path == "/api/auto/sessions":
            import json as _json
            from taskmaster_v3 import list_auto_sessions
            body = _json.dumps({"sessions": list_auto_sessions()}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        elif clean_path.startswith("/api/auto/sessions/"):
            import json as _json
            from taskmaster_v3 import load_auto_session
            sid = clean_path[len("/api/auto/sessions/"):]
            state = load_auto_session(sid)
            if state is None:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":false,"error":"not found"}')
                return
            state["hook_counts"] = _read_hook_events(sid)
            body = _json.dumps(state).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        elif clean_path == "/api/auto/state":
            import json as _json
            from taskmaster_v3 import list_auto_sessions
            sessions = list_auto_sessions()
            active = next(
                (s for s in sessions if s.get("cursor") is not None and not s.get("stopped")),
                None,
            )
            body = (
                _json.dumps(active).encode("utf-8") if active
                else b'{"running":false}'
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        elif clean_path.startswith("/api/auto/events"):
            from urllib.parse import urlsplit, parse_qs
            from taskmaster_v3 import read_auto_events
            qs = parse_qs(urlsplit(self.path).query)
            sid = (qs.get("sid") or [None])[0]
            since = (qs.get("since") or [None])[0]
            if not sid:
                self._send_json(400, {"ok": False, "error": "sid required"})
                return
            events = read_auto_events(sid, since=since)
            self._send_json(200, {"events": events})
            return
        elif clean_path.startswith("/api/auto/budget/"):
            from taskmaster_v3 import load_auto_session, compute_budget
            sid = clean_path[len("/api/auto/budget/"):]
            state = load_auto_session(sid)
            if state is None:
                self._send_json(404, {"ok": False, "error": "not found"})
                return
            self._send_json(200, {"session_id": sid, "meters": compute_budget(state)})
            return
        elif clean_path == "/api/viewer/prefs":
            self._send_json(200, load_viewer_prefs())
            return
        elif clean_path == "/backlog.yaml":
            self._serve_file(_backlog_path(), "text/yaml")
        elif clean_path.startswith("/api/task/"):
            rest = clean_path[len("/api/task/"):].rstrip("/")
            if rest.endswith("/related"):
                task_id = rest[: -len("/related")]
                related = _load_related_for_task(task_id)
                if related is None:
                    self._send_json(404, {"ok": False, "error": f"task {task_id} not found"})
                    return
                self._send_json(200, related)
                return
            if "/" not in rest and rest:
                full = _load_task_full(rest)
                if full is None:
                    self._send_json(404, {"ok": False, "error": f"task {rest} not found"})
                    return
                from taskmaster_v3 import compute_etag
                etag = compute_etag(_backlog_path())
                self._send_json(200, full, etag=etag)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        elif clean_path.startswith("/api/epic/"):
            eid = clean_path[len("/api/epic/"):].rstrip("/")
            if eid and "/" not in eid:
                full = _load_epic_full(eid)
                if full is None:
                    self._send_json(404, {"ok": False, "error": f"epic {eid} not found"})
                    return
                from taskmaster_v3 import compute_etag
                etag = compute_etag(_backlog_path())
                self._send_json(200, full, etag=etag)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        elif clean_path == "/api/backlog":
            self._serve_json()
        elif clean_path == "/api/session":
            self._serve_session()
        elif clean_path == "/api/identity":
            self._serve_identity()
        elif clean_path.startswith("/file/"):
            self._serve_repo_file(clean_path)
        elif self.path.startswith("/api/dashboard/recent-events"):
            import urllib.parse
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            since = (qs.get("since") or [None])[0]
            if not since:
                self._send_json(400, {"ok": False, "error": "missing 'since' query param"})
                return
            try:
                events = _compute_recent_events(since)
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            self._send_json(200, events)
            return
        elif clean_path == "/api/sessions":
            self._send_json(200, list_sessions())
            return
        elif clean_path.startswith("/api/sessions/"):
            sid = clean_path[len("/api/sessions/"):]
            detail = get_session_detail(sid)
            if detail is None:
                self._send_json(404, {"ok": False, "error": f"unknown session {sid}"})
                return
            self._send_json(200, detail)
            return
        elif clean_path == "/api/project-structure":
            import json as _json
            from urllib.parse import parse_qs, urlsplit
            qs = parse_qs(urlsplit(self.path).query)
            refresh_raw = (qs.get("refresh_git") or ["0"])[0]
            refresh_git = refresh_raw not in ("0", "false", "False", "")
            raw = backlog_project_structure(refresh_git=refresh_git)
            data = _json.loads(raw)
            self._send_json(200, data)
            return
        elif clean_path.startswith("/api/recap/"):
            sid = clean_path[len("/api/recap/"):]
            rec = load_recap(sid)
            if rec is None:
                self._send_json(404, {"ok": False, "error": f"no recap for {sid}"})
                return
            self._send_json(200, rec)
            return
        elif clean_path.startswith("/api/snapshots/diff"):
            from urllib.parse import urlsplit, parse_qs
            qs = parse_qs(urlsplit(self.path).query)
            a = (qs.get("from") or [None])[0]
            b = (qs.get("to")   or [None])[0]
            if not a or not b:
                self._send_json(400, {"ok": False,
                    "error": "both 'from' and 'to' query params required"})
                return
            snap_a = load_session_snapshot(a)
            snap_b = load_session_snapshot(b)
            if snap_a is None or snap_b is None:
                self._send_json(404, {"ok": False,
                    "error": f"missing snapshot(s): from={snap_a is not None} to={snap_b is not None}"})
                return
            self._send_json(200, _snapshot_diff(snap_a, snap_b))
            return
        elif clean_path == "/api/lessons":
            import json
            from taskmaster_v3 import (
                list_lesson_ids_cwd, load_lesson, compute_lesson_shelf,
            )
            prefs = load_viewer_prefs()
            thresholds = prefs.get("lessons", {}).get("thresholds", {})
            lessons = []
            for lid in list_lesson_ids_cwd():
                try:
                    lesson = load_lesson(lid)
                except Exception:
                    continue
                summary = {k: v for k, v in lesson.items() if k != "_body"}
                summary["shelf"] = compute_lesson_shelf(lesson, thresholds)
                summary["summary"] = (lesson.get("_body") or "").strip()
                lessons.append(summary)
            self._send_json(200, {"lessons": lessons})
            return
        elif clean_path.startswith("/api/bugs"):
            from urllib.parse import urlparse, parse_qs
            from taskmaster_v3 import (
                list_bug_ids as _list_bug_ids_http,
                read_bug as _read_bug_http,
            )
            bp = _backlog_path()
            qs = parse_qs(urlparse(self.path).query)
            include_archive = qs.get("include_archive", ["false"])[0].strip().lower() in ("1", "true", "yes", "on")
            status_filter = qs.get("status", [""])[0]
            found_in_filter = qs.get("found_in", [""])[0]
            bugs = []
            for bid in _list_bug_ids_http(bp, include_archive=include_archive):
                try:
                    fm, body = _read_bug_http(bp, bid)
                except Exception:
                    continue
                summary = {k: v for k, v in fm.items() if k != "_body"}
                summary["summary"] = body.strip()
                bugs.append(summary)
            if status_filter:
                bugs = [b for b in bugs if b.get("status") == status_filter]
            if found_in_filter:
                bugs = [b for b in bugs if b.get("found_in") == found_in_filter]
            self._send_json(200, bugs)
            return
        elif clean_path.startswith("/api/issues"):
            import json
            from urllib.parse import urlparse, parse_qs
            from taskmaster_v3 import (
                list_issue_ids_cwd, load_issue, compute_issue_aging, severity_label,
            )
            qs = parse_qs(urlparse(self.path).query)
            include_resolved = qs.get("include_resolved", ["true"])[0].lower() != "false"
            prefs = load_viewer_prefs()
            aging_cfg = prefs.get("issues", {}).get("aging", {})
            issues = []
            for iid in list_issue_ids_cwd():
                try:
                    issue = load_issue(iid)
                except Exception:
                    continue
                if not include_resolved and issue.get("status") in ("fixed", "wontfix"):
                    continue
                try:
                    summary = {k: v for k, v in issue.items() if k != "_body"}
                    summary["severity_label"] = severity_label(summary.get("severity", "P2"))
                    summary["aging"] = compute_issue_aging(issue, aging_cfg)
                    summary["summary"] = (issue.get("_body") or "").strip()
                    issues.append(summary)
                except Exception:
                    # One bad issue must not blank the whole screen. ISS-005.
                    continue
            self._send_json(200, {"issues": issues})
            return
        elif clean_path.startswith("/api/ideas"):
            from urllib.parse import urlparse, parse_qs
            from taskmaster_v3 import _resolve_artifact_root
            artifact_root = _resolve_artifact_root()
            bp = artifact_root / "backlog.yaml"
            if not bp.exists():
                self._send_json(200, {"ideas": []})
                return
            qs = parse_qs(urlparse(self.path).query)
            archived = qs.get("archived", ["false"])[0].lower() == "true"
            status = qs.get("status", [""])[0] or None
            tag = qs.get("tag", [""])[0] or None
            related_task = qs.get("related_task", [""])[0] or None
            # Default summary=False on HTTP so the viewer can render detail
            # without a second fetch. MCP callers via backlog_idea_list still
            # default to summary=True (they don't need every body in the list).
            summary = qs.get("summary", ["false"])[0].lower() == "true"
            try:
                limit = int(qs.get("limit", ["100"])[0])
            except (TypeError, ValueError):
                limit = 100
            entries = _list_ideas(
                bp,
                status=status,
                tag=tag,
                archived=archived,
                related_task=related_task,
                limit=max(1, limit),
                summary=summary,
            )
            self._send_json(200, {"ideas": entries})
            return
        elif clean_path == "/api/continuity":
            import json
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            include_auto = qs.get("include_auto_stage", ["0"])[0] in ("1", "true")
            payload = json.loads(backlog_continuity_items(include_auto_stage=include_auto))
            self._send_json(200, payload)
            return
        elif m := re.fullmatch(r"/api/decisions/([A-Za-z0-9_\-]+)", clean_path):
            decision_id = m.group(1)
            bp = _backlog_path()
            try:
                fm, body = _read_decision(bp, decision_id)
                self._send_json(200, {**fm, "body": body})
            except FileNotFoundError:
                self._send_json(404, {"ok": False, "error": f"decision {decision_id} not found"})
            return
        elif m := re.fullmatch(r"/api/handover/([A-Za-z0-9_\-]+)", clean_path):
            handover_id = m.group(1)
            bp = _backlog_path()
            try:
                fm, body = _read_handover(bp, handover_id)
                self._send_json(200, {**fm, "body": body})
            except FileNotFoundError:
                self._send_json(404, {"ok": False, "error": f"handover {handover_id} not found"})
            return
        elif clean_path == "/api/notes":
            from urllib.parse import urlparse, parse_qs
            from taskmaster_v3 import list_notes as _list_notes
            qs = parse_qs(urlparse(self.path).query)
            include_archived = qs.get("include_archived", ["0"])[0] in ("1", "true")
            bp = _backlog_path()
            notes = _list_notes(bp, include_archived=include_archived) if bp.exists() else []
            self._send_json(200, {"notes": notes})
            return
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _serve_file(self, path: Path, content_type: str) -> None:
        try:
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _serve_session(self) -> None:
        session_data = dict(_session_task) if _session_task else {}
        session_data["session_id"] = SESSION_ID
        self._send_json(200, session_data)

    def _serve_identity(self) -> None:
        """Return the project root and version so callers can verify which project this server serves."""
        self._send_json(200, {"root": str(ROOT.resolve()), "version": VERSION})

    def _serve_repo_file(self, clean_path: str) -> None:
        """Serve a file from the repo root. Renders .md files as styled HTML."""
        rel_path = clean_path[len("/file/"):]  # already unquoted
        # Security: prevent path traversal
        try:
            resolved = (ROOT / rel_path).resolve()
            if not str(resolved).startswith(str(ROOT.resolve())):
                self.send_error(HTTPStatus.FORBIDDEN, "Path traversal blocked")
                return
        except (ValueError, OSError):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return

        if not resolved.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, f"File not found: {rel_path}")
            return

        try:
            content = resolved.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if resolved.suffix.lower() == ".md":
            # Render markdown in a styled HTML page
            from html import escape
            import base64
            b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
            full_path = str(resolved).replace("\\", "/")
            html = _MD_TEMPLATE.replace("{{TITLE}}", escape(rel_path)).replace("{{B64CONTENT}}", b64).replace("{{FULL_PATH}}", full_path)
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            body = content.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            ext_map = {".yaml": "text/yaml", ".yml": "text/yaml", ".json": "application/json",
                       ".py": "text/plain", ".cpp": "text/plain", ".h": "text/plain",
                       ".cs": "text/plain", ".txt": "text/plain"}
            ct = ext_map.get(resolved.suffix.lower(), "text/plain")
            self.send_header("Content-Type", f"{ct}; charset=utf-8")

        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self) -> None:
        try:
            data = yaml.safe_load(_backlog_path().read_text(encoding="utf-8"))
            data.setdefault("meta", {})["_version"] = VERSION
            if not isinstance(data.get("tasks"), list):
                data["tasks"] = [
                    {**t, "epic": t.get("epic", e.get("id"))}
                    for e in (data.get("epics") or [])
                    for t in (e.get("tasks") or [])
                ]
            # Sort phases by order so the viewer always receives them in logical
            # sequence, regardless of YAML insertion order. Phases added out of
            # order (e.g. inserting "1.5" after "2" was written) would otherwise
            # appear in the wrong position in the phase stepper / board grouping.
            if isinstance(data.get("phases"), list):
                data["phases"] = sorted(
                    data["phases"],
                    key=lambda p: (p.get("order") if p.get("order") is not None else 999),
                )
            from taskmaster_v3 import compute_etag
            etag = compute_etag(_backlog_path())
            self._send_json(200, data, etag=etag)
        except Exception as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def do_POST(self):
        import json
        import re
        from taskmaster_v3 import lesson_reinforce as _reinforce

        if self.path == "/api/ideas":
            from taskmaster_v3 import _resolve_artifact_root
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            title = (payload.get("title") or "").strip()
            if not title:
                self._send_json(400, {"ok": False, "error": "title is required"})
                return
            artifact_root = _resolve_artifact_root()
            bp = artifact_root / "backlog.yaml"
            if not bp.exists():
                self._send_json(400, {"ok": False, "error": f"no backlog at {bp}"})
                return
            try:
                iid, target = _write_idea(
                    bp,
                    title=title,
                    body=payload.get("body", ""),
                    tags=payload.get("tags") or [],
                    status=payload.get("status", ""),
                    related_tasks=payload.get("related_tasks") or [],
                    related_issues=payload.get("related_issues") or [],
                    related_lessons=payload.get("related_lessons") or [],
                    created_by=payload.get("created_by", "user"),
                )
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            self._send_json(201, {"ok": True, "id": iid, "path": str(target)})
            return

        if self.path == "/api/notes":
            from taskmaster_v3 import write_note as _write_note
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            text = (payload.get("text") or "").strip()
            if not text:
                self._send_json(400, {"ok": False, "error": "text is required"})
                return
            bp = _backlog_path()
            if not bp.exists():
                self._send_json(400, {"ok": False, "error": f"no backlog at {bp}"})
                return
            try:
                nid, _target = _write_note(
                    bp, text=text, author="user",
                    pinned=bool(payload.get("pinned", False)),
                )
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            self._send_json(201, {"ok": True, "id": nid})
            return

        m = re.fullmatch(r"/api/notes/([A-Za-z0-9_\-]+)/(update|archive)", self.path)
        if m:
            from taskmaster_v3 import update_note as _update_note, archive_note as _archive_note
            note_id, action = m.group(1), m.group(2)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            bp = _backlog_path()
            try:
                if action == "archive":
                    _archive_note(bp, note_id)
                else:
                    text = payload.get("text")
                    pinned = payload.get("pinned")
                    _update_note(
                        bp, note_id,
                        text=(text.strip() if isinstance(text, str) and text.strip() else None),
                        pinned=(bool(pinned) if pinned is not None else None),
                    )
            except FileNotFoundError:
                self._send_json(404, {"ok": False, "error": f"note {note_id} not found"})
                return
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            self._send_json(200, {"ok": True, "id": note_id})
            return

        if self.path in ("/api/auto/pause", "/api/auto/stop"):
            from datetime import datetime, timezone
            from taskmaster_v3 import load_auto_session, save_auto_session, append_auto_event
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            sid = payload.get("session_id")
            if not sid:
                self._send_json(400, {"ok": False, "error": "session_id required"})
                return
            state = load_auto_session(sid)
            if state is None:
                self._send_json(404, {"ok": False, "error": "not found"})
                return
            kind = "control_pause" if self.path.endswith("/pause") else "control_stop"
            flag = "paused" if kind == "control_pause" else "stopped"
            state[flag] = True
            save_auto_session(sid, state)
            append_auto_event(sid, {
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "kind": kind, "stage": state.get("cursor", {}).get("stage"),
                "msg": f"{kind} via /api/auto",
            })
            self._send_json(200, {"ok": True})
            return

        m = re.fullmatch(r"/api/lessons/([A-Za-z0-9_\-]+)/reinforce", self.path)
        if m:
            lesson_id = m.group(1)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                data = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            source = data.get("source", "user")
            note = data.get("note", "")
            try:
                summary = _reinforce(lesson_id, source=source, note=note)
            except FileNotFoundError:
                self._send_json(404, {"ok": False, "error": f"lesson {lesson_id} not found"})
                return
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            self._send_json(200, summary)
            return

        m = re.fullmatch(r"/api/handover/([A-Za-z0-9_\-\.]+)/status", self.path)
        if m:
            handover_id = m.group(1)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            status = payload.get("status", "")
            reason = payload.get("reason", "")
            try:
                from taskmaster_v3 import update_handover_status as _update
                fm, _ = _update(_backlog_path(), handover_id=handover_id, status=status, reason=reason)
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            except FileNotFoundError:
                self._send_json(404, {"ok": False, "error": f"handover not found: {handover_id}"})
                return
            data = _load()
            _sync_handover_index(data, _backlog_path())
            _save(data)
            self._send_json(200, {"ok": True, "id": handover_id, "status": fm["status"]})
            return

        if self.path == "/api/tasks/validate":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw)
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            tid = payload.get("task_id") or "<new>"
            patch = payload.get("patch") or {}
            from taskmaster_v3 import validate_task_write
            errors = validate_task_write(tid, patch)
            self._send_json(200, {"ok": len(errors) == 0, "errors": errors})
            return

        # Edit-in-UI: create task, archive task
        if self.path == "/api/tasks":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw)
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            try:
                from taskmaster_v3 import validate_task_write, create_task
                errors = validate_task_write("<new>", payload)
                if errors:
                    self._send_json(422, {"ok": False, "errors": errors})
                    return
                new_id = create_task(payload)
                # Look up the new task to return.
                task = _load_task_full(new_id) or {"id": new_id}
                self._send_json(201, {"ok": True, "task": task})
            except (KeyError, ValueError) as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        m = re.fullmatch(r"/api/tasks/([A-Za-z0-9_\-]+)/archive", self.path)
        if m:
            task_id = m.group(1)
            # If-Match check
            from taskmaster_v3 import compute_etag, archive_task
            if_match = self.headers.get("If-Match")
            if if_match:
                if_match = if_match.strip('"')
                current_etag = compute_etag(_backlog_path())
                if if_match != current_etag:
                    current = _load_task_full(task_id)
                    self._send_json(409, {
                        "ok": False, "error": "stale",
                        "current_etag": current_etag,
                        "current": current,
                    })
                    return
            try:
                archive_task(task_id)
                new_etag = compute_etag(_backlog_path())
                self._send_json(200, {"ok": True}, etag=new_etag)
            except KeyError as e:
                self._send_json(404, {"ok": False, "error": str(e)})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        m = re.fullmatch(r"/api/decisions/([A-Za-z0-9_\-]+)/resolve", self.path)
        if m:
            decision_id = m.group(1)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            resolved_with = payload.get("resolved_with")
            rationale = payload.get("rationale", "")
            if resolved_with is None:
                self._send_json(400, {"ok": False, "error": "resolved_with is required"})
                return
            bp = _backlog_path()
            try:
                fm = _resolve_decision(bp, decision_id, resolved_with=int(resolved_with), rationale=rationale)
                self._send_json(200, {"ok": True, "id": decision_id, "status": fm.get("status")})
            except FileNotFoundError:
                self._send_json(404, {"ok": False, "error": f"decision {decision_id} not found"})
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        m = re.fullmatch(r"/api/decisions/([A-Za-z0-9_\-]+)/drop", self.path)
        if m:
            decision_id = m.group(1)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            reason = payload.get("reason", "")
            bp = _backlog_path()
            try:
                fm = _drop_decision(bp, decision_id, reason=reason)
                self._send_json(200, {"ok": True, "id": decision_id, "status": fm.get("status")})
            except FileNotFoundError:
                self._send_json(404, {"ok": False, "error": f"decision {decision_id} not found"})
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        # ── Bug HTTP routes ───────────────────────────────────────────────────────
        clean_path_post = self.path.split("?")[0].rstrip("/")

        if clean_path_post == "/api/bugs":
            from taskmaster_v3 import write_bug as _write_bug_http, sync_bug_index as _sync_bug_index_http
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            title = (payload.get("title") or "").strip()
            if not title:
                self._send_json(400, {"ok": False, "error": "title is required"})
                return
            bp = _backlog_path()
            if not bp.exists():
                self._send_json(400, {"ok": False, "error": f"no backlog at {bp}"})
                return
            try:
                bid, target = _write_bug_http(
                    bp,
                    title=title,
                    found_in=payload.get("found_in") or None,
                    discovered_by=payload.get("discovered_by", "user"),
                    severity=payload.get("severity") or None,
                    components=payload.get("components") or [],
                    location=payload.get("location") or [],
                    body=payload.get("body", ""),
                )
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            data = _load()
            _sync_bug_index_http(data, bp)
            _save(data)
            self._send_json(201, {"ok": True, "id": bid, "path": str(target)})
            return

        m = re.fullmatch(r"/api/bugs/pattern-scan", clean_path_post)
        if m:
            from taskmaster_v3 import scan_bug_patterns as _scan_bug_patterns_http
            bp = _backlog_path()
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            mode = payload.get("mode", "all")
            include_archive = (mode != "end_of_task")
            groups = _scan_bug_patterns_http(bp, include_archive=include_archive)
            self._send_json(200, {"groups": groups})
            return

        m = re.fullmatch(r"/api/bugs/promote", clean_path_post)
        if m:
            from taskmaster_v3 import (
                promote_bugs_to_issue as _promote_http,
                sync_bug_index as _sync_bug_index_http2,
                sync_issue_index as _sync_issue_index_http,
            )
            bp = _backlog_path()
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            bug_ids = payload.get("bug_ids") or []
            title = (payload.get("title") or "").strip()
            severity = (payload.get("severity") or "").strip()
            evidence_text = (payload.get("evidence_text") or "").strip()
            if not bug_ids:
                self._send_json(400, {"ok": False, "error": "bug_ids is required"})
                return
            if not title:
                self._send_json(400, {"ok": False, "error": "title is required"})
                return
            if not severity:
                self._send_json(400, {"ok": False, "error": "severity is required"})
                return
            if not evidence_text:
                self._send_json(400, {"ok": False, "error": "evidence_text is required"})
                return
            try:
                iid = _promote_http(
                    bp,
                    bug_ids=list(bug_ids),
                    title=title,
                    severity=severity,
                    evidence_text=evidence_text,
                    components=payload.get("components") or None,
                    body=payload.get("body", ""),
                )
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            data = _load()
            _sync_bug_index_http2(data, bp)
            _sync_issue_index_http(data, bp)
            _save(data)
            self._send_json(201, {"ok": True, "issue_id": iid})
            return

        m = re.fullmatch(r"/api/bugs/([A-Za-z0-9_\-]+)/archive", clean_path_post)
        if m:
            bug_id = m.group(1)
            from taskmaster_v3 import archive_bug as _archive_bug_http, sync_bug_index as _sync_bug_index_http3
            bp = _backlog_path()
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)  # consume body
            try:
                _archive_bug_http(bp, bug_id)
            except FileNotFoundError:
                self._send_json(404, {"ok": False, "error": f"bug {bug_id} not found"})
                return
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            data = _load()
            _sync_bug_index_http3(data, bp)
            _save(data)
            self._send_json(200, {"ok": True, "id": bug_id})
            return

        m = re.fullmatch(r"/api/bugs/([A-Za-z0-9_\-]+)", clean_path_post)
        if m:
            bug_id = m.group(1)
            from taskmaster_v3 import update_bug as _update_bug_http, sync_bug_index as _sync_bug_index_http4, BUG_STATUSES as _BUG_STATUSES_HTTP
            bp = _backlog_path()
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            updates = {}
            if "status" in payload:
                if payload["status"] not in _BUG_STATUSES_HTTP:
                    self._send_json(400, {"ok": False, "error": f"status must be one of {_BUG_STATUSES_HTTP}"})
                    return
                updates["status"] = payload["status"]
            for field in ("title", "severity", "fix_commit", "adopted_into", "promoted_to", "body"):
                if field in payload and payload[field]:
                    updates[field] = payload[field]
            for field in ("components", "location"):
                if field in payload and payload[field] is not None:
                    updates[field] = payload[field]
            try:
                fm, _ = _update_bug_http(bp, bug_id, **updates)
            except FileNotFoundError:
                self._send_json(404, {"ok": False, "error": f"bug {bug_id} not found"})
                return
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            data = _load()
            _sync_bug_index_http4(data, bp)
            _save(data)
            self._send_json(200, {"ok": True, "id": bug_id, "status": fm["status"]})
            return

        self.send_response(404)
        self.end_headers()

    def do_PUT(self):
        import re
        if self.path == "/api/viewer/prefs":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                patch = json.loads(raw)
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            if not isinstance(patch, dict):
                self._send_json(400, {"ok": False, "error": "patch must be a JSON object"})
                return

            prefs = load_viewer_prefs()
            _deep_merge(prefs, patch)
            save_viewer_prefs(prefs)
            self._send_json(200, {"ok": True})
            return

        if self.path.startswith("/api/recap/"):
            import json as _json
            sid = self.path[len("/api/recap/"):]
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = _json.loads(raw)
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            required = {"frontmatter", "title", "what_happened", "what_landed", "whats_next"}
            if not required.issubset(payload.keys()):
                self._send_json(400, {"ok": False,
                    "error": f"payload missing keys: {sorted(required - set(payload.keys()))}"})
                return
            save_recap(
                session_id=sid,
                frontmatter=payload["frontmatter"],
                title=payload["title"],
                what_happened=payload["what_happened"],
                what_landed=payload["what_landed"],
                whats_next=payload["whats_next"],
            )
            self._send_json(200, {"ok": True})
            return

        if m := re.fullmatch(r"/api/tasks/([A-Za-z0-9_\-]+)", self.path):
            task_id = m.group(1)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                full = json.loads(raw)
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            if not isinstance(full, dict):
                self._send_json(400, {"ok": False, "error": "body must be object"})
                return
            # If-Match check
            from taskmaster_v3 import compute_etag, update_task
            if_match = self.headers.get("If-Match")
            if if_match:
                if_match = if_match.strip('"')
                current_etag = compute_etag(_backlog_path())
                if if_match != current_etag:
                    current = _load_task_full(task_id)
                    self._send_json(409, {
                        "ok": False, "error": "stale",
                        "current_etag": current_etag,
                        "current": current,
                    })
                    return
            try:
                from taskmaster_v3 import validate_task_write, update_task
                errors = validate_task_write(task_id, full)
                if "_task" in errors:
                    self._send_json(404, {"ok": False, "error": errors["_task"]})
                    return
                if errors:
                    self._send_json(422, {"ok": False, "errors": errors})
                    return
                task = update_task(task_id, full)
                new_etag = compute_etag(_backlog_path())
                self._send_json(200, {"ok": True, "task": task}, etag=new_etag)
            except KeyError as e:
                self._send_json(404, {"ok": False, "error": str(e)})
            return

        self.send_response(404)
        self.end_headers()

    def do_PATCH(self):
        import json
        import re
        from taskmaster_v3 import compute_etag, update_task
        if m := re.fullmatch(r"/api/tasks/([A-Za-z0-9_\-]+)", self.path):
            task_id = m.group(1)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                patch = json.loads(raw)
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
                return
            if not isinstance(patch, dict):
                self._send_json(400, {"ok": False, "error": "patch must be object"})
                return
            # If-Match check
            if_match = self.headers.get("If-Match")
            if if_match:
                if_match = if_match.strip('"')
                current_etag = compute_etag(_backlog_path())
                if if_match != current_etag:
                    current = _load_task_full(task_id)
                    self._send_json(409, {
                        "ok": False, "error": "stale",
                        "current_etag": current_etag,
                        "current": current,
                    })
                    return
            try:
                from taskmaster_v3 import validate_task_write, update_task
                errors = validate_task_write(task_id, patch)
                if "_task" in errors:
                    self._send_json(404, {"ok": False, "error": errors["_task"]})
                    return
                if errors:
                    self._send_json(422, {"ok": False, "errors": errors})
                    return
                task = update_task(task_id, patch)
                new_etag = compute_etag(_backlog_path())
                self._send_json(200, {"ok": True, "task": task}, etag=new_etag)
            except KeyError as e:
                self._send_json(404, {"ok": False, "error": str(e)})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        self.send_error(404)

    def _send_json(self, status: int, payload: dict, etag: str | None = None):
        """Serialize *payload* as JSON and write the complete HTTP response."""
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if etag:
            self.send_header("ETag", f'"{etag}"')
        # JSON API responses are intentionally uncached — no Cache-Control header
        # means browsers apply their default heuristic (usually no-store for XHR).
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Allow cross-origin preflight for /api/* endpoints only."""
        from urllib.parse import urlparse
        clean_path = urlparse(self.path).path
        if not clean_path.startswith("/api/"):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass  # suppress HTTP logs in MCP stderr


_MD_TEMPLATE = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{TITLE}}</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#141920;color:#d4dae3;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:14px;line-height:1.6;padding:0}
.topbar{background:#1c222b;border-bottom:1px solid #363e4a;padding:10px 24px;display:flex;align-items:center;gap:10px;position:sticky;top:0;z-index:10}
.topbar a{color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600}
.topbar a:hover{text-decoration:underline}
.topbar .path{color:#97a0ad;font-family:"SFMono-Regular",Consolas,monospace;font-size:12px;flex:1}
.open-editor{background:#232a34;border:1px solid #363e4a;border-radius:4px;padding:4px 10px;font-size:12px !important;white-space:nowrap}
.open-editor:hover{background:#363e4a}
.zoom-controls{display:flex;align-items:center;gap:4px}
.zoom-btn{background:#232a34;border:1px solid #363e4a;border-radius:4px;padding:2px 8px;color:#d4dae3;cursor:pointer;font-size:13px;font-weight:600;line-height:1.4;min-width:26px;text-align:center}
.zoom-btn:hover{background:#363e4a}
.zoom-label{color:#97a0ad;font-size:11px;font-family:"SFMono-Regular",Consolas,monospace;min-width:36px;text-align:center}
.content{max-width:860px;margin:0 auto;padding:32px 24px;transition:font-size 0.15s ease}
h1,h2,h3,h4{color:#d4dae3;margin:20px 0 10px;line-height:1.3}
h1{font-size:1.7em;border-bottom:1px solid #363e4a;padding-bottom:8px}
h2{font-size:1.4em;border-bottom:1px solid #363e4a;padding-bottom:6px}
h3{font-size:1.15em}h4{font-size:1em;color:#97a0ad}
p{margin:8px 0}
a{color:#58a6ff}
code{font-family:"SFMono-Regular",Consolas,monospace;font-size:0.85em;background:#232a34;border:1px solid #363e4a;padding:1px 5px;border-radius:3px;color:#58a6ff}
pre{background:#0d1117;border:1px solid #363e4a;border-radius:6px;padding:14px 18px;overflow-x:auto;margin:12px 0}
pre code{background:none;border:none;padding:0;color:#d4dae3}
ul,ol{margin:8px 0;padding-left:22px}
li{margin:3px 0}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:0.93em}
th{text-align:left;padding:8px 12px;background:#232a34;border:1px solid #363e4a;font-weight:600}
td{padding:8px 12px;border:1px solid #363e4a}
tr:hover td{background:#1c222b}
blockquote{border-left:3px solid #58a6ff;padding:6px 14px;margin:10px 0;color:#97a0ad;background:#1c222b;border-radius:0 4px 4px 0}
hr{border:none;border-top:1px solid #363e4a;margin:20px 0}
strong{color:#d4dae3}
img{max-width:100%}
</style>
</head><body>
<div class="topbar">
  <a href="/">&larr; Backlog</a>
  <span class="path">{{TITLE}}</span>
  <div class="zoom-controls">
    <button class="zoom-btn" id="zoom-out" title="Zoom out">&minus;</button>
    <span class="zoom-label" id="zoom-label">100%</span>
    <button class="zoom-btn" id="zoom-in" title="Zoom in">+</button>
    <button class="zoom-btn" id="zoom-reset" title="Reset zoom">&#x21bb;</button>
  </div>
  <a href="vscode://file/{{FULL_PATH}}" class="open-editor">&#x1F4DD; Open in VSCode</a>
</div>
<div class="content" id="content"></div>
<script>
const raw = decodeURIComponent(atob("{{B64CONTENT}}").split('').map(c=>'%'+('00'+c.charCodeAt(0).toString(16)).slice(-2)).join(''));
document.getElementById('content').innerHTML = marked.parse(raw);

// Zoom
const ZOOM_KEY = 'taskmaster-docs-zoom';
const ZOOM_STEP = 10;
const ZOOM_MIN = 60;
const ZOOM_MAX = 200;
const BASE_SIZE = 14;
let zoomPct = parseInt(localStorage.getItem(ZOOM_KEY) || '100', 10);

function applyZoom() {
  zoomPct = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, zoomPct));
  document.getElementById('content').style.fontSize = (BASE_SIZE * zoomPct / 100) + 'px';
  document.getElementById('zoom-label').textContent = zoomPct + '%';
  localStorage.setItem(ZOOM_KEY, String(zoomPct));
}

document.getElementById('zoom-in').addEventListener('click', () => { zoomPct += ZOOM_STEP; applyZoom(); });
document.getElementById('zoom-out').addEventListener('click', () => { zoomPct -= ZOOM_STEP; applyZoom(); });
document.getElementById('zoom-reset').addEventListener('click', () => { zoomPct = 100; applyZoom(); });
applyZoom();
</script>
</body></html>"""

_viewer_started = False
VIEWER_PORT = 0


def _project_port() -> int:
    """Deterministic port per project root, in range 6800–6899."""
    h = hashlib.md5(str(ROOT.resolve()).encode()).hexdigest()
    return 6800 + int(h, 16) % 100


def _check_identity(port: int) -> bool:
    """Check if an existing server on `port` belongs to this project."""
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/identity", timeout=1)
        data = json.loads(resp.read())
        return Path(data.get("root", "")).resolve() == ROOT.resolve()
    except Exception:
        return False


def _make_server(host: str = "127.0.0.1", port: int = 0):
    """Build the HTTP server without starting it. Returns (server, bound_port)."""
    server = ThreadingHTTPServer((host, port), ViewerHandler)
    return server, server.server_address[1]


def _init_storage() -> None:
    """One-time storage migrations / dir setup. Called by server entry + tests."""
    from taskmaster_v3 import migrate_auto_state_to_sessions, AUTO_SESSIONS_DIR
    AUTO_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    migrate_auto_state_to_sessions()


def _start_viewer_server() -> int:
    """Start the viewer HTTP server on a deterministic per-project port.

    If another session for the same project already owns the port, reuse it.
    If a different project owns it, fall back to an OS-assigned port.
    """
    global _viewer_started, VIEWER_PORT
    if _viewer_started:
        return VIEWER_PORT

    target_port = _project_port()

    # Disable SO_REUSEADDR — on Windows, it allows multiple processes to bind
    # the same port, causing requests to route to the wrong session's server.
    class _ExclusiveServer(ThreadingHTTPServer):
        allow_reuse_address = False

    try:
        server = _ExclusiveServer(("127.0.0.1", target_port), ViewerHandler)
        VIEWER_PORT = target_port
    except OSError:
        # Port taken — check if the existing server serves the same project
        if _check_identity(target_port):
            # Same project, another session already runs the server — reuse it
            _viewer_started = True
            VIEWER_PORT = target_port
            return VIEWER_PORT
        # Different project owns this port — use a random free port
        server = _ExclusiveServer(("127.0.0.1", 0), ViewerHandler)
        VIEWER_PORT = server.server_address[1]

    _init_storage()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _viewer_started = True
    return VIEWER_PORT


# Start on import so the viewer is always available
_start_viewer_server()


@mcp.tool()
def backlog_open_viewer() -> str:
    """Open the backlog kanban board in the default browser. The viewer auto-loads current data."""
    port = _start_viewer_server()
    url = f"http://127.0.0.1:{port}/"
    webbrowser.open(url)
    return f"Opened backlog viewer at {url}"


@mcp.tool()
def recap_get(session_id: str) -> str:
    """Return the recap for a session as JSON, or `null` when missing."""
    import json as _json
    rec = load_recap(session_id)
    return _json.dumps(rec)


@mcp.tool()
def recap_set(
    session_id: str,
    frontmatter_json: str,
    title: str,
    what_happened: str,
    what_landed: str,
    whats_next: str,
) -> str:
    """Write a recap. `frontmatter_json` is a JSON object holding
    snapshot_before / snapshot_after / generator / generated_at / token_cost.
    `session_id` and `schema_version` are auto-injected.
    """
    import json as _json
    try:
        fm = _json.loads(frontmatter_json)
    except Exception as e:
        return f"error: invalid frontmatter JSON ({e})"
    if not isinstance(fm, dict):
        return "error: frontmatter must be a JSON object"
    save_recap(
        session_id=session_id,
        frontmatter=fm,
        title=title,
        what_happened=what_happened,
        what_landed=what_landed,
        whats_next=whats_next,
    )
    return "ok"


@mcp.tool()
def recap_list() -> str:
    """List session ids that have a recap on disk (newest first)."""
    import json as _json
    return _json.dumps(list_recaps())


@mcp.tool()
def snapshot_diff(snapshot_a_json: str, snapshot_b_json: str) -> str:
    """Compute structured diff between two snapshot payloads, returned as JSON."""
    import json as _json
    a = _json.loads(snapshot_a_json)
    b = _json.loads(snapshot_b_json)
    return _json.dumps(_snapshot_diff(a, b))


@mcp.tool()
def lesson_list_extended() -> str:
    """List all lessons with computed shelf placement using current viewer thresholds."""
    import json as _json
    from taskmaster_v3 import (
        list_lesson_ids_cwd, load_lesson, compute_lesson_shelf, load_viewer_prefs,
    )

    prefs = load_viewer_prefs()
    thresholds = prefs.get("lessons", {}).get("thresholds", {})
    out = []
    for lid in list_lesson_ids_cwd():
        try:
            lesson = load_lesson(lid)
        except Exception:
            continue
        summary = {k: v for k, v in lesson.items() if k != "_body"}
        summary["shelf"] = compute_lesson_shelf(lesson, thresholds)
        summary["summary"] = (lesson.get("_body") or "").strip()
        out.append(summary)
    return _json.dumps({"lessons": out}, indent=2, default=str)


@mcp.tool()
def issue_list_extended(include_resolved: bool = True) -> str:
    """List all issues with computed aging tier per severity base."""
    import json as _json
    from taskmaster_v3 import (
        list_issue_ids_cwd, load_issue, compute_issue_aging, severity_label, load_viewer_prefs,
    )

    prefs = load_viewer_prefs()
    aging_cfg = prefs.get("issues", {}).get("aging", {})
    out = []
    for iid in list_issue_ids_cwd():
        try:
            issue = load_issue(iid)
        except Exception:
            continue
        if not include_resolved and issue.get("status") in ("fixed", "wontfix"):
            continue
        try:
            summary = {k: v for k, v in issue.items() if k != "_body"}
            summary["severity_label"] = severity_label(summary.get("severity", "P2"))
            summary["aging"] = compute_issue_aging(issue, aging_cfg)
            summary["summary"] = (issue.get("_body") or "").strip()
            out.append(summary)
        except Exception:
            # One bad issue must not blank the whole list. ISS-005.
            continue
    return _json.dumps({"issues": out}, indent=2, default=str)


@mcp.tool()
def auto_state_get() -> str:
    """Return the most-recent auto-mode session state as JSON. {} if none running."""
    import json
    from taskmaster_v3 import list_auto_sessions
    sessions = list_auto_sessions()
    return json.dumps(sessions[0] if sessions else {})


@mcp.tool()
def auto_pause(session_id: str) -> str:
    """Mark a running auto-mode session as paused. Returns 'ok' or 'error: ...'."""
    from datetime import datetime, timezone
    from taskmaster_v3 import load_auto_session, save_auto_session, append_auto_event
    state = load_auto_session(session_id)
    if state is None:
        return f"error: session {session_id} not found"
    state["paused"] = True
    save_auto_session(session_id, state)
    append_auto_event(session_id, {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "control_pause",
        "stage": state.get("cursor", {}).get("stage"),
        "msg": "paused via MCP",
    })
    return "ok"


@mcp.tool()
def auto_stop(session_id: str) -> str:
    """Mark a running auto-mode session as stopped. Returns 'ok' or 'error: ...'."""
    from datetime import datetime, timezone
    from taskmaster_v3 import load_auto_session, save_auto_session, append_auto_event
    state = load_auto_session(session_id)
    if state is None:
        return f"error: session {session_id} not found"
    state["stopped"] = True
    save_auto_session(session_id, state)
    append_auto_event(session_id, {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "control_stop",
        "stage": state.get("cursor", {}).get("stage"),
        "msg": "stopped via MCP",
    })
    return "ok"


@mcp.tool()
def auto_history(limit: int = 50) -> str:
    """Return recent auto-mode sessions as JSON, newest first."""
    import json
    from taskmaster_v3 import list_auto_sessions
    sessions = list_auto_sessions()[: max(1, int(limit))]
    return json.dumps({"sessions": sessions}, indent=2)


@mcp.tool()
def auto_event_log(session_id: str, since: str | None = None) -> str:
    """Return events for a session, optionally filtered by ISO 8601 timestamp."""
    import json
    from taskmaster_v3 import read_auto_events
    return json.dumps({"events": read_auto_events(session_id, since=since)}, indent=2)


# --- .taskmaster/project.yaml (Project manifest) ---

from dataclasses import asdict
from project import (
    ProjectManifest,
    SCHEMA_VERSION,
    load_project_manifest,
    load_project_manifest_raw,
    manifest_to_dict,
    project_yaml_path,
    resolve_project_root,
    validate_manifest_dict,
)

_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _project_root_or_cwd() -> Path:
    """Resolve a project root from ROOT (the module-level cwd anchor) or fall back."""
    root = resolve_project_root(ROOT)
    return root if root is not None else ROOT


def _dig(data: Any, path: str) -> Any:
    if not path.strip():
        return None
    cursor: Any = data
    for match in _PATH_TOKEN.finditer(path):
        key, idx = match.group(1), match.group(2)
        try:
            if key is not None:
                cursor = cursor[key]
            else:
                cursor = cursor[int(idx)]
        except (KeyError, IndexError, TypeError):
            return None
    return cursor


@mcp.tool()
def backlog_project_get() -> dict | None:
    """Return the full .taskmaster/project.yaml as a dict, or None if missing/invalid.

    The returned dict is the EXPANDED form — every dataclass default is filled
    in (e.g. `project.goal == ""` even when the YAML didn't set it). For "is
    this field absent in the source?" queries, use `backlog_project_get_field`
    which reads the raw YAML.
    """
    m = load_project_manifest(_project_root_or_cwd())
    return manifest_to_dict(m) if m is not None else None


@mcp.tool()
def backlog_project_get_field(path: str) -> Any:
    """Read a single field via dotted/indexed path from the RAW YAML.

    Unlike `backlog_project_get`, this reads the source file directly without
    coercing through dataclasses — so absent fields return None rather than
    their schema defaults. Examples:
        "meta.name"
        "repos[0].name"
        "repos[0].branches.protected[0]"

    Returns None if any segment is missing or out of range.
    """
    data = load_project_manifest_raw(_project_root_or_cwd())
    if data is None:
        return None
    return _dig(data, path)


@mcp.tool()
def backlog_project_ship_order() -> list[str]:
    """Return repos in topological dependency order. Empty list if no manifest.

    Raises ValueError if depends_on contains a cycle (caught by validator on load).
    """
    m = load_project_manifest(_project_root_or_cwd())
    return m.ship_order() if m is not None else []


@mcp.tool()
def backlog_project_set(yaml_content: str) -> str:
    """Write .taskmaster/project.yaml with strict validation.

    Raises ValueError if YAML is malformed or schema invalid. Atomic write
    using the existing _atomic_write helper. Returns the absolute path written.
    """
    try:
        data = yaml.safe_load(yaml_content) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML parse failed: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("project.yaml top-level must be a mapping")
    validate_manifest_dict(data, raise_on_error=True)

    root = _project_root_or_cwd()
    path = project_yaml_path(root)
    _ensure_taskmaster_dir(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    _atomic_write(path, rendered)
    return str(path)


def _ensure_taskmaster_dir(path: Path) -> None:
    """Raise ValueError if path.parent exists but is not a directory."""
    if path.parent.exists() and not path.parent.is_dir():
        raise ValueError(f"{path.parent} exists but is not a directory")


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "project"


@mcp.tool()
def backlog_project_init(name: str, slug: str = "") -> str:
    """Scaffold a minimal valid project.yaml. Refuses to overwrite.

    Returns a confirmation message including the path written.
    """
    if not name or not name.strip():
        raise ValueError("name: required (project name must be non-empty)")
    root = _project_root_or_cwd()
    path = project_yaml_path(root)
    if path.exists():
        raise ValueError(f"{path} exists — refusing to overwrite (edit it directly)")
    slug = slug or _slugify(name)
    scaffold = {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": name, "slug": slug, "kind": "app"},
        "project": {"description": "", "goal": "", "owners": [], "tags": []},
        "repos": [],
        "submodules": [],
        "integrations": {"observability": {"error_trace_ladder": []}, "external": []},
        "conventions": {"narrative_ref": "./CLAUDE.md", "policies": {}},
        "extensions": {},
    }
    _ensure_taskmaster_dir(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, errs = validate_manifest_dict(scaffold)
    if not ok:
        raise ValueError("refusing to write invalid manifest: " + "; ".join(errs))
    _atomic_write(
        path,
        yaml.safe_dump(scaffold, sort_keys=False, allow_unicode=True, default_flow_style=False),
    )
    return f"Created {path}"


@mcp.tool()
def backlog_project_error_trace_ladder() -> list[dict]:
    """Return the observability error-trace ladder as a list of dicts.

    Empty list if no manifest. Consumed by IDEA-006 Diagnose-Auth-Or-Not.
    """
    m = load_project_manifest(_project_root_or_cwd())
    if m is None:
        return []
    return [asdict(e) for e in m.error_trace_ladder()]


# ── Linear MCP tools (linear-005) ────────────────────────────────────────────


@mcp.tool()
def backlog_linear_probe(token_env: str) -> str:
    """Discover Linear workspace structure using a token read from the environment.

    Calls list_teams, then for each team samples list_issue_statuses and list_users.
    Returns a JSON summary suitable for proposing a status/priority mapping to the user.
    Never prints the token. On missing env var: error with a link to the Linear API key page.

    Args:
        token_env: Name of the environment variable holding the Linear API token.
    """
    import json
    import os
    from integrations.linear.client import LinearAPIError, LinearClient

    token = os.environ.get(token_env)
    if not token:
        return json.dumps({
            "error": (
                f"${token_env} is not set. "
                f"Create a Linear personal API key at https://linear.app/settings/api "
                f"then export {token_env}=<token>."
            )
        })

    client = LinearClient(token=token)
    try:
        teams = client.list_teams()
    except LinearAPIError as e:
        return json.dumps({"error": str(e)})

    result = []
    for team in teams:
        tid = team.get("id", "")
        entry: dict = {"id": tid, "name": team.get("name"), "key": team.get("key")}
        try:
            entry["statuses"] = client.list_issue_statuses(tid)
        except LinearAPIError as e:
            entry["statuses_error"] = str(e)
        try:
            entry["users"] = client.list_users(tid)
        except LinearAPIError as e:
            entry["users_error"] = str(e)
        result.append(entry)

    return json.dumps({"teams": result}, indent=2)


@mcp.tool()
def backlog_linear_bootstrap_apply(
    workspace_alias: str,
    team_id: str,
    token_env: str,
    status_mapping: str = "",
    priority_mapping: str = "",
    default_workspace: bool = True,
) -> str:
    """Write (or append) a workspace entry to .taskmaster/linear.yaml.

    If linear.yaml exists: appends the new workspace (errors if alias collides).
    If absent: creates the file with this workspace as the only entry.

    status_mapping / priority_mapping: optional comma-separated tm_value:linear_id pairs
    (e.g. "todo:state-uuid-1,in-progress:state-uuid-2").

    Args:
        workspace_alias: Short identifier for this workspace (e.g. "cm").
        team_id: Linear team UUID for this workspace.
        token_env: Environment variable name holding the Linear API token.
        status_mapping: Comma-separated TM-status:linear-state-id pairs.
        priority_mapping: Comma-separated TM-priority:linear-priority-id pairs.
        default_workspace: If True (default), set this workspace as default_workspace.
    """
    import json
    from taskmaster_v3 import (
        linear_config_path,
        _validate_linear_config,
    )

    if not workspace_alias or not workspace_alias.strip():
        return json.dumps({"error": "workspace_alias is required"})
    if not team_id or not team_id.strip():
        return json.dumps({"error": "team_id is required"})
    if not token_env or not token_env.strip():
        return json.dumps({"error": "token_env is required"})

    def _parse_mapping(raw: str) -> dict:
        """Parse 'a:b,c:d' into {'a': 'b', 'c': 'd'}, deduped, no empty halves."""
        out: dict = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            parts = pair.split(":", 1)
            if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                raise ValueError(f"invalid mapping pair {pair!r} — expected tm_value:linear_id")
            out[parts[0].strip()] = parts[1].strip()
        return out

    try:
        sm = _parse_mapping(status_mapping) if status_mapping.strip() else {}
        pm = _parse_mapping(priority_mapping) if priority_mapping.strip() else {}
    except ValueError as e:
        return json.dumps({"error": str(e)})

    bp = _backlog_path()
    cfg_path = linear_config_path(bp)

    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        existing_aliases = {ws.get("alias") for ws in cfg.get("workspaces") or []}
        if workspace_alias in existing_aliases:
            return json.dumps({"error": f"workspace alias {workspace_alias!r} already exists in linear.yaml"})
    else:
        cfg = {}

    ws_entry: dict = {
        "alias": workspace_alias,
        "team_id": team_id,
        "token_env": token_env,
    }
    if sm:
        ws_entry["status_mapping"] = sm
    if pm:
        ws_entry["priority_mapping"] = pm

    cfg.setdefault("workspaces", []).append(ws_entry)
    if default_workspace:
        cfg["default_workspace"] = workspace_alias

    try:
        _validate_linear_config(cfg)
    except ValueError as e:
        return json.dumps({"error": f"config validation failed: {e}"})

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return json.dumps({
        "ok": True,
        "path": str(cfg_path),
        "workspace": workspace_alias,
        "default": default_workspace,
    })


@mcp.tool()
def backlog_linear_link(task_id: str, external_key: str, workspace_alias: str = "") -> str:
    """Link an existing TM task to an existing Linear issue by creating a Tracker file.

    Pure local operation — does not push to Linear or fetch from it.
    Sets tracker_id on the task in backlog.yaml.

    Args:
        task_id: Taskmaster task id (e.g. "ts-001").
        external_key: Linear issue identifier (e.g. "ENG-42").
        workspace_alias: Workspace alias from linear.yaml. Uses default_workspace if empty.
    """
    import json

    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({"error": "No backlog found."})

    cfg = _load_linear_config(bp)
    if cfg is None:
        return json.dumps({"error": "linear.yaml not found — run backlog_linear_bootstrap_apply first."})

    try:
        from taskmaster_v3 import get_linear_workspace
        workspace = get_linear_workspace(cfg, workspace_alias or None)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    alias = workspace["alias"]
    data = _load()
    result = _find_task(data, task_id)
    if result is None:
        return json.dumps({"error": f"task {task_id!r} not found in backlog"})
    task, _epic = result

    existing_tracker = task.get("tracker_id")
    if existing_tracker:
        return json.dumps({"error": f"task {task_id!r} already has tracker_id {existing_tracker!r} — unlink first"})

    tracker_id = _make_tracker_id("linear", alias, external_key)
    tp = _tracker_path(bp, tracker_id)
    if tp.exists():
        return json.dumps({"error": f"tracker file already exists at {tp} — it may be linked to another task"})

    try:
        _write_tracker(
            bp,
            external_system="linear",
            instance_alias=alias,
            external_key=external_key,
            title=task.get("title", external_key),
            status=task.get("status", "todo"),
        )
    except (ValueError, OSError) as e:
        return json.dumps({"error": f"failed to write tracker: {e}"})

    task["tracker_id"] = tracker_id
    _save(data)

    return json.dumps({"ok": True, "tracker_id": tracker_id, "task_id": task_id})


@mcp.tool()
def backlog_linear_unlink(task_id: str) -> str:
    """Clear the tracker_id on a TM task. Does NOT delete the Tracker file.

    Idempotent: if the task has no tracker_id, returns that fact without error.

    Args:
        task_id: Taskmaster task id (e.g. "ts-001").
    """
    import json

    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({"error": "No backlog found."})

    data = _load()
    result = _find_task(data, task_id)
    if result is None:
        return json.dumps({"error": f"task {task_id!r} not found in backlog"})
    task, _epic = result

    existing = task.get("tracker_id")
    if not existing:
        return json.dumps({"ok": True, "note": f"task {task_id!r} had no tracker_id — nothing to unlink"})

    task.pop("tracker_id", None)
    _save(data)
    return json.dumps({"ok": True, "unlinked": existing, "task_id": task_id})


@mcp.tool()
def backlog_linear_list() -> str:
    """Return JSON list of all linear-* trackers from disk.

    Each item: id, external_key, title, status, instance_alias, last_pushed, push_hash.
    Reads from tracker frontmatter; does not hit Linear.
    """
    import json

    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({"trackers": []})

    out = []
    for tid in _list_tracker_ids(bp):
        if not tid.startswith("linear-"):
            continue
        try:
            fm, _ = _read_tracker(bp, tid)
        except (OSError, yaml.YAMLError):
            continue
        out.append({
            "id": fm.get("id"),
            "external_key": fm.get("external_key"),
            "title": fm.get("title"),
            "status": fm.get("status"),
            "instance_alias": fm.get("instance_alias"),
            "last_pushed": fm.get("last_pushed"),
            "push_hash": fm.get("push_hash"),
        })

    return json.dumps({"trackers": out}, indent=2)


@mcp.tool()
def backlog_linear_show(tracker_id: str) -> str:
    """Return one tracker's full frontmatter and body as JSON.

    Returns an error-style JSON if the tracker is missing.

    Args:
        tracker_id: Tracker id in linear-<alias>-<key> format (e.g. "linear-cm-eng-42").
    """
    import json

    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({"error": "No backlog found."})

    tp = _tracker_path(bp, tracker_id)
    if not tp.exists():
        return json.dumps({"error": f"tracker {tracker_id!r} not found"})

    try:
        fm, body = _read_tracker(bp, tracker_id)
    except (OSError, yaml.YAMLError) as e:
        return json.dumps({"error": f"cannot read tracker: {e}"})

    return json.dumps({"frontmatter": fm, "body": body}, indent=2, default=str)


@mcp.tool()
def backlog_linear_status() -> str:
    """Return a summary of the Linear sync queue state.

    Includes: queue depth, oldest pending item's enqueued_at, count of permanent
    failures, and the last error message if any. No network calls.
    """
    import json
    from integrations.linear.worker import read_queue

    bp = _backlog_path()
    items = read_queue(bp) if bp.exists() else []

    permanent_count = sum(1 for i in items if i.get("permanent"))
    pending = [i for i in items if not i.get("permanent")]
    oldest_at = None
    if pending:
        oldest_at = min((i.get("enqueued_at") or "") for i in pending) or None

    last_error = None
    for item in reversed(items):
        if item.get("last_error"):
            last_error = item["last_error"]
            break

    return json.dumps({
        "queue_depth": len(items),
        "pending": len(pending),
        "permanent_failures": permanent_count,
        "oldest_enqueued_at": oldest_at,
        "last_error": last_error,
    }, indent=2)


@mcp.tool()
def backlog_linear_retry(target_id: str = "") -> str:
    """Drain the Linear sync queue, optionally limiting to one target.

    If target_id is empty: drains all queued items.
    If target_id is provided: drains only items for that target, leaving others intact.

    Requires linear.yaml and the configured token env var.

    Args:
        target_id: Taskmaster task id to retry. Empty = retry all.
    """
    import json
    from integrations.linear.client import LinearAPIError, LinearClient
    from integrations.linear import worker as _worker

    bp = _backlog_path()
    if not bp.exists():
        return json.dumps({"error": "No backlog found."})

    cfg = _load_linear_config(bp)
    if cfg is None:
        return json.dumps({"error": "linear.yaml not found — run backlog_linear_bootstrap_apply first."})

    try:
        from taskmaster_v3 import get_linear_workspace, resolve_linear_token
        workspace = get_linear_workspace(cfg)
        token = resolve_linear_token(workspace)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    try:
        client = LinearClient(token=token)
    except (ValueError, LinearAPIError) as e:
        return json.dumps({"error": f"cannot build Linear client: {e}"})

    data = _load()

    # An explicit retry is the un-park action: clear permanent/attempts on the
    # items being retried so a previously-parked push gets one fresh attempt
    # (routine drains still skip parked items). The full queue is rewritten with
    # cleared flags, then drained with a target filter — so a target-scoped retry
    # never removes other targets' items from disk and a crash mid-drain cannot
    # lose them (B-029).
    all_items = _worker.read_queue(bp)
    targets_present = False
    for it in all_items:
        if target_id and it.get("target_id") != target_id:
            continue
        targets_present = True
        it.pop("permanent", None)
        it["attempts"] = 0
        it["last_error"] = None

    if target_id and not targets_present:
        return json.dumps({"error": f"no queued items for target_id {target_id!r}"})

    _worker._write_queue(bp, all_items)
    counts = _worker.drain(
        bp, client, cfg, backlog_data=data,
        only_targets={target_id} if target_id else None,
    )

    return json.dumps({"ok": True, "counts": counts}, indent=2)


if __name__ == "__main__":
    mcp.run()
