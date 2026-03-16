# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp", "pyyaml"]
# ///

import json
import os
import socket
import subprocess
import threading
import uuid
import webbrowser
from datetime import date, datetime
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

import yaml
from fastmcp import FastMCP

mcp = FastMCP("taskmaster")

SCRIPT_DIR = Path(__file__).parent
ROOT = Path(os.environ.get("TASKMASTER_ROOT", Path.cwd()))
VIEWER_PATH = SCRIPT_DIR / "backlog-viewer.html"
CONFIG_PATH = ROOT / ".claude" / "taskmaster.json"


def _resolve_paths() -> tuple[Path, Path]:
    """Resolve backlog.yaml and PROGRESS.md paths from config or defaults.

    Priority: .claude/taskmaster.json config > .claude/backlog.yaml > ./backlog.yaml
    """
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return (
                ROOT / config.get("backlog_path", "backlog.yaml"),
                ROOT / config.get("progress_path", "PROGRESS.md"),
            )
        except (json.JSONDecodeError, KeyError):
            pass

    # Auto-detect: check .claude/ first, then project root
    if (ROOT / ".claude" / "backlog.yaml").exists():
        return ROOT / ".claude" / "backlog.yaml", ROOT / ".claude" / "PROGRESS.md"
    return ROOT / "backlog.yaml", ROOT / "PROGRESS.md"


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


def _load() -> dict:
    return yaml.safe_load(_backlog_path().read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    with _backlog_lock:
        data["meta"]["updated"] = _today()
        bp = _backlog_path()
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
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


def _find_milestone(data: dict, milestone_id: str) -> dict | None:
    for ms in data.get("milestones", []):
        if ms["id"] == milestone_id:
            return ms
    return None


def _active_milestone(data: dict) -> dict | None:
    """Return the currently active milestone, or None."""
    for ms in data.get("milestones", []):
        if ms.get("status") == "active":
            return ms
    return None


def _milestone_task_ids(data: dict, milestone_id: str) -> set[str]:
    """Get all task IDs assigned to a milestone."""
    ids = set()
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t.get("milestone") == milestone_id:
                ids.add(t["id"])
    return ids


def _milestone_stats(data: dict, milestone_id: str) -> dict:
    """Compute stats for a specific milestone."""
    counts = {"todo": 0, "in-progress": 0, "in-review": 0, "done": 0, "blocked": 0, "archived": 0}
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t.get("milestone") == milestone_id:
                s = t.get("status", "todo")
                counts[s] = counts.get(s, 0) + 1
    total = sum(counts.values()) - counts["archived"]
    return {"total": total, **counts}


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

    # next_up: top 3 priority todo across active epics
    todo_tasks = []
    for t, epic in all_tasks:
        if t.get("status") == "todo" and epic.get("status") == "active":
            todo_tasks.append((t, epic))
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    todo_tasks.sort(key=lambda x: (priority_order.get(x[0].get("priority", "P2"), 9), str(x[0].get("created", ""))))
    next_up = [
        {"id": t["id"], "title": t["title"], "priority": t.get("priority", "P2"), "epic": epic["id"]}
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
        active_epic = active_epics[0] if active_epics else data["epics"][0]["id"]

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

    # Milestone context
    active_ms = _active_milestone(data)
    if active_ms:
        ms_stats = _milestone_stats(data, active_ms["id"])
        data["context"]["active_milestone"] = {
            "id": active_ms["id"],
            "name": active_ms["name"],
            "stats": ms_stats,
        }
    else:
        data["context"]["active_milestone"] = None


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

    # Milestone progress
    milestones = data.get("milestones", [])
    if milestones:
        active_ms = _active_milestone(data)
        if active_ms:
            ms_stats = _milestone_stats(data, active_ms["id"])
            ms_done = ms_stats["done"]
            ms_total = ms_stats["total"]
            lines.append(f"**Active Milestone:** {active_ms['name']} ({ms_done}/{ms_total} done)")
        # List all milestones briefly
        ms_summary = []
        for ms in sorted(milestones, key=lambda m: m.get("order", 999)):
            s = ms.get("status", "planned")
            if s == "archived":
                continue
            label = {"active": ">>", "done": "done", "planned": "..."}.get(s, s)
            ms_summary.append(f"{label} {ms['name']}")
        if ms_summary:
            lines.append(f"**Milestones:** {' | '.join(ms_summary)}")
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
        nu_str = ", ".join(f"{t['id']} {t['title']} ({t.get('priority', 'P2')})" for t in next_items)
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


def _task_context(data: dict, task: dict, epic: dict) -> str:
    """Format task details + epic context for display after pick."""
    lines = [
        f"**Epic:** {epic['name']} — {epic.get('description', '')}",
        f"**Priority:** {task.get('priority', 'P2')}",
    ]
    if task.get("notes"):
        lines.append(f"**Notes:** {task['notes']}")
    if task.get("branch"):
        lines.append(f"**Branch:** {task['branch']}")
    if task.get("blockers"):
        lines.append(f"**Blockers:** {task['blockers']}")

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
def backlog_status() -> str:
    """Show project dashboard: epic progress table, in-progress tasks, blocked items, next priorities, and stats."""
    data = _load()
    regenerate_context(data)  # ensure fresh stats without writing
    ctx = data["context"]

    lines = ["## Dashboard\n"]
    lines.append("| Workstream | Status | Progress | Current Focus |")
    lines.append("|-----------|--------|----------|---------------|")

    for epic in data["epics"]:
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

    # Next Up
    nu = ctx.get("next_up", [])
    if nu:
        lines.append("**Next Up:**")
        for t in nu:
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('priority', 'P2')})")
    else:
        lines.append("**Next Up:** —")

    # Stats
    s = ctx.get("stats", {})
    stats_line = f"\nTotal: {s.get('total', 0)} | Done: {s.get('done', 0)} | In Progress: {s.get('in_progress', 0)} | In Review: {s.get('in_review', 0)} | Active: {s.get('in_progress', 0) + s.get('in_review', 0)} | Todo: {s.get('todo', 0)} | Blocked: {s.get('blocked', 0)}"
    if s.get("archived", 0):
        stats_line += f" | Archived: {s['archived']}"
    lines.append(stats_line)

    # Milestone info
    milestones = data.get("milestones", [])
    active_ms = _active_milestone(data)
    if active_ms:
        ms_stats = _milestone_stats(data, active_ms["id"])
        ms_done = ms_stats["done"]
        ms_total = ms_stats["total"]
        lines.append(f"\n**Active Milestone:** {active_ms['name']} — {ms_done}/{ms_total} tasks done")
        if active_ms.get("description"):
            lines.append(f"  {active_ms['description']}")
    if milestones:
        lines.append("\n**Milestones:**")
        for ms in sorted(milestones, key=lambda m: m.get("order", 999)):
            s = ms.get("status", "planned")
            if s == "archived":
                continue
            ms_st = _milestone_stats(data, ms["id"])
            marker = {"active": "▶", "done": "✓", "planned": "○"}.get(s, "?")
            lines.append(f"- {marker} **{ms['name']}** ({ms_st['done']}/{ms_st['total']}) — {s}")

    return "\n".join(lines)


