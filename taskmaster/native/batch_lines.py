"""User intent: one pure statement of what each `backlog_batch_update` line does and
why it is refused, so the N08 adapter can report per-line errors before commit and
the native core applies exactly the same line inside its all-or-nothing batch.
Constraint: pure — no database, file system or clock beyond the caller's `now`.
"""
from copy import deepcopy
from dataclasses import dataclass, field as dataclass_field
from typing import Callable

from taskmaster.taskmaster_v3 import VALID_LANES, compute_gate_state
from . import claims, domain

TASK_OPS = ("update", "status", "complete", "archive", "pick")


@dataclass
class Lookups:
    """What a line may consult besides the entity it edits."""
    task_exists: Callable[[str], bool]
    find_phase: Callable[[str], "dict | None"]
    area_error: Callable[[str], "str | None"]
    open_bugs: Callable[[str], list]
    # Whether a status change leaves the task's claim in place: a live peer's
    # claim survives it (`claims.survives_status_change`), which needs the
    # store and the caller's session, so the caller supplies the answer.
    keeps_claim: Callable[[dict, "str | None"], bool] = lambda doc, before: False
    # The session the batch runs for: a `pick` line refuses any other holder.
    session: str = ""


@dataclass
class Outcome:
    error: "str | None" = None
    doc: "dict | None" = None
    # ("field", field, expected) or ("status", status, reason_field) — how the tool
    # reports this line from the committed document.
    report: tuple = ()
    cascade: bool = False
    extra: dict = dataclass_field(default_factory=dict)


_MISSING = object()


def parse(operations: str):
    """`(line, op, parts)` for every non-blank line, exactly as the tool splits them."""
    for line in operations.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 3)
        yield line, (parts[0].lower() if parts else ""), parts


def classify(op, parts):
    """The line's kind, or None when the tool reports it as unknown/malformed."""
    if len(parts) < 2:
        return "malformed"
    if op == "update" and len(parts) >= 4:
        return "task"
    if op == "status" and len(parts) >= 3:
        return "task"
    if op in ("complete", "archive", "pick"):
        return "task"
    if op == "update_epic" and len(parts) >= 4:
        return "epic"
    return None


def task_line_arguments(op, parts) -> dict:
    """The native `task.batch_line` arguments for one parsed task line."""
    arguments = {"id": parts[1], "op": op}
    if op == "update":
        arguments.update(field=parts[2], value=parts[3])
    elif op == "status":
        arguments["status"] = parts[2]
    elif op == "archive":
        arguments["reason"] = parts[2] if len(parts) > 2 else "done"
    return arguments


def _archive_transition(doc, before, after, now):
    if after == before:
        return
    if after == "archived":
        doc.setdefault("archived", now)
    elif before == "archived":
        doc.pop("archived", None)
        doc.pop("archive_reason", None)


def _release_claim(doc, lookups: Lookups, before=None) -> None:
    if not lookups.keeps_claim(doc, before):
        doc.pop("locked_by", None)


def apply_task_line(arguments, task, lookups: Lookups, *, now) -> Outcome:
    """One task line against the task's current document (None when not found)."""
    op, ident = arguments["op"], arguments["id"]
    if op == "update":
        field, value = arguments["field"], arguments["value"]
        if field not in domain.ALLOWED_FIELDS:
            return Outcome(f"`{ident}`: field `{field}` not allowed")
    if op == "status" and arguments["status"] not in domain.VALID_STATUSES:
        return Outcome(f"`{ident}`: invalid status `{arguments['status']}`")
    if task is None:
        return Outcome(f"`{ident}`: not found")
    doc = deepcopy(task)
    if op == "update":
        return _update(doc, ident, field, value, lookups, now)
    if op == "status":
        return _status(doc, ident, arguments["status"], lookups, now)
    if op == "complete":
        current = doc.get("status", "todo")
        if current not in ("in-progress", "in-review", "blocked"):
            return Outcome(f"`{ident}`: cannot complete from `{current}` (expected in-progress/in-review/blocked)")
        bugs = lookups.open_bugs(ident)
        if bugs:
            return Outcome(f"`{ident}`: {len(bugs)} open bug(s) linked via found_in: {', '.join(bugs)}")
        block = domain.completion_block_reason(doc)
        if block:
            return Outcome(f"`{ident}`: {block}")
        doc["status"] = "done"
        doc["started"] = doc.get("started") or now
        if not doc.get("completed"):
            doc["completed"] = now
        _release_claim(doc, lookups)
        doc.pop("human_action", None)
        return Outcome(doc=doc, report=("status", "done", ""))
    if op == "archive":
        reason = arguments.get("reason", "done")
        already = doc.get("status") == "archived"
        doc["status"] = "archived"
        doc["archive_reason"] = reason
        _release_claim(doc, lookups)
        if not already:
            doc["archived"] = now
        return Outcome(doc=doc, report=("status", "archived", "archive_reason"), extra={"reason": reason})
    prior = doc.get("status", "todo")
    if prior not in ("todo", "in-progress", "in-review"):
        return Outcome(f"`{ident}`: is `{prior}`, expected one of: todo, in-progress, in-review")
    holder = claims.foreign_holder(doc, lookups.session)
    if holder:
        return Outcome(f"`{ident}`: {claims.batch_pick_refusal(ident, holder)}")
    doc["status"] = "in-progress"
    if not doc.get("started"):
        doc["started"] = now
    _archive_transition(doc, prior, "in-progress", now)
    return Outcome(doc=doc, report=("status", "in-progress", ""))


