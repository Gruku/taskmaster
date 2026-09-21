"""User intent: one shared task/epic/phase rules layer, so the MCP tools, the
viewer HTTP path and the native command core cannot disagree about a legal
transition, a gate or an archive. Pure functions only — no database, no file
system, no clock reads other than the explicit `now` arguments callers pass in.
"""
from datetime import date, datetime
import re

from taskmaster.taskmaster_v3 import (
    TLDR_MAX_CHARS,
    VALID_GATES,
    VALID_GATE_VERDICTS,
    VERDICT_GATES,
    VALID_LANES,
    blocking_gates,
    compute_gate_state,
    compute_merge_gate_state,
    default_lane,
    extract_tldr,
    gate_satisfied,
    outstanding_required_gates,
)

PRIORITY_NAMES = ("critical", "high", "medium", "low")
LEGACY_PRIORITY_TO_NAME = {"P0": "critical", "P1": "high", "P2": "medium", "P3": "low"}
VALID_PRIORITIES = {"critical", "high", "medium", "low"}
VALID_STATUSES = {"todo", "in-progress", "in-review", "done", "archived", "blocked"}
VALID_DOC_KEYS = {"plan", "spec", "roadmap", "design", "analysis"}
VALID_ARCHIVE_REASONS = {"done", "deprecated", "duplicate", "wont-fix", "superseded"}
ALLOWED_FIELDS = {"title", "status", "priority", "notes", "branch", "worktree", "blockers", "docs",
                  "depends_on", "sub_repo", "stage", "estimate", "locked_by", "review_instructions",
                  "phase", "anchors", "blast_radius_depth", "patchnote", "release", "tldr", "next_step",
                  "component", "design_change", "lane", "bundle", "area", "human_action"}
# Spec A Task 11: forward-transition table enforced on lane'd tasks. Laneless
# tasks are exempt (old permissive behavior) except when leaving `archived`.
LEGAL_STATUS_TRANSITIONS = {
    "todo":        {"in-progress", "blocked", "archived"},
    "in-progress": {"in-review", "done", "blocked", "todo", "archived"},
    "in-review":   {"done", "in-progress", "blocked", "archived"},
    "blocked":     {"todo", "in-progress", "in-review", "archived"},
    "done":        {"in-review", "archived"},
    "archived":    {"todo"},
}
COMPLETABLE_FROM = ("in-progress", "in-review", "blocked")
PICKABLE_FROM = ("todo", "in-progress", "in-review")
ARCHIVABLE_FROM = ("done", "blocked", "todo")

VALID_EPIC_STATUSES = {"active", "planned", "done", "archived"}
ALLOWED_EPIC_FIELDS = {"name", "status", "description", "docs", "components", "design_status", "done_when", "area"}
VALID_DESIGN_STATUSES = {"exploring", "proposed", "locked", "revising"}
EPIC_DONE_WHEN_REQUIRED_MSG = (
    "Epics are finite: 'done_when' is required. "
    "An epic that can't say when it's done is an area."
)

VALID_PHASE_STATUSES = {"planned", "active", "done", "archived"}
ALLOWED_PHASE_FIELDS = {"name", "status", "description", "order", "target_date", "start_date", "deliverables", "docs"}

ALLOWED_AREA_FIELDS = {"name", "description", "anchors"}

BUNDLE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
EPIC_ID_RE = re.compile(r"^[a-z0-9]+(-?[a-z0-9]+)*$")


def now_stamp(now=None) -> str:
    """ISO timestamp with minute precision: YYYY-MM-DDTHH:MM"""
    return (now or datetime.now()).strftime("%Y-%m-%dT%H:%M")


def today_stamp(now=None) -> str:
    return (now.date() if isinstance(now, datetime) else now or date.today()).isoformat()


def normalize_priority(value: str) -> str:
    """Accept both the legacy P0-P3 codes and the canonical names."""
    if value in PRIORITY_NAMES:
        return value
    return LEGACY_PRIORITY_TO_NAME.get(value, value)


def valid_bundle_slug(value: str) -> bool:
    """Empty string clears the bundle (descope); otherwise lowercase kebab."""
    return value == "" or bool(BUNDLE_SLUG_RE.match(value))


def valid_kebab_id(value: str) -> bool:
    return bool(value) and value == value.lower() and all(c.isalnum() or c == "-" for c in value)