@mcp.tool()
def backlog_list_tasks(epic: str = "", status: str = "", priority: str = "", milestone: str = "") -> str:
    """List tasks with optional filters. All params optional — defaults to showing all tasks.

    Args:
        epic: Filter by epic ID (e.g., "ue-plugin", "desktop-app")
        status: Filter by status: todo, in-progress, in-review, done, blocked
        priority: Filter by priority: P0, P1, P2, P3
        milestone: Filter by milestone ID
    """
    data = _load()
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    results: list[tuple[int, str, str]] = []  # (priority_rank, created, formatted)
    for ep in data["epics"]:
        if epic and ep["id"] != epic:
            continue
        for t in ep.get("tasks", []):
            if status and t.get("status") != status:
                continue
            # Hide archived tasks unless explicitly filtering for them
            if not status and t.get("status") == "archived":
                continue
            if priority and t.get("priority") != priority:
                continue
            if milestone and t.get("milestone") != milestone:
                continue
            pri = t.get("priority", "P2")
            results.append((
                priority_order.get(pri, 9),
                str(t.get("created", "")),
                f"`{t['id']}` — {t['title']} ({pri}, {ep['id']}, {t.get('status', 'todo')})",
            ))

    if not results:
        filters = []
        if epic:
            filters.append(f"epic={epic}")
        if status:
            filters.append(f"status={status}")
        if priority:
            filters.append(f"priority={priority}")
        if milestone:
            filters.append(f"milestone={milestone}")
        return f"No tasks found matching: {', '.join(filters) if filters else 'any'}"

    results.sort(key=lambda x: (x[0], x[1]))
    return f"**{len(results)} tasks:**\n" + "\n".join(f"- {r[2]}" for r in results)