def _status(doc, ident, new_status, lookups, now):
    if new_status not in domain.VALID_STATUSES:
        return Outcome(f"`{ident}`: invalid status `{new_status}`")
    if new_status == "in-review" and doc.get("status") != "in-review" and not (doc.get("human_action") or "").strip():
        return Outcome(f"`{ident}`: in-review requires human_action — set it first via backlog_update_task")
    if new_status == "done":
        current = doc.get("status", "todo")
        if current not in ("in-progress", "in-review", "blocked"):
            return Outcome(f"`{ident}`: cannot complete from `{current}` (expected in-progress/in-review/blocked)")
    refusal = domain.illegal_transition_message(doc, new_status)
    if refusal:
        return Outcome(f"`{ident}`: {refusal}")
    if new_status == "done":
        bugs = lookups.open_bugs(ident)
        if bugs:
            return Outcome(f"`{ident}`: {len(bugs)} open bug(s) linked via found_in: {', '.join(bugs)}")
        block = domain.completion_block_reason(doc)
        if block:
            return Outcome(f"`{ident}`: {block}")
    prior = doc.get("status", "todo")
    doc["status"] = new_status
    if new_status == "in-progress" and not doc.get("started"):
        doc["started"] = now
    elif new_status == "done":
        doc["started"] = doc.get("started") or now
        if not doc.get("completed"):
            doc["completed"] = now
        doc.pop("human_action", None)
    _archive_transition(doc, prior, new_status, now)
    _release_claim(doc, lookups, prior)
    return Outcome(doc=doc, report=("status", new_status, ""))


def _update(doc, ident, field, value, lookups, now):
    if field == "status":
        if value not in domain.VALID_STATUSES:
            return Outcome(f"`{ident}`: invalid status `{value}`")
        if value == "in-review" and doc.get("status") != "in-review" and not (doc.get("human_action") or "").strip():
            return Outcome(f"`{ident}`: in-review requires human_action — set it first via backlog_update_task")
        refusal = domain.illegal_transition_message(doc, value)
        if refusal:
            return Outcome(f"`{ident}`: {refusal}")
        if value == "done":
            doc.pop("human_action", None)
        prior = doc.get("status", "todo")
        doc["status"] = value
        if value == "in-progress" and not doc.get("started"):
            doc["started"] = now
        elif value == "done" and not doc.get("completed"):
            doc["completed"] = now
        _archive_transition(doc, prior, value, now)
        _release_claim(doc, lookups, prior)
    elif field == "priority":
        value = domain.normalize_priority(value)
        if value not in domain.VALID_PRIORITIES:
            return Outcome(f"`{ident}`: invalid priority `{value}`")
        doc["priority"] = value
    elif field == "docs":
        if ":" not in value:
            return Outcome(f"`{ident}`: docs must be `key:path` format")
        key, path = value.split(":", 1)
        if key.strip() not in domain.VALID_DOC_KEYS:
            return Outcome(f"`{ident}`: invalid docs key `{key.strip()}`")
        if not isinstance(doc.get("docs"), dict):
            doc["docs"] = {}
        doc["docs"][key.strip()] = path.strip()
    elif field == "depends_on":
        dependencies = [d.strip() for d in value.split(",") if d.strip()]
        missing = [d for d in dependencies if not lookups.task_exists(d)]
        if missing:
            return Outcome(f"`{ident}`: dependencies not found: {', '.join(missing)}")
        doc["depends_on"] = dependencies
    elif field == "stage":
        try:
            doc["stage"] = int(value)
        except ValueError:
            return Outcome(f"`{ident}`: stage must be integer")
    elif field == "locked_by":
        return Outcome(f"`{ident}`: {claims.HOLDER_WRITE_REFUSAL}")
    elif field == "phase":
        if value == "" or value.lower() == "none":
            doc.pop("phase", None)
        else:
            phase = lookups.find_phase(value)
            if not phase:
                return Outcome(f"`{ident}`: phase `{value}` not found")
            doc["phase"] = phase["id"]
    elif field == "lane":
        if value not in VALID_LANES:
            return Outcome(f"`{ident}`: invalid lane `{value}`. Valid: {', '.join(VALID_LANES)}")
        doc["lane"] = value
        doc["gate_state"] = compute_gate_state(doc)
    elif field == "area":
        if value == "" or value.lower() == "none":
            doc.pop("area", None)
        else:
            error = lookups.area_error(value)
            if error:
                return Outcome(f"`{ident}`: {error}")
            doc["area"] = value
    else:
        doc[field] = value
    return Outcome(doc=doc, report=("field", field, _expected(doc, field)))


def apply_epic_line(epic_id, field, value, epic) -> Outcome:
    """One `update_epic` line. A status of `archived` asks for the cascade."""
    if field not in domain.ALLOWED_EPIC_FIELDS:
        return Outcome(f"epic `{epic_id}`: field `{field}` not allowed")
    if epic is None:
        return Outcome(f"epic `{epic_id}`: not found")
    if field == "status" and value not in domain.VALID_EPIC_STATUSES:
        return Outcome(f"epic `{epic_id}`: invalid status `{value}`")
    doc = deepcopy(epic)
    if field == "status" and value == "archived":
        if doc.get("status") == "archived":
            return Outcome(f"epic `{epic_id}`: already archived")
        return Outcome(doc=doc, cascade=True)
    before = str(doc.get(field, "") or "")
    doc[field] = value
    if field == "status" and before != value and before == "archived":
        doc.pop("archived", None)
        doc.pop("archive_reason", None)
    return Outcome(doc=doc, report=("field", field, _expected(doc, field)))


def _expected(doc, field):
    # Never deep-copy the sentinel: a copy is a different object, and a cleared
    # field would then read back as not persisted.
    value = doc.get(field, _MISSING)
    return value if value is _MISSING else deepcopy(value)


def missing():
    """The absent-field sentinel `Outcome.report` uses, for callers that compare."""
    return _MISSING
