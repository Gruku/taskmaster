# User intent: serve epics and phases — creation, field edits, the archive
# cascade, activation and advance, and their status reads — from the native core
# (N08), answering and refusing exactly as the legacy epic and phase tools.
"""Epic and phase adapters."""
from __future__ import annotations

from datetime import date, datetime
import json

from taskmaster import backlog_server as bs
from taskmaster.native import domain

from . import reads
from .registry import adapter
from .runtime import error_text


def _run(call, operation, arguments):
    try:
        call.execute(operation, arguments)
    except (ValueError, KeyError) as exc:
        return error_text(exc)
    return None


# ── Epics ───────────────────────────────────────────────────────────────────


@adapter("backlog_add_epic")
def add_epic(call, *, epic_id, name, done_when, description, status, area):
    if not done_when.strip():
        return f"Error: {domain.EPIC_DONE_WHEN_REQUIRED_MSG}"
    if status not in domain.VALID_EPIC_STATUSES:
        return f"Error: invalid status `{status}`. Valid: {', '.join(sorted(domain.VALID_EPIC_STATUSES))}"
    if not epic_id or not all(c.isalnum() or c == "-" for c in epic_id) or epic_id != epic_id.lower():
        return f"Error: epic_id must be lowercase kebab-case (e.g., 'auth-system'), got `{epic_id}`"
    with call.read() as snapshot:
        if area:
            error = reads.validate_area_ref(snapshot, area)
            if error:
                return error
        if reads.get(snapshot, "epic", epic_id) is not None:
            return f"Error: epic `{epic_id}` already exists"
    refusal = _run(call, "epic.create", {"epic_id": epic_id, "name": name, "done_when": done_when,
                                          "description": description, "status": status, "area": area})
    if refusal:
        return refusal
    return call.finish(f"Created epic `{epic_id}` — {name} ({status})")


@adapter("backlog_update_epic")
def update_epic(call, *, epic_id, field, value):
    if field not in domain.ALLOWED_EPIC_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(domain.ALLOWED_EPIC_FIELDS))}"
    with call.read() as snapshot:
        entity = reads.get(snapshot, "epic", epic_id)
        if entity is None:
            return f"Error: epic `{epic_id}` not found"
        epic = entity["fields"]
        area_error = reads.validate_area_ref(snapshot, value) if field == "area" and value else None
    answer = None
    if field == "status":
        if value == "archived":
            return "Error: use `backlog_archive_epic` to archive an epic (it cascades to tasks)"
        if value not in domain.VALID_EPIC_STATUSES:
            return f"Error: invalid epic status `{value}`. Valid: {', '.join(sorted(domain.VALID_EPIC_STATUSES))}"
    elif field == "docs":
        if ":" not in value:
            return (f"Error: docs value must be `key:path` format (e.g., `design:docs/design/foo.md`). "
                    f"Valid keys: {', '.join(sorted(domain.VALID_DOC_KEYS))}")
        key, path = (part.strip() for part in value.split(":", 1))
        if key not in domain.VALID_DOC_KEYS:
            return f"Error: invalid docs key `{key}`. Valid: {', '.join(sorted(domain.VALID_DOC_KEYS))}"
        answer = (f"Cleared epic `{epic_id}` doc key `{key}`" if path == ""
                  else f"Updated epic `{epic_id}` doc `{key}` → `{path}`")
    elif field == "components":
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return "Error: components value must be a JSON object {key: {title, after}}"
        error = domain.validate_components(parsed)
        if error:
            return error
        answer = f"Updated epic `{epic_id}` components ({len(parsed)} declared)"
    elif field == "design_status":
        if value not in domain.VALID_DESIGN_STATUSES:
            return f"Error: invalid design_status `{value}`. Valid: {', '.join(sorted(domain.VALID_DESIGN_STATUSES))}"
        answer = f"Updated epic `{epic_id}` design_status → `{value}`"
    elif field == "done_when":
        if not value.strip():
            return f"Error: {domain.EPIC_DONE_WHEN_REQUIRED_MSG}"
    elif field == "area" and area_error:
        return area_error
    if answer is None:
        answer = f"Updated epic `{epic_id}` field `{field}`: `{epic.get(field, '')}` → `{value}`"
    refusal = _run(call, "epic.update", {"id": epic_id, "field": field, "value": value})
    if refusal:
        return refusal
    return call.finish(answer)


@adapter("backlog_archive_epic")
def archive_epic(call, *, epic_id, reason):
    if reason not in domain.VALID_ARCHIVE_REASONS:
        return f"Error: invalid reason `{reason}`. Valid: {', '.join(sorted(domain.VALID_ARCHIVE_REASONS))}"
    with call.read() as snapshot:
        entity = reads.get(snapshot, "epic", epic_id)
        if entity is None:
            return f"Error: epic `{epic_id}` not found"
        if entity["fields"].get("status") == "archived":
            return f"Error: epic `{epic_id}` is already archived"
        cascaded = sum(1 for task in reads.epic_tasks(snapshot, epic_id) if task.get("status") != "archived")
    refusal = _run(call, "epic.archive", {"id": epic_id, "reason": reason})
    if refusal:
        return refusal
    return call.finish(f"Archived epic `{epic_id}` — {entity['fields'].get('name', epic_id)} "
                       f"({cascaded} tasks cascaded, reason: {reason})")


@adapter("backlog_epic_status")
def epic_status(call, *, epic_id):
    with call.read() as snapshot:
        return bs._epic_status_text(reads.tree(snapshot, context=False), epic_id)