@mcp.tool()
def backlog_get_task(task_id: str) -> str:
    """Get full details for a single task including epic context and related tasks.

    Args:
        task_id: The task ID (e.g., "ue-plugin-003")
    """
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    lines = [f"## `{task['id']}` — {task['title']}\n"]

    fields = [
        ("Status", task.get("status", "todo")),
        ("Priority", task.get("priority", "P2")),
        ("Epic", f"{epic['name']} ({epic['id']})"),
        ("Stage", str(task["stage"]) if task.get("stage") is not None else "—"),
        ("Estimate", task.get("estimate", "—")),
        ("Milestone", task.get("milestone", "—")),
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
    next_todo.sort(key=lambda t: ({"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(t.get("priority", "P2"), 9)))
    if next_todo[:3]:
        lines.append("\n**Next todo in this epic:**")
        for t in next_todo[:3]:
            lines.append(f"- `{t['id']}` — {t['title']} ({t.get('priority', 'P2')})")

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
                priority = task.get("priority", "P2")
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
def backlog_next_available() -> str:
    """Show tasks that are ready to work on — todo tasks in active epics with all dependencies satisfied.
    Sorted by priority, then by creation date."""
    data = _load()
    active_ms = _active_milestone(data)

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
            # Filter by active milestone if one exists
            if active_ms and task.get("milestone") != active_ms["id"]:
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

    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    available.sort(key=lambda x: (priority_order.get(x[0].get("priority", "P2"), 9), str(x[0].get("created", ""))))

    lines = ["## Available Tasks\n"]

    if active_ms:
        lines.append(f"*Filtered to milestone: **{active_ms['name']}***\n")

    if available:
        lines.append(f"**{len(available)} tasks ready to pick:**")
        for task, epic in available:
            lines.append(f"- `{task['id']}` — {task['title']} ({task.get('priority', 'P2')}, {epic['id']})")
    else:
        if active_ms:
            lines.append(f"No tasks available in milestone **{active_ms['name']}** — all tasks are done, in progress, or have unmet dependencies.")
        else:
            lines.append("No tasks available — all todo tasks have unmet dependencies or belong to non-active epics.")

    if blocked_by_deps:
        lines.append(f"\n**{len(blocked_by_deps)} tasks blocked by dependencies:**")
        for task, epic, unmet in blocked_by_deps[:5]:
            unmet_str = ", ".join(f"`{d}`" for d in unmet)
            lines.append(f"- `{task['id']}` — {task['title']} (waiting on {unmet_str})")

    # Show unassigned tasks hint
    if active_ms:
        unassigned = []
        for epic in data["epics"]:
            if epic.get("status") != "active":
                continue
            for task in epic.get("tasks", []):
                if task.get("status") == "todo" and not task.get("milestone"):
                    unassigned.append(task)
        if unassigned:
            lines.append(f"\n*{len(unassigned)} todo tasks are not assigned to any milestone.*")

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

    # 7. Milestone validation
    for task, epic in all_tasks:
        tid = task["id"]
        # 8. Milestone references that don't exist
        task_ms = task.get("milestone")
        if task_ms and not _find_milestone(data, task_ms):
            issues.append(f"`{tid}`: milestone `{task_ms}` does not exist")

    # Stats summary
    stats = {"total": len(all_tasks), "issues": len(issues)}

    if issues:
        header = f"**{len(issues)} issue{'s' if len(issues) != 1 else ''} found** across {stats['total']} tasks:\n"
        return header + "\n".join(f"- {i}" for i in issues)
    else:
        return f"All clear — {stats['total']} tasks validated, no issues found."


# ── Mutating Tools ───────────────────────────────────────────


@mcp.tool()
def backlog_init(project_name: str = "", location: str = "hidden") -> str:
    """Initialize taskmaster in the current project. Creates config, backlog.yaml, and PROGRESS.md.

    Args:
        project_name: Name for the project. Defaults to the directory name.
        location: Where to store backlog files.
                  "hidden" — .claude/ directory (won't pollute repo, gitignored by default).
                  "tracked" — project root (visible, can be committed to git for team visibility).
    """
    if location not in ("hidden", "tracked"):
        return f"Error: location must be 'hidden' or 'tracked', got '{location}'"

    if not project_name:
        project_name = ROOT.name

    # Check if already initialized (check both locations)
    for check_path in [ROOT / ".claude" / "backlog.yaml", ROOT / "backlog.yaml"]:
        if check_path.exists():
            rel = check_path.relative_to(ROOT)
            return (
                f"Already initialized — `backlog.yaml` exists at `{rel}`.\n"
                f"Use `backlog_status` to see the current state."
            )

    # Determine paths based on location choice
    if location == "hidden":
        backlog_rel = ".claude/backlog.yaml"
        progress_rel = ".claude/PROGRESS.md"
    else:
        backlog_rel = "backlog.yaml"
        progress_rel = "PROGRESS.md"

    backlog_abs = ROOT / backlog_rel
    progress_abs = ROOT / progress_rel

    created = []

    # Write config so the server knows where to find files
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = {"backlog_path": backlog_rel, "progress_path": progress_rel}
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    created.append(".claude/taskmaster.json")

    # Create backlog.yaml
    backlog_abs.parent.mkdir(parents=True, exist_ok=True)
    initial_data = {
        "meta": {
            "project": project_name,
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
        "milestones": [],
    }
    backlog_abs.write_text(
        yaml.dump(initial_data, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    created.append(backlog_rel)

    # Create PROGRESS.md
    progress_content = f"# {project_name} Progress\n\n> Auto-generated from backlog.yaml — do not edit manually\n\n## Dashboard\n\n---\n\n## Changelog\n"
    progress_abs.write_text(progress_content, encoding="utf-8")
    created.append(progress_rel)

    location_label = "`.claude/` (hidden from repo)" if location == "hidden" else "project root (trackable in git)"
    return (
        f"Initialized taskmaster for **{project_name}** in {location_label}\n"
        f"Created: {', '.join(created)}"
    )


@mcp.tool()
def backlog_add_task(
    title: str, epic: str, priority: str = "P2", notes: str = "",
    docs: str = "", depends_on: str = "", sub_repo: str = "",
    stage: int | None = None, estimate: str = "", milestone: str = "",
) -> str:
    """Create a new task under an epic. Auto-generates the task ID.

    Args:
        title: Short imperative description of the task
        epic: Epic ID (e.g., "ue-plugin", "desktop-app", "cpp-parser")
        priority: P0 (critical) through P3 (nice-to-have), default P2
        notes: Optional freeform context
        docs: Optional doc references as "key:path" pairs separated by semicolons (e.g., "plan:docs/plans/foo.md;spec:docs/specs/bar.md")
        depends_on: Optional comma-separated task IDs this task depends on (e.g., "cpp-parser-002,cpp-parser-003")
        sub_repo: Optional sub-repo directory name for monorepo projects
        stage: Optional stage number for phased work
        estimate: Optional size estimate (e.g., "S", "M", "L")
        milestone: Optional milestone ID to assign this task to
    """
    if priority not in VALID_PRIORITIES:
        return f"Error: invalid priority `{priority}`. Valid: {', '.join(sorted(VALID_PRIORITIES))}"

    data = _load()
    epic_obj = _find_epic(data, epic)
    if not epic_obj:
        return f"Error: epic `{epic}` not found. Valid epics: {_epic_names(data)}"

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

    new_task: dict = {
        "id": new_id,
        "title": title,
        "status": "todo",
        "priority": priority,
        "created": _today(),
        "notes": notes,
    }

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
    if milestone:
        if not _find_milestone(data, milestone):
            return f"Error: milestone `{milestone}` not found"
        new_task["milestone"] = milestone

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

    if "tasks" not in epic_obj:
        epic_obj["tasks"] = []
    epic_obj["tasks"].append(new_task)

    _mutate_and_save(data)
    # Warn if notes are getting bloated
    notes_warning = ""
    if notes and len(notes) > 300:
        notes_warning = (
            f"\n\n**Warning:** Notes are over 300 characters. Consider moving detailed content "
            f"to an external doc and linking it via `backlog_update_task({new_id}, docs, plan:docs/plans/...)`"
        )

    return f"Added `{new_id}` — {title} ({priority}) under {epic_obj['name']}" + notes_warning


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
def backlog_pick_task(task_id: str) -> str:
    """Start working on a task — sets it to in-progress. Idempotent if already in-progress.

    Args:
        task_id: The task ID to pick (e.g., "ue-plugin-003")
    """
    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    status = task.get("status", "todo")

    # Allowed statuses: todo, in-progress (idempotent), in-review (revert to in-progress)
    locked_by = task.get("locked_by")

    if status == "in-progress":
        if locked_by and locked_by != SESSION_ID:
            # Another session owns this task
            return (
                f"Error: task `{task_id}` is locked by another session (`{locked_by}`). "
                f"It is already in-progress elsewhere. Pick a different task, or use "
                f"`backlog_update_task({task_id}, locked_by, {SESSION_ID})` to force-claim it."
            )
        # Idempotent: update session state and lock, return details without mutation
        if not locked_by:
            task["locked_by"] = SESSION_ID
            _mutate_and_save(data)
        _set_session_task(task, epic)
        sub_repo = task.get("sub_repo", "")
        branch = task.get("branch", "")
        worktree = task.get("worktree", "")
        worktree_instruction = _build_worktree_instruction(task_id, sub_repo, branch, worktree)
        return f"Already in progress: `{task_id}` — {task['title']}\n\n" + _task_context(data, task, epic) + worktree_instruction

    if status not in ("todo", "in-review"):
        # blocked/done tasks cannot be picked — use backlog_update_task to change status first
        return f"Error: task `{task_id}` is `{status}`, expected one of: todo, in-progress, in-review"

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

    return f"Picked `{task_id}` — {task['title']} (locked to this session)\n\n" + _task_context(data, task, epic) + worktree_instruction


def _append_changelog(
    session_title: str,
    done: str,
    decisions: str,
    issues: str,
    tasks_touched: str,
) -> str:
    """Insert a changelog entry into PROGRESS.md right after the '## Changelog' marker.

    Returns a confirmation message for the tool response.
    """
    title = session_title or "Work session"
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


@mcp.tool()
def backlog_complete_task(
    task_id: str,
    session_title: str = "",
    done: str = "",
    decisions: str = "",
    issues: str = "",
    tasks_touched: str = "",
    target_status: str = "done",
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
    """
    if target_status not in ("done", "in-review"):
        return f"Error: target_status must be 'done' or 'in-review', got '{target_status}'"

    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result
    status = task.get("status", "todo")

    if status not in ("in-progress", "in-review", "blocked"):
        return f"Error: task `{task_id}` is `{status}`, expected one of: in-progress, in-review, blocked"

    # Warn if skipping in-review when going straight to done
    review_warning = ""
    if target_status == "done" and status == "in-progress":
        review_warning = "\n\n**Note:** Task went directly from in-progress → done, skipping the in-review stage. Consider using `in-review` first so the user can manually test and confirm it works."

    task["status"] = target_status
    if target_status == "done":
        task["completed"] = _now()
    task.pop("locked_by", None)

    _mutate_and_save(data)
    if target_status == "done":
        _clear_session_task(task_id)

    # Append changelog entry if session summary provided
    changelog_msg = ""
    if session_title or done:
        changelog_msg = _append_changelog(session_title, done, decisions, issues, tasks_touched)

    # Suggest next task in same epic
    next_todo = [t for t in epic.get("tasks", []) if t.get("status") == "todo"]
    next_todo.sort(key=lambda t: ({"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(t.get("priority", "P2"), 9)))
    suggestion = ""
    if next_todo:
        n = next_todo[0]
        suggestion = f"\n\n**Next in {epic['name']}:** `{n['id']}` — {n['title']} ({n.get('priority', 'P2')})"

    status_label = "Completed" if target_status == "done" else "Moved to in-review"
    return f"{status_label} `{task_id}` — {task['title']}" + changelog_msg + review_warning + suggestion


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
    return f"Archived `{task_id}` — {task['title']} (reason: {reason})"


# ── Worktree Discovery ───────────────────────────────────


def _discover_sub_repos() -> list[Path]:
    """Auto-discover git repositories in ROOT (immediate children only)."""
    sub_repos = []
    for child in ROOT.iterdir():
        if child.is_dir() and (child / ".git").exists():
            sub_repos.append(child)
    return sub_repos


def _discover_worktrees() -> list[dict]:
    """Run `git worktree list --porcelain` in the project root and all sub-repos."""
    worktrees = []
    dirs_to_scan = [ROOT]
    dirs_to_scan.extend(_discover_sub_repos())

    for repo_dir in dirs_to_scan:
        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                capture_output=True, text=True, cwd=str(repo_dir), timeout=5,
            )
            if result.returncode != 0:
                continue
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

        # Parse porcelain output: blocks separated by blank lines
        current: dict = {}
        repo_name = repo_dir.name
        for line in result.stdout.splitlines():
            if not line.strip():
                if current:
                    current["repo"] = repo_name
                    worktrees.append(current)
                    current = {}
                continue
            if line.startswith("worktree "):
                current["path"] = line[len("worktree "):]
            elif line.startswith("HEAD "):
                current["head"] = line[len("HEAD "):][:8]
            elif line.startswith("branch "):
                # refs/heads/feature/foo → feature/foo
                branch = line[len("branch "):]
                if branch.startswith("refs/heads/"):
                    branch = branch[len("refs/heads/"):]
                current["branch"] = branch
            elif line == "detached":
                current["detached"] = True

        if current:
            current["repo"] = repo_name
            worktrees.append(current)

    # Filter out the main worktree (the repo root itself) — only show additional worktrees
    main_paths = {str(d.resolve()) for d in dirs_to_scan}
    worktrees = [w for w in worktrees if w.get("path") and str(Path(w["path"]).resolve()) not in main_paths]

    return worktrees


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


@mcp.tool()
def backlog_worktrees() -> str:
    """List all active git worktrees across the monorepo and sub-repos.
    Shows worktree path, branch, and which repo it belongs to.
    Useful for checking what worktrees exist before creating new ones or for cleanup."""
    worktrees = _discover_worktrees()

    if not worktrees:
        return "No active worktrees found (only main worktrees exist)."

    lines = [f"**{len(worktrees)} active worktree{'s' if len(worktrees) != 1 else ''}:**\n"]
    for wt in worktrees:
        branch = wt.get("branch", "detached")
        repo = wt.get("repo", "?")
        path = wt.get("path", "?")
        lines.append(f"- `{branch}` in **{repo}** → `{path}`")

    return "\n".join(lines)


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


ALLOWED_FIELDS = {"title", "status", "priority", "notes", "branch", "worktree", "blockers", "docs", "depends_on", "sub_repo", "stage", "estimate", "locked_by", "review_instructions", "milestone"}
VALID_STATUSES = {"todo", "in-progress", "in-review", "done", "archived", "blocked"}
VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
VALID_DOC_KEYS = {"plan", "spec", "roadmap", "design", "analysis"}


@mcp.tool()
def backlog_update_task(task_id: str, field: str, value: str) -> str:
    """Update a single field on a task. Status changes trigger appropriate date updates.

    Args:
        task_id: The task ID (e.g., "ue-plugin-003")
        field: Field to update — one of: title, status, priority, notes, branch, worktree, blockers, docs, depends_on, sub_repo, stage, estimate, locked_by, review_instructions, milestone
        value: New value. Format varies by field:
            - docs: "key:path" (e.g., "plan:docs/plans/foo.md")
            - depends_on: comma-separated task IDs (e.g., "cpp-parser-002,cpp-parser-003")
            - stage: integer
            - estimate: size string (e.g., "S", "M", "L")
            - sub_repo: sub-repo directory name for monorepo projects
            - locked_by: session ID to claim the lock, or "" to clear it
            - milestone: milestone ID to assign, or "" to clear
    """
    if field not in ALLOWED_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(ALLOWED_FIELDS))}"

    data = _load()
    result = _find_task(data, task_id)
    if not result:
        return f"Error: task `{task_id}` not found"

    task, epic = result

    if field == "status":
        if value not in VALID_STATUSES:
            return f"Error: invalid status `{value}`. Valid: {', '.join(sorted(VALID_STATUSES))}"
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
        if value not in VALID_PRIORITIES:
            return f"Error: invalid priority `{value}`. Valid: {', '.join(sorted(VALID_PRIORITIES))}"
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
    elif field == "milestone":
        if value == "" or value.lower() == "none":
            task.pop("milestone", None)
        else:
            if not _find_milestone(data, value):
                return f"Error: milestone `{value}` not found"
            task["milestone"] = value
    else:
        task[field] = value

    _mutate_and_save(data)

    # Warn if notes are getting bloated
    notes_warning = ""
    if field == "notes" and len(value) > 300:
        notes_warning = (
            f"\n\n**Warning:** Notes are over 300 characters. Consider moving detailed content "
            f"to an external doc and linking it via `backlog_update_task({task_id}, docs, plan:docs/plans/...)`"
        )

    return f"Updated `{task_id}` field `{field}` → {value}" + notes_warning


VALID_EPIC_STATUSES = {"active", "planned", "done"}
ALLOWED_EPIC_FIELDS = {"name", "status", "description"}


@mcp.tool()
def backlog_update_epic(epic_id: str, field: str, value: str) -> str:
    """Update a single field on an epic.

    Args:
        epic_id: The epic ID (e.g., "cpp-parser", "ue-plugin", "infra")
        field: Field to update — one of: name, status, description
        value: New value. For status, one of: active, planned, done
    """
    if field not in ALLOWED_EPIC_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(ALLOWED_EPIC_FIELDS))}"

    data = _load()
    epic = _find_epic(data, epic_id)
    if not epic:
        return f"Error: epic `{epic_id}` not found"

    if field == "status":
        if value not in VALID_EPIC_STATUSES:
            return f"Error: invalid epic status `{value}`. Valid: {', '.join(sorted(VALID_EPIC_STATUSES))}"

    old_value = epic.get(field, "")
    epic[field] = value
    _mutate_and_save(data)
    return f"Updated epic `{epic_id}` field `{field}`: `{old_value}` → `{value}`"


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
        "tasks": [],
    }

    data["epics"].append(new_epic)
    _mutate_and_save(data)
    return f"Created epic `{epic_id}` — {name} ({status})"


# ── Milestone Tools ──────────────────────────────────────


VALID_MILESTONE_STATUSES = {"planned", "active", "done", "archived"}
ALLOWED_MILESTONE_FIELDS = {"name", "status", "description", "order"}


@mcp.tool()
def backlog_add_milestone(
    milestone_id: str, name: str, description: str = "", order: int | None = None,
) -> str:
    """Create a new milestone. Milestones are sequential blocks of work — only one is active at a time.
    Tasks are assigned to milestones to control focus.

    Args:
        milestone_id: Short kebab-case identifier (e.g., "m1", "foundation", "mvp"). Must be unique.
        name: Human-readable name (e.g., "Foundation", "Core Features", "Polish & Launch")
        description: Brief description of the milestone's goals
        order: Position in the sequence (1, 2, 3...). Auto-assigned if omitted.
    """
    # Validate ID format
    if not milestone_id or not all(c.isalnum() or c == "-" for c in milestone_id) or milestone_id != milestone_id.lower():
        return f"Error: milestone_id must be lowercase kebab-case (e.g., 'm1', 'foundation'), got `{milestone_id}`"

    data = _load()

    if _find_milestone(data, milestone_id):
        return f"Error: milestone `{milestone_id}` already exists"

    if "milestones" not in data:
        data["milestones"] = []

    # Auto-assign order
    if order is None:
        existing_orders = [ms.get("order", 0) for ms in data["milestones"]]
        order = max(existing_orders, default=0) + 1

    # Auto-activate if this is the first milestone
    status = "planned"
    if not any(ms.get("status") == "active" for ms in data["milestones"]):
        status = "active"

    new_milestone = {
        "id": milestone_id,
        "name": name,
        "status": status,
        "description": description,
        "order": order,
        "created": _today(),
    }

    data["milestones"].append(new_milestone)
    _mutate_and_save(data)

    status_note = f" (auto-activated — first milestone)" if status == "active" else ""
    return f"Created milestone `{milestone_id}` — {name} (order: {order}){status_note}"


@mcp.tool()
def backlog_update_milestone(milestone_id: str, field: str, value: str) -> str:
    """Update a single field on a milestone.

    Args:
        milestone_id: The milestone ID (e.g., "m1", "foundation")
        field: Field to update — one of: name, status, description, order
        value: New value. For status: planned, active, done, archived. For order: integer.
    """
    if field not in ALLOWED_MILESTONE_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(ALLOWED_MILESTONE_FIELDS))}"

    data = _load()
    ms = _find_milestone(data, milestone_id)
    if not ms:
        return f"Error: milestone `{milestone_id}` not found"

    if field == "status":
        if value not in VALID_MILESTONE_STATUSES:
            return f"Error: invalid status `{value}`. Valid: {', '.join(sorted(VALID_MILESTONE_STATUSES))}"
        # If activating, deactivate any currently active milestone
        if value == "active":
            for other_ms in data.get("milestones", []):
                if other_ms["id"] != milestone_id and other_ms.get("status") == "active":
                    other_ms["status"] = "planned"
        if value == "done":
            ms["completed"] = _now()
        ms["status"] = value
    elif field == "order":
        try:
            ms["order"] = int(value)
        except ValueError:
            return f"Error: order must be an integer, got `{value}`"
    else:
        ms[field] = value

    _mutate_and_save(data)
    return f"Updated milestone `{milestone_id}` field `{field}` → {value}"


@mcp.tool()
def backlog_milestone_status(milestone_id: str = "") -> str:
    """Show detailed progress for a milestone. Defaults to the active milestone.

    Args:
        milestone_id: Milestone ID. If omitted, shows the active milestone.
    """
    data = _load()

    if milestone_id:
        ms = _find_milestone(data, milestone_id)
        if not ms:
            return f"Error: milestone `{milestone_id}` not found"
    else:
        ms = _active_milestone(data)
        if not ms:
            return "No active milestone. Create one with `backlog_add_milestone`."

    stats = _milestone_stats(data, ms["id"])
    lines = [f"## Milestone: {ms['name']}\n"]
    if ms.get("description"):
        lines.append(f"{ms['description']}\n")
    lines.append(f"**Status:** {ms['status']} | **Order:** {ms.get('order', '?')}")
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

    # List tasks in this milestone grouped by status
    for status_group in ["in-progress", "in-review", "todo", "blocked", "done"]:
        group_tasks = []
        for epic in data["epics"]:
            for t in epic.get("tasks", []):
                if t.get("milestone") == ms["id"] and t.get("status") == status_group:
                    group_tasks.append((t, epic))

        if group_tasks:
            label = status_group.replace("-", " ").title()
            lines.append(f"**{label}:**")
            for t, epic in group_tasks:
                pri = t.get("priority", "P2")
                lines.append(f"- `{t['id']}` — {t['title']} ({pri}, {epic['id']})")
            lines.append("")

    # Unassigned tasks hint
    unassigned_count = 0
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t.get("status") not in ("done", "archived") and not t.get("milestone"):
                unassigned_count += 1
    if unassigned_count:
        lines.append(f"*{unassigned_count} active tasks are not assigned to any milestone.*")

    return "\n".join(lines)


@mcp.tool()
def backlog_advance_milestone() -> str:
    """Complete the active milestone and activate the next one in sequence.
    Archives all 'done' tasks in the completed milestone. Activates the next 'planned' milestone by order.
    """
    data = _load()
    active_ms = _active_milestone(data)
    if not active_ms:
        return "No active milestone to advance."

    ms_stats = _milestone_stats(data, active_ms["id"])

    # Warn if there are incomplete tasks
    incomplete = ms_stats["todo"] + ms_stats["in-progress"] + ms_stats["in-review"] + ms_stats["blocked"]
    warning = ""
    if incomplete > 0:
        warning = (
            f"\n\n**Warning:** {incomplete} tasks in this milestone are not done "
            f"(todo: {ms_stats['todo']}, in-progress: {ms_stats['in-progress']}, "
            f"in-review: {ms_stats['in-review']}, blocked: {ms_stats['blocked']}). "
            f"They will remain in their current status but the milestone will be marked done."
        )

    # Mark active milestone as done
    active_ms["status"] = "done"
    active_ms["completed"] = _now()

    # Archive done tasks in this milestone
    archived_count = 0
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t.get("milestone") == active_ms["id"] and t.get("status") == "done":
                t["status"] = "archived"
                t["archive_reason"] = "done"
                t["archived"] = _now()
                archived_count += 1

    # Find and activate next planned milestone by order
    planned = [ms for ms in data.get("milestones", []) if ms.get("status") == "planned"]
    planned.sort(key=lambda m: m.get("order", 999))
    next_ms = planned[0] if planned else None

    if next_ms:
        next_ms["status"] = "active"

    _mutate_and_save(data)

    result = f"Completed milestone **{active_ms['name']}** — archived {archived_count} done tasks."
    if next_ms:
        next_stats = _milestone_stats(data, next_ms["id"])
        result += f"\n\nActivated next milestone: **{next_ms['name']}** ({next_stats['total']} tasks, order: {next_ms.get('order', '?')})"
        if next_ms.get("description"):
            result += f"\n{next_ms['description']}"
    else:
        result += "\n\nNo more planned milestones. Create one with `backlog_add_milestone`."

    return result + warning


@mcp.tool()
def backlog_snapshot(operations: str) -> str:
    """Preview what would change without writing to disk. Dry-run mode for batch planning.
    Describes the current state of the specified tasks/epics and what the requested operations would do.

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


# ── HTTP Viewer Server ───────────────────────────────────


class ViewerHandler(BaseHTTPRequestHandler):
    """Serves the backlog viewer HTML and YAML data."""

    def do_GET(self) -> None:
        from urllib.parse import unquote, urlparse
        parsed = urlparse(self.path)
        clean_path = unquote(parsed.path)

        if clean_path in ("/", "/index.html"):
            self._serve_file(VIEWER_PATH, "text/html")
        elif clean_path == "/backlog.yaml":
            self._serve_file(_backlog_path(), "text/yaml")
        elif clean_path == "/api/backlog":
            self._serve_json()
        elif clean_path == "/api/session":
            self._serve_session()
        elif clean_path == "/api/worktrees":
            self._serve_worktrees()
        elif clean_path.startswith("/file/"):
            self._serve_repo_file(clean_path)
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
        body = json.dumps(session_data).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_worktrees(self) -> None:
        try:
            wts = _discover_worktrees()
            body = json.dumps(wts, default=str).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

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
            body = json.dumps(data, default=str).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

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
.content{max-width:860px;margin:0 auto;padding:32px 24px}
h1,h2,h3,h4{color:#d4dae3;margin:20px 0 10px;line-height:1.3}
h1{font-size:24px;border-bottom:1px solid #363e4a;padding-bottom:8px}
h2{font-size:20px;border-bottom:1px solid #363e4a;padding-bottom:6px}
h3{font-size:16px}h4{font-size:14px;color:#97a0ad}
p{margin:8px 0}
a{color:#58a6ff}
code{font-family:"SFMono-Regular",Consolas,monospace;font-size:12px;background:#232a34;border:1px solid #363e4a;padding:1px 5px;border-radius:3px;color:#58a6ff}
pre{background:#0d1117;border:1px solid #363e4a;border-radius:6px;padding:14px 18px;overflow-x:auto;margin:12px 0}
pre code{background:none;border:none;padding:0;color:#d4dae3}
ul,ol{margin:8px 0;padding-left:22px}
li{margin:3px 0}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:13px}
th{text-align:left;padding:8px 12px;background:#232a34;border:1px solid #363e4a;font-weight:600}
td{padding:8px 12px;border:1px solid #363e4a}
tr:hover td{background:#1c222b}
blockquote{border-left:3px solid #58a6ff;padding:6px 14px;margin:10px 0;color:#97a0ad;background:#1c222b;border-radius:0 4px 4px 0}
hr{border:none;border-top:1px solid #363e4a;margin:20px 0}
strong{color:#d4dae3}
img{max-width:100%}
</style>
</head><body>
<div class="topbar"><a href="/">&larr; Backlog</a><span class="path">{{TITLE}}</span><a href="vscode://file/{{FULL_PATH}}" class="open-editor">&#x1F4DD; Open in VSCode</a></div>
<div class="content" id="content"></div>
<script>
const raw = decodeURIComponent(atob("{{B64CONTENT}}").split('').map(c=>'%'+('00'+c.charCodeAt(0).toString(16)).slice(-2)).join(''));
document.getElementById('content').innerHTML = marked.parse(raw);
</script>
</body></html>"""

VIEWER_PORT = 6800
_viewer_started = False


def _start_viewer_server() -> int:
    """Start the viewer HTTP server. Picks a free port if 6800 is taken."""
    global _viewer_started, VIEWER_PORT
    if _viewer_started:
        return VIEWER_PORT

    try:
        server = ThreadingHTTPServer(("127.0.0.1", VIEWER_PORT), ViewerHandler)
    except OSError:
        # Port 6800 is taken (stale process or another active session).
        # Use port 0 to let the OS pick a free port.
        server = ThreadingHTTPServer(("127.0.0.1", 0), ViewerHandler)
        VIEWER_PORT = server.server_address[1]

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


if __name__ == "__main__":
    mcp.run()