def parse_date(value: str):
    """Parse a YYYY-MM-DD string, returning the date or None when invalid."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def find_phase(phases, phase_id):
    """A phase by id (exact), then normalized name, then either name containing the other."""
    for phase in phases:
        if phase["id"] == phase_id:
            return phase
    needle = phase_id.strip().lower().replace("-", " ").replace("_", " ")
    for phase in phases:
        name = phase.get("name", "").strip().lower().replace("-", " ").replace("_", " ")
        if name == needle:
            return phase
    for phase in phases:
        name = phase.get("name", "").strip().lower().replace("-", " ").replace("_", " ")
        if needle in name or name in needle:
            return phase
    return None


def strictest_lane(lanes) -> str:
    order = {"express": 0, "standard": 1, "full": 2}
    present = [lane for lane in lanes if lane in order] or ["standard"]
    return max(present, key=lambda lane: order[lane])


def illegal_transition_message(task, after):
    """Why moving `task` to `after` is refused, or None when it is allowed.

    One rule, applied by the MCP tools, by batch, by the viewer and by the
    native core, so no caller can make a move another one refuses.

    - a same-status write is never a transition, and is always allowed;
    - a task with a lane is held to the full `LEGAL_STATUS_TRANSITIONS` table;
    - a laneless (pre-lane, grandfathered) task keeps the permissive behaviour,
      except that it may still only leave `archived` the way the table says.
    """
    before = (task or {}).get("status", "todo")
    if after is None or after == before:
        return None
    if before != "archived" and not (task or {}).get("lane"):
        return None
    legal = LEGAL_STATUS_TRANSITIONS.get(before, set())
    if after in legal:
        return None
    return (
        f"illegal transition `{before}` → `{after}`. "
        f"Legal: {', '.join(sorted(legal)) or '(none)'}"
    )


def completion_block_reason(task) -> str:
    """Why a lane'd task may not reach `done` yet, or '' when it may."""
    if not task.get("lane"):
        return ""   # laneless => exempt (Spec A rollout rule)
    outstanding = outstanding_required_gates(task)
    if outstanding:
        return (f"Cannot complete `{task['id']}` — outstanding gates for lane "
                f"`{task['lane']}`: {', '.join(outstanding)}. "
                f"Record each (backlog_record_gate) or skip it (backlog_skip_gate).")
    return ""