# ── Phases ──────────────────────────────────────────────────────────────────


@adapter("backlog_add_phase")
def add_phase(call, *, phase_id, name, description, order, target_date, start_date):
    if not phase_id or not all(c.isalnum() or c == "-" for c in phase_id) or phase_id != phase_id.lower():
        return f"Error: phase_id must be lowercase kebab-case (e.g., 'foundation', 'mvp'), got `{phase_id}`"
    with call.read() as snapshot:
        phases = reads.phases(snapshot)
    if any(phase["id"] == phase_id for phase in phases):
        return f"Error: phase `{phase_id}` already exists"
    if order is None:
        order = max((phase.get("order", 0) for phase in phases), default=0) + 1
    if target_date and not domain.parse_date(target_date):
        return f"Error: target_date must be YYYY-MM-DD format, got `{target_date}`"
    if start_date and not domain.parse_date(start_date):
        return f"Error: start_date must be YYYY-MM-DD format, got `{start_date}`"
    activated = not any(phase.get("status") == "active" for phase in phases)
    refusal = _run(call, "phase.create", {"phase_id": phase_id, "name": name, "description": description,
                                           "order": order, "target_date": target_date, "start_date": start_date})
    if refusal:
        return refusal
    note = " (auto-activated — first phase)" if activated else ""
    return call.finish(f"Created phase `{phase_id}` — {name} (order: {order}){note}")


@adapter("backlog_update_phase")
def update_phase(call, *, phase_id, field, value):
    if field not in domain.ALLOWED_PHASE_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(domain.ALLOWED_PHASE_FIELDS))}"
    with call.read() as snapshot:
        phase = reads.find_phase(snapshot, phase_id)
    if not phase:
        return f"Error: phase `{phase_id}` not found"
    if field == "docs":
        if ":" not in value:
            return (f"Error: docs value must be `key:path` format "
                    f"(e.g. `design:docs/design/ship.md`). Valid keys: {', '.join(sorted(domain.VALID_DOC_KEYS))}")
        key = value.split(":", 1)[0].strip()
        if key not in domain.VALID_DOC_KEYS:
            return f"Error: invalid docs key `{key}`. Valid: {', '.join(sorted(domain.VALID_DOC_KEYS))}"
    refusal = _run(call, "phase.update", {"id": phase["id"], "field": field, "value": value})
    if refusal:
        return refusal
    return call.finish(f"Updated phase `{phase_id}` field `{field}` → {value}")


@adapter("backlog_phase_status")
def phase_status(call, *, phase_id):
    with call.read() as snapshot:
        return bs._phase_status_text(reads.tree(snapshot, context=False), phase_id)


@adapter("backlog_advance_phase")
def advance_phase(call, *, force):
    with call.read() as snapshot:
        data = reads.tree(snapshot, context=False)
        ranks = {phase["id"]: n for n, phase in enumerate(data["phases"])}
    active = bs._active_phase(data)
    if not active:
        return "No active phase to advance."
    stats = bs._phase_stats(data, active["id"])
    unchecked = [d for d in active.get("deliverables", []) if not d.get("done")]
    if unchecked and not force:
        items = "\n".join(f"  - [ ] {d['text']}" for d in unchecked)
        return (f"**Blocked:** {len(unchecked)} unchecked deliverable(s) in phase "
                f"**{active['name']}**:\n{items}\n\n"
                f"Check them off with `backlog_update_phase(phase_id=\"{active['id']}\", "
                f"field=\"deliverables\", value='{{\"action\":\"toggle\",\"index\":N}}')` "
                f"or advance with force=True.")
    incomplete = stats["todo"] + stats["in-progress"] + stats["in-review"] + stats["blocked"]
    warning = ""
    if incomplete > 0:
        warning = (f"\n\n**Warning:** {incomplete} tasks in this phase are not done "
                   f"(todo: {stats['todo']}, in-progress: {stats['in-progress']}, "
                   f"in-review: {stats['in-review']}, blocked: {stats['blocked']}). "
                   f"They will remain in their current status but the phase will be marked done.")
    archived = sum(1 for epic in data["epics"] for task in epic.get("tasks", [])
                   if task.get("phase") == active["id"] and task.get("status") == "done")
    planned = sorted((p for p in data["phases"] if p.get("status") == "planned"),
                     key=lambda p: (p.get("order") if isinstance(p.get("order"), int) else 999, ranks[p["id"]]))
    following = planned[0] if planned else None
    refusal = _run(call, "phase.advance", {"force": bool(force)})
    if refusal:
        return refusal
    result = f"Completed phase **{active['name']}** — archived {archived} done tasks."
    if active.get("start_date"):
        try:
            start = datetime.strptime(str(active["start_date"]), "%Y-%m-%d").date()
            result += f" Duration: {(date.today() - start).days}d."
        except ValueError:
            pass
    if active.get("target_date"):
        try:
            target = datetime.strptime(str(active["target_date"]), "%Y-%m-%d").date()
            delta = (date.today() - target).days
            result += " Completed on time." if delta <= 0 else f" Completed {delta}d past target."
        except ValueError:
            pass
    if following:
        following_stats = bs._phase_stats(data, following["id"])
        result += (f"\n\nActivated next phase: **{following['name']}** ({following_stats['total']} tasks, "
                   f"order: {following.get('order', '?')})")
        if following.get("description"):
            result += f"\n{following['description']}"
    else:
        result += "\n\nNo more planned phases. Create one with `backlog_add_phase`."
    return call.finish(result + warning)