def validate_components(components) -> str:
    """'' if the epic components block is well-formed, else the error string.

    Shape: { <key>: { "title": str, "after": [<other keys>] } }.
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


# ── Pure document transforms ────────────────────────────────────────────────


def build_task_doc(*, task_id, title, epic, phase, priority="medium", tldr="", notes="",
                   next_step="", depends_on=(), bundle="", docs=None, sub_repo="", stage=None,
                   estimate="", anchors=(), area="", order=None, now=None):
    """The create-time task document, including lane and gate mirrors."""
    stamp = now_stamp(now)
    priority = normalize_priority(priority)
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority `{priority}`. Valid: {', '.join(PRIORITY_NAMES)}")
    autogen = not tldr
    if autogen:
        tldr = extract_tldr(notes or title) or title[:TLDR_MAX_CHARS]
    doc = {"id": task_id, "title": title, "tldr": tldr, "status": "todo", "priority": priority,
           "created": stamp, "last_referenced": stamp, "notes": notes}
    if autogen:
        doc["tldr_autogen"] = True
    if next_step:
        doc["next_step"] = next_step
    if depends_on:
        doc["depends_on"] = list(depends_on)
    if sub_repo:
        doc["sub_repo"] = sub_repo
    if stage is not None:
        doc["stage"] = stage
    if estimate:
        doc["estimate"] = estimate
    doc["phase"] = phase
    if anchors:
        doc["anchors"] = list(anchors)
    if bundle:
        doc["bundle"] = bundle
    if area:
        doc["area"] = area
    if docs:
        doc["docs"] = dict(docs)
    doc["lane"] = default_lane(doc["priority"])
    doc["gate_state"] = compute_gate_state(doc)
    doc["merge_gate_state"] = ""   # no merges yet
    doc["epic"] = epic
    doc["order"] = 1.0 if order is None else order
    return doc


def touch(doc, now=None):
    doc["last_referenced"] = now_stamp(now)
    return doc


def pick_task_doc(task, *, session, now=None):
    """Claim a task for `session`. Caller has already checked lock eligibility."""
    doc = dict(task)
    doc["status"] = "in-progress"
    doc["started"] = doc.get("started") or now_stamp(now)
    doc["locked_by"] = session
    return doc


def complete_task_doc(task, *, target_status, human_action="", patchnote="", release="", now=None,
                      keep_holder=False):
    """`keep_holder`: to `in-review` a live peer's claim survives
    (`claims.survives_status_change`); to `done` no claim does."""
    doc = dict(task)
    doc["status"] = target_status
    if target_status == "done":
        doc["completed"] = now_stamp(now)
        doc.pop("human_action", None)
    else:
        doc["human_action"] = human_action
    if not keep_holder or target_status == "done":
        doc.pop("locked_by", None)
    if patchnote:
        doc["patchnote"] = patchnote
    if release:
        doc["release"] = release
    return doc


def archive_task_doc(task, *, reason, now=None):
    """Archived is terminal, so the claim is released whoever archives it."""
    doc = dict(task)
    doc["status"] = "archived"
    doc["archive_reason"] = reason
    doc["archived"] = now_stamp(now)
    doc.pop("locked_by", None)
    return doc


def record_gate_doc(task, *, gate, verdict="", status="", commit_sha="", spec_path="",
                    codex_used=False, critical_count=0, important_count=0, now=None):
    """Stamp one gate record and refresh the slim `gate_state` mirror."""
    if gate not in VALID_GATES:
        raise ValueError(f"invalid gate `{gate}`. Valid: {', '.join(VALID_GATES)}")
    is_verdict = gate in VERDICT_GATES
    if is_verdict:
        if verdict not in VALID_GATE_VERDICTS:
            raise ValueError(f"gate `{gate}` requires verdict in "
                             f"{', '.join(VALID_GATE_VERDICTS)}, got `{verdict or '(none)'}`")
    elif status != "done":
        raise ValueError(f"status gate `{gate}` requires status=\"done\", got `{status or '(none)'}`")
    lane = task.get("lane")
    if lane and is_verdict:
        required = blocking_gates(lane)
        recorded = task.get("gates") or {}
        if gate in required:
            for earlier in required[:required.index(gate)]:
                if not gate_satisfied(recorded.get(earlier)):
                    raise ValueError(f"cannot record `{gate}` for `{task['id']}` — earlier required "
                                     f"gate `{earlier}` is not satisfied (pass/skipped). "
                                     f"Record or skip it first.")
    record = {"at": now_stamp(now)}
    if is_verdict:
        record["verdict"] = verdict
        if commit_sha:
            record["commit_sha"] = commit_sha
        if spec_path:
            record["spec_path"] = spec_path
        record["codex_used"] = bool(codex_used)
        record["critical_count"] = int(critical_count)
        record["important_count"] = int(important_count)
    else:
        record["status"] = "done"
        if commit_sha:
            record["commit_sha"] = commit_sha
    doc = dict(task)
    doc["gates"] = {**(task.get("gates") or {}), gate: record}
    doc["gate_state"] = compute_gate_state(doc)
    return doc


def skip_gate_doc(task, *, gate, reason, by="claude", now=None):
    if gate not in VALID_GATES:
        raise ValueError(f"invalid gate `{gate}`. Valid: {', '.join(VALID_GATES)}")
    if not (reason or "").strip():
        raise ValueError("skip_gate requires a non-empty reason (this is the audit trail)")
    doc = dict(task)
    doc["gates"] = {**(task.get("gates") or {}),
                    gate: {"skipped": True, "reason": reason.strip(), "by": by, "at": now_stamp(now)}}
    doc["gate_state"] = compute_gate_state(doc)
    return doc


def clear_gate_doc(task, *, gate):
    """Return (doc, cleared). `cleared` is False when the task had no such record."""
    if gate not in VALID_GATES:
        raise ValueError(f"invalid gate `{gate}`. Valid: {', '.join(VALID_GATES)}")
    gates = dict(task.get("gates") or {})
    if gate not in gates:
        return dict(task), False
    del gates[gate]
    doc = dict(task)
    doc["gates"] = gates
    doc["gate_state"] = compute_gate_state(doc)
    return doc, True


def record_merge_doc(task, *, rung, sha, merge_targets, merged_at="", now=None):
    if not (rung or "").strip():
        raise ValueError("rung is required")
    if not (sha or "").strip():
        raise ValueError("sha is required")
    doc = dict(task)
    doc["merge_status"] = {**(task.get("merge_status") or {}),
                           rung: {"merged_at": merged_at or now_stamp(now), "merge_commit": sha}}
    doc["merge_gate_state"] = compute_merge_gate_state(doc, merge_targets)
    return doc


def spec_review_doc(task, *, verdict, spec_path, codex_used=False, critical_count=0,
                    important_count=0, now=None):
    """`record_gate_doc` for spec-review plus the legacy `spec_review` mirror."""
    doc = record_gate_doc(task, gate="spec-review", verdict=verdict, spec_path=spec_path,
                          codex_used=codex_used, critical_count=critical_count,
                          important_count=important_count, now=now)
    doc["spec_review"] = {"timestamp": now_stamp(now), "verdict": verdict,
                          "codex_used": bool(codex_used), "critical_count": int(critical_count),
                          "important_count": int(important_count), "spec_path": spec_path}
    return doc


def clear_spec_review_doc(task):
    doc, cleared = clear_gate_doc(task, gate="spec-review")
    if "spec_review" in doc:
        doc = dict(doc)
        del doc["spec_review"]
        cleared = True
    return doc, cleared


def build_epic_doc(*, epic_id, name, done_when, description="", status="planned", area="", now=None):
    if not (done_when or "").strip():
        raise ValueError(EPIC_DONE_WHEN_REQUIRED_MSG)
    if status not in VALID_EPIC_STATUSES:
        raise ValueError(f"invalid status `{status}`. Valid: {', '.join(sorted(VALID_EPIC_STATUSES))}")
    if not valid_kebab_id(epic_id):
        raise ValueError(f"epic_id must be lowercase kebab-case (e.g., 'auth-system'), got `{epic_id}`")
    doc = {"id": epic_id, "name": name, "status": status, "description": description,
           "created": now_stamp(now), "done_when": done_when}
    if area:
        doc["area"] = area
    return doc


def archive_epic_doc(epic, *, reason, now=None):
    doc = dict(epic)
    doc["status"] = "archived"
    doc["archive_reason"] = reason
    doc["archived"] = now_stamp(now)
    return doc


def build_phase_doc(*, phase_id, name, description="", order=None, target_date="",
                    start_date="", activate=False, now=None):
    if not valid_kebab_id(phase_id):
        raise ValueError(f"phase_id must be lowercase kebab-case (e.g., 'foundation', 'mvp'), got `{phase_id}`")
    if target_date and not parse_date(target_date):
        raise ValueError(f"target_date must be YYYY-MM-DD format, got `{target_date}`")
    if start_date and not parse_date(start_date):
        raise ValueError(f"start_date must be YYYY-MM-DD format, got `{start_date}`")
    status = "active" if activate else "planned"
    doc = {"id": phase_id, "name": name, "status": status, "description": description,
           "order": order, "created": now_stamp(now)}
    if target_date:
        doc["target_date"] = target_date
    if start_date:
        doc["start_date"] = start_date
    elif status == "active":
        doc["start_date"] = today_stamp(now)
    return doc


def apply_deliverables_command(phase, command):
    """Apply one `{"action": …}` deliverables command, returning the new doc."""
    doc = dict(phase)
    deliverables = [dict(item) for item in (phase.get("deliverables") or [])]
    action = command.get("action", "")
    if action == "add":
        text = str(command.get("text", "")).strip()
        if not text:
            raise ValueError("deliverable text is required")
        deliverables.append({"text": text, "done": False})
    elif action in ("remove", "toggle"):
        index = command.get("index")
        if type(index) is not int or index < 0 or index >= len(deliverables):
            raise ValueError(f"invalid index {index} — phase has {len(deliverables)} deliverables")
        if action == "remove":
            deliverables.pop(index)
        else:
            deliverables[index]["done"] = not deliverables[index]["done"]
    elif action == "set":
        deliverables = [{"text": str(item.get("text", "")), "done": bool(item.get("done", False))}
                        for item in command.get("items", [])]
    else:
        raise ValueError(f"unknown deliverables action `{action}`. Use: add, remove, toggle, set")
    doc["deliverables"] = deliverables
    return doc
