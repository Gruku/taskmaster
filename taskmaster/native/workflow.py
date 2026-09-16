"""User intent: finish the native command inventory — task/epic/phase lifecycle,
gates, claims, promotions, typed links, settings and the Linear outbox — so every
N02 mutating tool has one native composite that commits in a single transaction.
Constraint: the rules come from `native.domain`, never a second copy, and nothing
here performs a remote call or a file read; Linear work is only ever enqueued.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

from taskmaster import taskmaster_v3 as domain_v3
from . import domain
from .contracts import Conflict, _identifier
from .queries import MAX_PAGE

TASK_OPERATIONS = {
    "task.create", "task.update", "task.pick", "task.complete", "task.archive",
    "task.gate", "task.gate_skip", "task.gate_clear", "task.merge",
    "task.spec_review", "task.spec_review_clear",
}
OPERATIONS = TASK_OPERATIONS | {
    "epic.create", "epic.update", "epic.archive",
    "phase.create", "phase.update", "phase.advance",
    "bug.promote", "link.create", "link.remove",
    "area.create", "area.update", "thread.update",
    "project.set", "linear.link", "linear.unlink",
}

def _text(arguments, name, *, default=""):
    value = arguments.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    return value


def _flag(arguments, name, *, default=False):
    value = arguments.get(name, default)
    if type(value) is not bool:
        raise ValueError(f"{name} must be boolean")
    return value


def _count(arguments, name, *, default=0):
    value = arguments.get(name, default)
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _string_list(arguments, name):
    value = arguments.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a list of strings")
    return value


def _keys(arguments, allowed, operation):
    if set(arguments) - allowed:
        raise ValueError(f"unknown {operation} argument")


# ── Argument admission (no database access) ─────────────────────────────────


def validate(operation, arguments):
    if operation == "task.create":
        _keys(arguments, {"title", "epic", "phase", "priority", "tldr", "notes", "next_step",
                          "depends_on", "bundle", "docs", "sub_repo", "stage", "estimate",
                          "anchors", "area", "task_id"}, operation)
        for name in ("title", "epic", "phase"):
            if not _text(arguments, name).strip():
                raise ValueError(f"{name} is required")
        for name in ("tldr", "notes", "next_step", "bundle", "sub_repo", "estimate", "area", "priority"):
            _text(arguments, name)
        _string_list(arguments, "depends_on")
        _string_list(arguments, "anchors")
        if arguments.get("task_id") is not None:
            _identifier(arguments["task_id"], "task id")
        if arguments.get("stage") is not None and type(arguments["stage"]) is not int:
            raise ValueError("stage must be an integer")
        docs = arguments.get("docs", {})
        if not isinstance(docs, dict) or not all(isinstance(v, str) for v in docs.values()):
            raise ValueError("docs must be a mapping of key to path")
        if set(docs) - domain.VALID_DOC_KEYS:
            raise ValueError(f"invalid docs key. Valid: {', '.join(sorted(domain.VALID_DOC_KEYS))}")
        if _text(arguments, "bundle") and not domain.valid_bundle_slug(arguments["bundle"]):
            raise ValueError(f"invalid bundle slug `{arguments['bundle']}` (lowercase kebab, 2-41 chars)")
    elif operation == "task.update":
        _keys(arguments, {"id", "field", "value"}, operation)
        _identifier(arguments.get("id"), "task id")
        if arguments.get("field") not in domain.ALLOWED_FIELDS:
            raise ValueError(f"field `{arguments.get('field')}` not allowed. "
                             f"Allowed: {', '.join(sorted(domain.ALLOWED_FIELDS))}")
        _text(arguments, "value")
    elif operation == "task.pick":
        _keys(arguments, {"id", "session", "force"}, operation)
        _identifier(arguments.get("id"), "task id")
        if not _text(arguments, "session").strip():
            raise ValueError("session is required to claim a task")
        _flag(arguments, "force")
    elif operation == "task.complete":
        _keys(arguments, {"id", "target_status", "human_action", "patchnote", "release", "changelog"}, operation)
        _identifier(arguments.get("id"), "task id")
        if _text(arguments, "target_status", default="done") not in ("done", "in-review"):
            raise ValueError("target_status must be 'done' or 'in-review'")
        for name in ("human_action", "patchnote", "release", "changelog"):
            _text(arguments, name)
    elif operation == "task.archive":
        _keys(arguments, {"id", "reason"}, operation)
        _identifier(arguments.get("id"), "task id")
        if _text(arguments, "reason", default="done") not in domain.VALID_ARCHIVE_REASONS:
            raise ValueError(f"invalid reason. Valid: {', '.join(sorted(domain.VALID_ARCHIVE_REASONS))}")
    elif operation == "task.gate":
        _keys(arguments, {"id", "gate", "verdict", "status", "commit_sha", "spec_path",
                          "codex_used", "critical_count", "important_count"}, operation)
        _identifier(arguments.get("id"), "task id")
        if arguments.get("gate") not in domain_v3.VALID_GATES:
            raise ValueError(f"invalid gate. Valid: {', '.join(domain_v3.VALID_GATES)}")
        for name in ("verdict", "status", "commit_sha", "spec_path"):
            _text(arguments, name)
        _flag(arguments, "codex_used")
        _count(arguments, "critical_count")
        _count(arguments, "important_count")
    elif operation == "task.gate_skip":
        _keys(arguments, {"id", "gate", "reason", "by"}, operation)
        _identifier(arguments.get("id"), "task id")
        if arguments.get("gate") not in domain_v3.VALID_GATES:
            raise ValueError(f"invalid gate. Valid: {', '.join(domain_v3.VALID_GATES)}")
        if not _text(arguments, "reason").strip():
            raise ValueError("skip_gate requires a non-empty reason (this is the audit trail)")
        _text(arguments, "by", default="claude")
    elif operation == "task.gate_clear":
        _keys(arguments, {"id", "gate"}, operation)
        _identifier(arguments.get("id"), "task id")
        if arguments.get("gate") not in domain_v3.VALID_GATES:
            raise ValueError(f"invalid gate. Valid: {', '.join(domain_v3.VALID_GATES)}")
    elif operation == "task.merge":
        _keys(arguments, {"id", "rung", "sha", "merged_at", "merge_targets"}, operation)
        _identifier(arguments.get("id"), "task id")
        if not _text(arguments, "rung").strip():
            raise ValueError("rung is required")
        if not _text(arguments, "sha").strip():
            raise ValueError("sha is required")
        _text(arguments, "merged_at")
        targets = arguments.get("merge_targets", [])
        if not isinstance(targets, list) or not all(isinstance(t, dict) for t in targets):
            raise ValueError("merge_targets must be a list of resolved rung objects")
    elif operation == "task.spec_review":
        _keys(arguments, {"id", "verdict", "spec_path", "codex_used", "critical_count", "important_count"}, operation)
        _identifier(arguments.get("id"), "task id")
        if arguments.get("verdict") not in domain_v3.VALID_GATE_VERDICTS:
            raise ValueError(f"invalid verdict. Valid: {', '.join(domain_v3.VALID_GATE_VERDICTS)}")
        _text(arguments, "spec_path")
        _flag(arguments, "codex_used")
        _count(arguments, "critical_count")
        _count(arguments, "important_count")
    elif operation == "task.spec_review_clear":
        _keys(arguments, {"id"}, operation)
        _identifier(arguments.get("id"), "task id")
    elif operation == "epic.create":
        _keys(arguments, {"epic_id", "name", "done_when", "description", "status", "area"}, operation)
        _identifier(arguments.get("epic_id"), "epic id")
        for name in ("name", "done_when", "description", "area"):
            _text(arguments, name)
        if _text(arguments, "status", default="planned") not in domain.VALID_EPIC_STATUSES:
            raise ValueError(f"invalid status. Valid: {', '.join(sorted(domain.VALID_EPIC_STATUSES))}")
    elif operation == "epic.update":
        _keys(arguments, {"id", "field", "value"}, operation)
        _identifier(arguments.get("id"), "epic id")
        if arguments.get("field") not in domain.ALLOWED_EPIC_FIELDS:
            raise ValueError(f"field not allowed. Allowed: {', '.join(sorted(domain.ALLOWED_EPIC_FIELDS))}")
        _text(arguments, "value")
    elif operation == "epic.archive":
        _keys(arguments, {"id", "reason"}, operation)
        _identifier(arguments.get("id"), "epic id")
        if _text(arguments, "reason", default="done") not in domain.VALID_ARCHIVE_REASONS:
            raise ValueError(f"invalid reason. Valid: {', '.join(sorted(domain.VALID_ARCHIVE_REASONS))}")
    elif operation == "phase.create":
        _keys(arguments, {"phase_id", "name", "description", "order", "target_date", "start_date"}, operation)
        _identifier(arguments.get("phase_id"), "phase id")
        for name in ("name", "description", "target_date", "start_date"):
            _text(arguments, name)
        if arguments.get("order") is not None and type(arguments["order"]) is not int:
            raise ValueError("order must be an integer")
    elif operation == "phase.update":
        _keys(arguments, {"id", "field", "value"}, operation)
        _identifier(arguments.get("id"), "phase id")
        if arguments.get("field") not in domain.ALLOWED_PHASE_FIELDS:
            raise ValueError(f"field not allowed. Allowed: {', '.join(sorted(domain.ALLOWED_PHASE_FIELDS))}")
        _text(arguments, "value")
    elif operation == "phase.advance":
        _keys(arguments, {"force"}, operation)
        _flag(arguments, "force")
    elif operation == "bug.promote":
        _keys(arguments, {"bug_ids", "title", "severity", "evidence_text", "components", "body"}, operation)
        if not _string_list(arguments, "bug_ids"):
            raise ValueError("bug_ids must be non-empty")
        for ident in arguments["bug_ids"]:
            _identifier(ident, "bug id")
        if not _text(arguments, "title").strip():
            raise ValueError("title is required")
        if arguments.get("severity") not in domain_v3.ISSUE_SEVERITIES:
            raise ValueError(f"invalid severity. Valid: {', '.join(domain_v3.ISSUE_SEVERITIES)}")
        if not _text(arguments, "evidence_text").strip():
            raise ValueError("evidence_text is required (cite recurrence/systemic/outstanding)")
        _text(arguments, "body")
        if arguments.get("components") is not None:
            _string_list(arguments, "components")
    elif operation in ("link.create", "link.remove"):
        _keys(arguments, {"source", "target", "type", "note"} if operation == "link.create"
              else {"source", "target", "type"}, operation)
        _identifier(arguments.get("source"), "link source")
        _identifier(arguments.get("target"), "link target")
        link_type = _text(arguments, "type")
        if operation == "link.create" and link_type not in domain_v3.LINK_TYPES:
            raise ValueError(f"invalid link type {link_type!r} (valid: {sorted(domain_v3.LINK_TYPES)})")
        if operation == "link.remove" and link_type and link_type not in domain_v3.LINK_TYPES:
            raise ValueError(f"invalid link type {link_type!r}")
        _text(arguments, "note")
    elif operation == "area.create":
        _keys(arguments, {"area_id", "name", "description", "anchors"}, operation)
        _identifier(arguments.get("area_id"), "area id")
        for name in ("name", "description"):
            _text(arguments, name)
        _string_list(arguments, "anchors")
    elif operation == "area.update":
        _keys(arguments, {"id", "field", "value"}, operation)
        _identifier(arguments.get("id"), "area id")
        if arguments.get("field") not in domain.ALLOWED_AREA_FIELDS:
            raise ValueError(f"field not allowed. Allowed: {', '.join(sorted(domain.ALLOWED_AREA_FIELDS))}")
        _text(arguments, "value")
    elif operation == "thread.update":
        _keys(arguments, {"name", "status", "reason"}, operation)
        if not _text(arguments, "name").strip():
            raise ValueError("thread name is required")
        if arguments.get("status") not in domain_v3.THREAD_STATUSES:
            raise ValueError(f"status must be one of {domain_v3.THREAD_STATUSES}")
        _text(arguments, "reason")
    elif operation == "project.set":
        _keys(arguments, {"document", "create_only"}, operation)
        if not isinstance(arguments.get("document"), dict):
            raise ValueError("project.set requires a manifest object")
        _flag(arguments, "create_only")
    elif operation == "linear.link":
        _keys(arguments, {"task_id", "external_key", "workspace_alias"}, operation)
        _identifier(arguments.get("task_id"), "task id")
        if not _text(arguments, "external_key").strip():
            raise ValueError("external_key is required")
        if not _text(arguments, "workspace_alias").strip():
            raise ValueError("workspace_alias is required; the adapter resolves the default")
    elif operation == "linear.unlink":
        _keys(arguments, {"task_id"}, operation)
        _identifier(arguments.get("task_id"), "task id")
    else:
        raise ValueError(f"unsupported operation: {operation}")


# ── Bounded lookups over the command's own snapshot ─────────────────────────


def _page(snapshot, kind, **filters):
    items, cursor = [], None
    while True:
        result = snapshot.list(kind, fields=None, limit=MAX_PAGE, cursor=cursor, **filters)
        items.extend(result["items"])
        cursor = result["cursor"]
        if cursor is None:
            return items


def _exists(transaction, kind, ident):
    try:
        transaction.snapshot.get(kind, ident, fields=[])
    except KeyError:
        return False
    return True


def _entity(transaction, kind, ident):
    return transaction.snapshot.get(kind, ident, include_body=True)


def _bugs_found_in(connection, task_id):
    """Open/fixed live bug ids whose `found_in` matches, case-insensitively."""
    rows = connection.execute(
        "SELECT c.public_id,json_extract(c.status_json,'$') FROM entity_extensions x "
        "JOIN entity_core c ON c.entity_key=x.entity_key "
        "WHERE x.field='found_in' AND c.kind='bug' AND c.deleted=0 AND c.archived=0 "
        "AND lower(json_extract(x.value_json,'$'))=lower(?) ORDER BY c.public_id", (task_id,)).fetchall()
    return ([ident for ident, status in rows if status == "open"],
            [ident for ident, status in rows if status == "fixed"])


def _bundle_members(connection, slug):
    return [row[0] for row in connection.execute(
        "SELECT c.public_id FROM memberships m JOIN entity_core c ON c.entity_key=m.entity_key "
        "WHERE m.field='bundle' AND c.kind='task' AND c.deleted=0 AND json_extract(m.value_json,'$')=? "
        "ORDER BY c.public_id", (slug,)).fetchall()]


def _terminal_task_ids(connection):
    return {row[0] for row in connection.execute(
        "SELECT public_id FROM entity_core WHERE kind='task' AND deleted=0 "
        "AND json_extract(status_json,'$') IN ('done','archived')")}


# ── Side effects that ride the same transaction ─────────────────────────────


def _enqueue_linear(transaction, task_id, task):
    """Queue one Linear push next to the mutation that dirtied the task.

    No network call and no config read happens here: the only trigger is a
    tracker the database already holds, so a rolled-back edit cannot leave a
    push queued and a committed edit cannot lose one.
    """
    tracker_id = task.get("tracker_id")
    if not tracker_id or not str(tracker_id).startswith("linear-"):
        return
    if not _exists(transaction, "tracker", str(tracker_id)):
        return
    connection = transaction.connection
    existing = connection.execute(
        "SELECT seq,tracker_id,payload FROM linear_queue WHERE op='task_upsert' AND target_id=? AND state='pending'",
        (task_id,)).fetchone()
    payload = json.dumps({"enqueued_at": domain.now_stamp()})
    if existing:
        seq, stored_tracker, stored_payload = existing
        connection.execute("UPDATE linear_queue SET tracker_id=?,payload=? WHERE seq=?",
                           (stored_tracker or tracker_id, stored_payload or payload, seq))
        return
    connection.execute("INSERT INTO linear_queue(op,target_id,tracker_id,payload,state,attempts,last_error) "
                       "VALUES('task_upsert',?,?,?,'pending',0,NULL)", (task_id, str(tracker_id), payload))


# The changelog paragraph is text that exists nowhere else, so it commits with
# the transition that produced it rather than waiting on a best-effort file write.
# It lands in native `sync_state`, not the legacy `meta` row: `meta` is the legacy
# authority whose every write invalidates a staging snapshot. The N11 exporter
# drains this key; until then the legacy row stays the exporter's own source.
PROGRESS_LOG_KEY = "pending_progress_log"


def _queue_progress_log(connection, entry):
    row = connection.execute("SELECT value_json FROM sync_state WHERE key=?", (PROGRESS_LOG_KEY,)).fetchone()
    pending = json.loads(row[0]) if row else []
    if not isinstance(pending, list):
        pending = []
    pending.append({"ts": datetime.now(timezone.utc).isoformat(), "text": entry})
    connection.execute("INSERT INTO sync_state(key,value_json) VALUES(?,?) "
                       "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                       (PROGRESS_LOG_KEY, json.dumps(pending)))


def _write_task(transaction, ident, doc, body, *, before_entity, enqueue=True):
    transaction.replace("task", ident, doc, body, before_entity=before_entity)
    if enqueue:
        _enqueue_linear(transaction, ident, doc)


def _auto_link(transaction, kind, ident, doc, body):
    """Add `references` links for inline mentions, plus each target's inverse.

    Mirrors `taskmaster_v3.auto_link_on_save` without touching the filesystem:
    the reference targets are resolved against this command's own snapshot.
    Returns the document, possibly with new links.
    """
    if doc.get("auto_link") is False:
        return doc
    if kind == "task":
        text = "\n\n".join(filter(None, [body or "", doc.get("notes") or "",
                                         doc.get("review_instructions") or ""]))
    else:
        text = body or ""
    references = domain_v3.extract_inline_refs(text, self_id=ident)
    if not references:
        return doc
    existing = {link["target"] for link in domain_v3.entity_links(doc)}
    doc = deepcopy(doc)
    added = []
    for target in references:
        if target in existing:
            continue
        target_kind = domain_v3.entity_kind_of(target)
        if target_kind is None or not _exists(transaction, target_kind, target):
            continue
        domain_v3.add_link(doc, "references", target)
        added.append((target_kind, target))
    for target_kind, target in added:
        _write_inverse(transaction, target_kind, target, source=ident, link_type="references")
    return doc


def _write_inverse(transaction, target_kind, target, *, source, link_type, remove=False):
    inverse = domain_v3.REVERSE_TYPE[link_type]
    entity = _entity(transaction, target_kind, target)
    doc = deepcopy(entity["fields"])
    changed = (domain_v3.remove_link(doc, inverse, source) if remove
               else domain_v3.add_link(doc, inverse, source))
    if changed:
        transaction.replace(target_kind, target, doc, entity["body"], before_entity=entity)


def _smart_close_handovers(transaction, task_id):
    """Close or flag the open handovers a terminal task belongs to, atomically."""
    terminal = _terminal_task_ids(transaction.connection) | {task_id}
    # The planner only carries the body through untouched, so it is never read
    # here: fetching every handover's prose to close one would be a full scan.
    rows = [(item["id"], item["fields"], None) for item in _page(transaction.snapshot, "handover")]
    plan = domain_v3.smart_auto_close_handovers(rows, triggering_task_id=task_id,
                                                done_or_archived_ids=terminal)
    for ident, updated, _body in plan["closed"] + plan["flagged"]:
        entity = _entity(transaction, "handover", ident)
        transaction.replace("handover", ident, updated, entity["body"], before_entity=entity)


# ── Command application ─────────────────────────────────────────────────────


def apply(transaction, operation, arguments):
    handler = _HANDLERS[operation]
    return handler(transaction, arguments)


def _task_create(transaction, arguments):
    epic = _entity(transaction, "epic", arguments["epic"])
    phase_id = arguments["phase"]
    if not _exists(transaction, "phase", phase_id):
        raise KeyError(f"phase `{phase_id}` not found")
    for dependency in arguments.get("depends_on", []):
        if not _exists(transaction, "task", dependency):
            raise KeyError(f"dependency `{dependency}` not found")
    area = arguments.get("area", "")
    if area and not _exists(transaction, "area", area):
        raise KeyError(f"area `{area}` not found")
    bundle = arguments.get("bundle", "")
    sub_repo = arguments.get("sub_repo", "")
    if bundle:
        _assert_bundle_repo(transaction, bundle, sub_repo)
    siblings = _page(transaction.snapshot, "task", epic=arguments["epic"], include_archived=True)
    orders = []
    for sibling in siblings:
        try:
            orders.append(float(sibling["fields"]["order"]))
        except (KeyError, TypeError, ValueError):
            continue
    doc = domain.build_task_doc(
        task_id=None, title=arguments["title"], epic=epic["fields"]["id"], phase=phase_id,
        priority=arguments.get("priority", "medium"), tldr=arguments.get("tldr", ""),
        notes=arguments.get("notes", ""), next_step=arguments.get("next_step", ""),
        depends_on=arguments.get("depends_on", []), bundle=bundle, docs=arguments.get("docs"),
        sub_repo=sub_repo, stage=arguments.get("stage"), estimate=arguments.get("estimate", ""),
        anchors=arguments.get("anchors", []), area=area,
        order=(max(orders) + 1.0) if orders else 1.0)
    ident = transaction.create("task", doc, None, requested_id=arguments.get("task_id"))
    _enqueue_linear(transaction, ident, {**doc, "id": ident})
    return ident


def _assert_bundle_repo(transaction, slug, sub_repo, *, exclude=""):
    for member in _bundle_members(transaction.connection, slug):
        if member == exclude:
            continue
        other = _entity(transaction, "task", member)["fields"].get("sub_repo", "") or ""
        if other != (sub_repo or ""):
            raise ValueError(f"bundle `{slug}` sub_repo mismatch with member `{member}` "
                             f"(one worktree = one repo)")


def _task_update(transaction, arguments):
    ident, field, value = arguments["id"], arguments["field"], arguments.get("value", "")
    entity = _entity(transaction, "task", ident)
    task = domain.touch(deepcopy(entity["fields"]))
    body = entity["body"]
    if field == "status":
        if value not in domain.VALID_STATUSES:
            raise ValueError(f"invalid status `{value}`. Valid: {', '.join(sorted(domain.VALID_STATUSES))}")
        current = task.get("status", "todo")
        if value == "in-review" and value != current and not (task.get("human_action") or "").strip():
            raise ValueError("`in-review` means blocked on a human-only action; set human_action first")
        refusal = domain.illegal_transition_message(task, value)
        if refusal:
            raise ValueError(f"`{ident}`: {refusal}")
        if task.get("lane") and value != current and value == "done":
            block = domain.completion_block_reason(task)
            if block:
                raise ValueError(block)
        if value == "done":
            task.pop("human_action", None)
        task["status"] = value
        if value == "in-progress" and not task.get("started"):
            task["started"] = domain.now_stamp()
        elif value == "done" and not task.get("completed"):
            task["completed"] = domain.now_stamp()
        _apply_archive_flag(task, before=current, after=value)
        if value != "in-progress":
            task.pop("locked_by", None)
    elif field == "priority":
        value = domain.normalize_priority(value)
        if value not in domain.VALID_PRIORITIES:
            raise ValueError(f"invalid priority `{value}`. Valid: {', '.join(domain.PRIORITY_NAMES)}")
        task["priority"] = value
    elif field == "docs":
        key, path = _doc_pair(value)
        task["docs"] = {**(task.get("docs") if isinstance(task.get("docs"), dict) else {}), key: path}
    elif field == "depends_on":
        dependencies = [item.strip() for item in value.split(",") if item.strip()]
        for dependency in dependencies:
            if not _exists(transaction, "task", dependency):
                raise KeyError(f"dependency `{dependency}` not found")
        task["depends_on"] = dependencies
    elif field == "stage":
        try:
            task["stage"] = int(value)
        except ValueError:
            raise ValueError(f"stage must be an integer, got `{value}`") from None
    elif field == "locked_by":
        if value == "" or value.lower() == "none":
            task.pop("locked_by", None)
        else:
            task["locked_by"] = value
    elif field == "phase":
        if value == "" or value.lower() == "none":
            task.pop("phase", None)
        elif not _exists(transaction, "phase", value):
            raise KeyError(f"phase `{value}` not found")
        else:
            task["phase"] = value
    elif field == "anchors":
        if value == "" or value.lower() == "none":
            task.pop("anchors", None)
        else:
            task["anchors"] = [item.strip() for item in value.split(",") if item.strip()]
    elif field in ("patchnote", "release", "next_step"):
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
            raise ValueError(f"`blast_radius_depth` must be 'shallow', 'deep', or '' to clear. Got: `{value}`")
    elif field == "tldr":
        if not value:
            raise ValueError("tldr cannot be cleared — provide a non-empty value or use autogen")
        task["tldr"] = value
        task.pop("tldr_autogen", None)
    elif field == "lane":
        if value not in domain_v3.VALID_LANES:
            raise ValueError(f"invalid lane `{value}`. Valid: {', '.join(domain_v3.VALID_LANES)}")
        task["lane"] = value
        task["gate_state"] = domain_v3.compute_gate_state(task)
    elif field == "component":
        if value == "" or value.lower() == "none":
            task.pop("component", None)
        else:
            epic = _entity(transaction, "epic", task.get("epic"))["fields"]
            components = epic.get("components") or {}
            if value not in components:
                declared = ", ".join(sorted(components)) or "(none declared)"
                raise ValueError(f"component `{value}` not declared on epic `{epic['id']}`. Declared: {declared}")
            task["component"] = value
    elif field == "design_change":
        if value.strip().lower() in ("true", "1", "yes"):
            epic = _entity(transaction, "epic", task.get("epic"))["fields"]
            if epic.get("design_status", "exploring") == "locked":
                raise ValueError(f"epic `{epic['id']}` design is locked — cannot flag a design-change task")
            task["design_change"] = True
        else:
            task.pop("design_change", None)
    elif field == "bundle":
        if not domain.valid_bundle_slug(value):
            raise ValueError(f"invalid bundle slug `{value}` (lowercase kebab, 2-41 chars)")
        if value == "":
            task.pop("bundle", None)
        else:
            _assert_bundle_repo(transaction, value, task.get("sub_repo", ""), exclude=ident)
            task["bundle"] = value
    elif field == "area":
        if value == "" or value.lower() == "none":
            task.pop("area", None)
        elif not _exists(transaction, "area", value):
            raise KeyError(f"area `{value}` not found")
        else:
            task["area"] = value
    else:
        task[field] = value
    if field in ("notes", "review_instructions"):
        task = _auto_link(transaction, "task", ident, task, body)
    _write_task(transaction, ident, task, body, before_entity=entity)
    return ident


def _doc_pair(value):
    if ":" not in value:
        raise ValueError(f"docs value must be `key:path` format. "
                         f"Valid keys: {', '.join(sorted(domain.VALID_DOC_KEYS))}")
    key, path = (part.strip() for part in value.split(":", 1))
    if key not in domain.VALID_DOC_KEYS:
        raise ValueError(f"invalid docs key `{key}`. Valid: {', '.join(sorted(domain.VALID_DOC_KEYS))}")
    return key, path


def _apply_archive_flag(doc, *, before, after):
    """Keep the document's archive markers agreeing with the status transition."""
    if after == before:
        return
    if after == "archived":
        doc.setdefault("archived", domain.now_stamp())
    elif before == "archived":
        doc.pop("archived", None)
        doc.pop("archive_reason", None)


def _task_pick(transaction, arguments):
    ident, session, force = arguments["id"], arguments["session"], arguments.get("force", False)
    entity = _entity(transaction, "task", ident)
    task = domain.touch(deepcopy(entity["fields"]))
    slug = task.get("bundle")
    if slug:
        return _bundle_pick(transaction, ident, slug, session=session, force=force)
    status = task.get("status", "todo")
    locked_by = task.get("locked_by")
    if locked_by and locked_by != session and not force:
        raise Conflict(f"task `{ident}` is locked by another session (`{locked_by}`)")
    if status not in domain.PICKABLE_FROM:
        raise ValueError(f"task `{ident}` is `{status}`, expected one of: {', '.join(domain.PICKABLE_FROM)}")
    if status == "in-progress":
        task["locked_by"] = session
    else:
        task = domain.pick_task_doc(task, session=session)
    _write_task(transaction, ident, task, entity["body"], before_entity=entity, enqueue=False)
    return ident


def _bundle_pick(transaction, ident, slug, *, session, force):
    members = _bundle_members(transaction.connection, slug)
    entities = {member: _entity(transaction, "task", member) for member in members}
    repos = {(entities[m]["fields"].get("sub_repo") or "") for m in members}
    if len(repos) > 1:
        raise ValueError(f"bundle `{slug}` spans multiple sub_repos {repos}; cannot pick")
    sub_repo = next(iter(repos), "")
    for member in members:
        holder = entities[member]["fields"].get("locked_by")
        if holder and holder != session and not force:
            raise Conflict(f"`{member}` is a member of bundle `{slug}` locked by another session ({holder})")
    branch = f"feature/{slug}"
    worktree = f"{sub_repo}/.worktrees/{slug}" if sub_repo else f".worktrees/{slug}"
    bound = all(entities[m]["fields"].get("status") == "in-progress"
                and entities[m]["fields"].get("locked_by") == session for m in members)
    if not bound:
        for member in members:
            entity = entities[member]
            doc = domain.pick_task_doc(domain.touch(deepcopy(entity["fields"])), session=session)
            doc["branch"], doc["worktree"] = branch, worktree
            _write_task(transaction, member, doc, entity["body"], before_entity=entity, enqueue=False)
    return ident


def _task_complete(transaction, arguments):
    ident = arguments["id"]
    target_status = arguments.get("target_status", "done")
    entity = _entity(transaction, "task", ident)
    task = domain.touch(deepcopy(entity["fields"]))
    status = task.get("status", "todo")
    if status not in domain.COMPLETABLE_FROM:
        raise ValueError(f"task `{ident}` is `{status}`, expected one of: {', '.join(domain.COMPLETABLE_FROM)}")
    human_action = arguments.get("human_action", "").strip()
    if target_status == "in-review":
        human_action = human_action or (task.get("human_action") or "").strip()
        if not human_action:
            raise ValueError("target_status='in-review' requires human_action — the human-only step "
                             "that blocks this task. If nothing blocks it, target 'done'.")
    open_bugs, fixed_bugs = _bugs_found_in(transaction.connection, ident)
    if open_bugs:
        raise ValueError(f"Cannot complete {ident} — {len(open_bugs)} open bug(s) linked via found_in: "
                         f"{', '.join(open_bugs)}. Resolve each (fix/adopt/shelve/promote) first.")
    if target_status == "done":
        block = domain.completion_block_reason(task)
        if block:
            raise ValueError(block)
    task = domain.complete_task_doc(task, target_status=target_status, human_action=human_action,
                                    patchnote=arguments.get("patchnote", ""),
                                    release=arguments.get("release", ""))
    _write_task(transaction, ident, task, entity["body"], before_entity=entity)
    if arguments.get("changelog", ""):
        _queue_progress_log(transaction.connection, arguments["changelog"])
    if target_status == "done":
        _smart_close_handovers(transaction, ident)
        for bug_id in fixed_bugs:
            bug = _entity(transaction, "bug", bug_id)
            try:
                domain_v3.assert_bug_archivable(bug["fields"])
            except ValueError:
                continue
            transaction.replace("bug", bug_id, dict(bug["fields"], archived=True), bug["body"],
                                before_entity=bug)
    return ident


def _task_archive(transaction, arguments):
    ident, reason = arguments["id"], arguments.get("reason", "done")
    entity = _entity(transaction, "task", ident)
    task = deepcopy(entity["fields"])
    status = task.get("status", "todo")
    if status not in domain.ARCHIVABLE_FROM:
        raise ValueError(f"task `{ident}` is `{status}`, only "
                         f"{', '.join(f'`{s}`' for s in domain.ARCHIVABLE_FROM)} tasks can be archived")
    if status == "todo" and reason == "done":
        raise ValueError("cannot archive a `todo` task with reason `done`. "
                         "Use one of: deprecated, duplicate, wont-fix, superseded")
    task = domain.archive_task_doc(task, reason=reason)
    _write_task(transaction, ident, task, entity["body"], before_entity=entity)
    _smart_close_handovers(transaction, ident)
    return ident


def _task_gate(transaction, arguments):
    ident = arguments["id"]
    entity = _entity(transaction, "task", ident)
    task = domain.record_gate_doc(
        domain.touch(deepcopy(entity["fields"])), gate=arguments["gate"],
        verdict=arguments.get("verdict", ""), status=arguments.get("status", ""),
        commit_sha=arguments.get("commit_sha", ""), spec_path=arguments.get("spec_path", ""),
        codex_used=arguments.get("codex_used", False),
        critical_count=arguments.get("critical_count", 0),
        important_count=arguments.get("important_count", 0))
    _write_task(transaction, ident, task, entity["body"], before_entity=entity, enqueue=False)
    return ident


def _task_gate_skip(transaction, arguments):
    ident = arguments["id"]
    entity = _entity(transaction, "task", ident)
    task = domain.skip_gate_doc(domain.touch(deepcopy(entity["fields"])), gate=arguments["gate"],
                                reason=arguments["reason"], by=arguments.get("by", "claude"))
    _write_task(transaction, ident, task, entity["body"], before_entity=entity, enqueue=False)
    return ident


def _task_gate_clear(transaction, arguments):
    ident = arguments["id"]
    entity = _entity(transaction, "task", ident)
    task, cleared = domain.clear_gate_doc(domain.touch(deepcopy(entity["fields"])), gate=arguments["gate"])
    if not cleared:
        return ident   # no record to clear: a no-op, not an error
    _write_task(transaction, ident, task, entity["body"], before_entity=entity, enqueue=False)
    return ident


def _task_merge(transaction, arguments):
    ident = arguments["id"]
    entity = _entity(transaction, "task", ident)
    task = domain.record_merge_doc(domain.touch(deepcopy(entity["fields"])), rung=arguments["rung"],
                                   sha=arguments["sha"], merged_at=arguments.get("merged_at", ""),
                                   merge_targets=arguments.get("merge_targets", []))
    _write_task(transaction, ident, task, entity["body"], before_entity=entity, enqueue=False)
    return ident


def _task_spec_review(transaction, arguments):
    ident = arguments["id"]
    entity = _entity(transaction, "task", ident)
    task = domain.spec_review_doc(
        domain.touch(deepcopy(entity["fields"])), verdict=arguments["verdict"],
        spec_path=arguments.get("spec_path", ""), codex_used=arguments.get("codex_used", False),
        critical_count=arguments.get("critical_count", 0),
        important_count=arguments.get("important_count", 0))
    _write_task(transaction, ident, task, entity["body"], before_entity=entity, enqueue=False)
    return ident


def _task_spec_review_clear(transaction, arguments):
    ident = arguments["id"]
    entity = _entity(transaction, "task", ident)
    task, cleared = domain.clear_spec_review_doc(domain.touch(deepcopy(entity["fields"])))
    if not cleared:
        return ident
    _write_task(transaction, ident, task, entity["body"], before_entity=entity, enqueue=False)
    return ident


def _epic_create(transaction, arguments):
    area = arguments.get("area", "")
    if area and not _exists(transaction, "area", area):
        raise KeyError(f"area `{area}` not found")
    doc = domain.build_epic_doc(epic_id=arguments["epic_id"], name=arguments.get("name", ""),
                                done_when=arguments.get("done_when", ""),
                                description=arguments.get("description", ""),
                                status=arguments.get("status", "planned"), area=area)
    return transaction.create("epic", doc, None, requested_id=arguments["epic_id"])


def _epic_update(transaction, arguments):
    ident, field, value = arguments["id"], arguments["field"], arguments.get("value", "")
    entity = _entity(transaction, "epic", ident)
    epic = deepcopy(entity["fields"])
    if field == "status":
        if value == "archived":
            raise ValueError("use epic.archive to archive an epic (it cascades to tasks)")
        if value not in domain.VALID_EPIC_STATUSES:
            raise ValueError(f"invalid epic status `{value}`. "
                             f"Valid: {', '.join(sorted(domain.VALID_EPIC_STATUSES))}")
        _apply_archive_flag(epic, before=str(epic.get("status") or ""), after=value)
        epic["status"] = value
    elif field == "docs":
        key, path = _doc_pair(value)
        docs = dict(epic.get("docs") if isinstance(epic.get("docs"), dict) else {})
        if path == "":
            docs.pop(key, None)
        else:
            docs[key] = path
        if docs:
            epic["docs"] = docs
        else:
            epic.pop("docs", None)
    elif field == "components":
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            raise ValueError("components value must be a JSON object {key: {title, after}}") from None
        error = domain.validate_components(parsed)
        if error:
            raise ValueError(error)
        epic["components"] = parsed
    elif field == "design_status":
        if value not in domain.VALID_DESIGN_STATUSES:
            raise ValueError(f"invalid design_status `{value}`. "
                             f"Valid: {', '.join(sorted(domain.VALID_DESIGN_STATUSES))}")
        epic["design_status"] = value
    elif field == "done_when":
        if not value.strip():
            raise ValueError(domain.EPIC_DONE_WHEN_REQUIRED_MSG)
        epic["done_when"] = value
    elif field == "area":
        if value and not _exists(transaction, "area", value):
            raise KeyError(f"area `{value}` not found")
        epic["area"] = value
    else:
        epic[field] = value
    transaction.replace("epic", ident, epic, entity["body"], before_entity=entity)
    return ident


def _epic_archive(transaction, arguments):
    ident, reason = arguments["id"], arguments.get("reason", "done")
    entity = _entity(transaction, "epic", ident)
    if entity["fields"].get("status") == "archived":
        raise ValueError(f"epic `{ident}` is already archived")
    stamp = domain.now_stamp()
    epic = domain.archive_epic_doc(entity["fields"], reason=reason, now=None)
    epic["archived"] = stamp
    transaction.replace("epic", ident, epic, entity["body"], before_entity=entity)
    for item in _page(transaction.snapshot, "task", epic=ident, include_archived=True):
        if item["fields"].get("status") == "archived":
            continue
        member = _entity(transaction, "task", item["id"])
        task = domain.archive_task_doc(member["fields"], reason=reason)
        task["archived"] = stamp
        _write_task(transaction, item["id"], task, member["body"], before_entity=member)
    return ident


def _phase_create(transaction, arguments):
    phases = _page(transaction.snapshot, "phase", include_archived=True)
    order = arguments.get("order")
    if order is None:
        order = max((int(p["fields"].get("order") or 0) for p in phases), default=0) + 1
    activate = not any(p["fields"].get("status") == "active" for p in phases)
    doc = domain.build_phase_doc(phase_id=arguments["phase_id"], name=arguments.get("name", ""),
                                 description=arguments.get("description", ""), order=order,
                                 target_date=arguments.get("target_date", ""),
                                 start_date=arguments.get("start_date", ""), activate=activate)
    return transaction.create("phase", doc, None, requested_id=arguments["phase_id"])


def _phase_update(transaction, arguments):
    ident, field, value = arguments["id"], arguments["field"], arguments.get("value", "")
    entity = _entity(transaction, "phase", ident)
    phase = deepcopy(entity["fields"])
    if field == "status":
        if value not in domain.VALID_PHASE_STATUSES:
            raise ValueError(f"invalid status `{value}`. "
                             f"Valid: {', '.join(sorted(domain.VALID_PHASE_STATUSES))}")
        if value == "active":
            for other in _page(transaction.snapshot, "phase", include_archived=True):
                if other["id"] != ident and other["fields"].get("status") == "active":
                    peer = _entity(transaction, "phase", other["id"])
                    transaction.replace("phase", other["id"], dict(peer["fields"], status="planned"),
                                        peer["body"], before_entity=peer)
            if not phase.get("start_date"):
                phase["start_date"] = domain.today_stamp()
        if value == "done":
            phase["completed"] = domain.now_stamp()
        before = str(phase.get("status") or "")
        phase["status"] = value
        if value == "archived":
            phase["archived"] = domain.now_stamp()
        _apply_archive_flag(phase, before=before, after=value)
    elif field == "order":
        try:
            phase["order"] = int(value)
        except ValueError:
            raise ValueError(f"order must be an integer, got `{value}`") from None
    elif field in ("target_date", "start_date"):
        if value == "":
            phase.pop(field, None)
        elif not domain.parse_date(value):
            raise ValueError(f"{field} must be YYYY-MM-DD format, got `{value}`")
        else:
            phase[field] = value
    elif field == "deliverables":
        try:
            command = json.loads(value)
        except (ValueError, TypeError):
            raise ValueError('deliverables value must be JSON — {"action": "add", "text": "..."}') from None
        if not isinstance(command, dict):
            raise ValueError('deliverables value must be JSON — {"action": "add", "text": "..."}')
        phase = domain.apply_deliverables_command(phase, command)
    elif field == "docs":
        key, path = _doc_pair(value)
        docs = dict(phase.get("docs") if isinstance(phase.get("docs"), dict) else {})
        if path == "":
            docs.pop(key, None)
        else:
            docs[key] = path
        if docs:
            phase["docs"] = docs
        else:
            phase.pop("docs", None)
    else:
        phase[field] = value
    transaction.replace("phase", ident, phase, entity["body"], before_entity=entity)
    return ident


def _phase_advance(transaction, arguments):
    phases = _page(transaction.snapshot, "phase", include_archived=True)
    active = next((p for p in phases if p["fields"].get("status") == "active"), None)
    if active is None:
        raise ValueError("no active phase to advance")
    unchecked = [d for d in (active["fields"].get("deliverables") or []) if not d.get("done")]
    if unchecked and not arguments.get("force", False):
        raise ValueError(f"blocked: {len(unchecked)} unchecked deliverable(s) in phase "
                         f"{active['fields'].get('name', active['id'])}")
    entity = _entity(transaction, "phase", active["id"])
    done = dict(entity["fields"], status="done", completed=domain.now_stamp())
    transaction.replace("phase", active["id"], done, entity["body"], before_entity=entity)
    stamp = domain.now_stamp()
    for item in _page(transaction.snapshot, "task", phase=active["id"]):
        if item["fields"].get("status") != "done":
            continue
        member = _entity(transaction, "task", item["id"])
        task = domain.archive_task_doc(member["fields"], reason="done")
        task["archived"] = stamp
        _write_task(transaction, item["id"], task, member["body"], before_entity=member)
    planned = sorted((p for p in phases if p["fields"].get("status") == "planned"),
                     key=lambda p: (p["fields"].get("order") if isinstance(p["fields"].get("order"), int) else 999,
                                    p["id"]))
    if not planned:
        return active["id"]
    following = _entity(transaction, "phase", planned[0]["id"])
    doc = dict(following["fields"], status="active")
    doc.setdefault("start_date", domain.today_stamp())
    transaction.replace("phase", planned[0]["id"], doc, following["body"], before_entity=following)
    return planned[0]["id"]


def _bug_promote(transaction, arguments):
    bug_ids = arguments["bug_ids"]
    sources = {ident: _entity(transaction, "bug", ident) for ident in bug_ids}
    components = arguments.get("components")
    if components is None:
        components = sorted({component for ident in bug_ids
                             for component in (sources[ident]["fields"].get("components") or [])})
    evidence = arguments["evidence_text"]
    issue_doc = domain_v3.build_issue_doc(title=arguments["title"], severity=arguments["severity"],
                                          impact=evidence, evidence=evidence, components=components,
                                          promoted_from=list(bug_ids))
    issue_id = transaction.create("issue", issue_doc, arguments.get("body", ""))
    for ident in bug_ids:
        source = sources[ident]
        updated = domain_v3.apply_bug_updates(source["fields"], status="promoted", promoted_to=issue_id)
        transaction.replace("bug", ident, updated, source["body"], before_entity=source)
    return issue_id


def _link_create(transaction, arguments):
    source_id, target_id = arguments["source"], arguments["target"]
    link_type, note = arguments["type"], arguments.get("note", "")
    source_kind = domain_v3.entity_kind_of(source_id)
    target_kind = domain_v3.entity_kind_of(target_id)
    if source_kind is None:
        raise ValueError(f"invalid source ID {source_id!r}")
    if target_kind is None:
        raise ValueError(f"invalid target ID {target_id!r}")
    if not domain_v3.is_valid_link(link_type, source_kind, target_kind):
        raise ValueError(f"invalid link — type {link_type!r} cannot go from "
                         f"{source_kind} ({source_id}) to {target_kind} ({target_id})")
    entity = _entity(transaction, source_kind, source_id)
    if not _exists(transaction, target_kind, target_id):
        raise KeyError(f"target {target_id!r} not found")
    if link_type in ("depends_on", "blocks"):
        _assert_no_cycle(transaction, source_id, target_id, link_type)
    doc = deepcopy(entity["fields"])
    del note   # accepted for signature parity; the tool never stored it either
    if domain_v3.add_link(doc, link_type, target_id):
        transaction.replace(source_kind, source_id, doc, entity["body"], before_entity=entity)
    _write_inverse(transaction, target_kind, target_id, source=source_id, link_type=link_type)
    return source_id


def _assert_no_cycle(transaction, source_id, target_id, link_type):
    graph = {}
    for item in _page(transaction.snapshot, "task", include_archived=True):
        ident = item["id"]
        graph.setdefault(ident, [])
        for link in item["fields"].get("links") or []:
            if link.get("type") == "depends_on":
                graph[ident].append(link["target"])
            elif link.get("type") == "blocks":
                graph.setdefault(link["target"], []).append(ident)
    start, end = (source_id, target_id) if link_type == "depends_on" else (target_id, source_id)
    if domain_v3.would_create_cycle(graph, start, end):
        raise ValueError(f"would create cycle in depends_on chain ({start} -> {end})")


def _link_remove(transaction, arguments):
    source_id, target_id = arguments["source"], arguments["target"]
    source_kind = domain_v3.entity_kind_of(source_id)
    target_kind = domain_v3.entity_kind_of(target_id)
    if source_kind is None:
        raise ValueError(f"invalid source ID {source_id!r}")
    if target_kind is None:
        raise ValueError(f"invalid target ID {target_id!r}")
    entity = _entity(transaction, source_kind, source_id)
    requested = arguments.get("type", "")
    types = [requested] if requested else sorted(
        {link["type"] for link in domain_v3.entity_links(entity["fields"]) if link["target"] == target_id})
    if not types:
        return source_id
    doc = deepcopy(entity["fields"])
    removed = any(domain_v3.remove_link(doc, link_type, target_id) for link_type in types)
    if removed:
        transaction.replace(source_kind, source_id, doc, entity["body"], before_entity=entity)
    for link_type in types:
        if _exists(transaction, target_kind, target_id):
            _write_inverse(transaction, target_kind, target_id, source=source_id,
                           link_type=link_type, remove=True)
    return source_id


def _area_create(transaction, arguments):
    doc = domain_v3.validate_area_doc({"id": arguments["area_id"], "name": arguments.get("name", ""),
                                       "description": arguments.get("description", ""),
                                       "anchors": list(arguments.get("anchors", [])),
                                       "created": domain.now_stamp()})
    return transaction.create("area", doc, "", requested_id=arguments["area_id"])


def _area_update(transaction, arguments):
    ident, field, value = arguments["id"], arguments["field"], arguments.get("value", "")
    entity = _entity(transaction, "area", ident)
    if field == "anchors":
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            raise ValueError("anchors value must be a JSON array of strings") from None
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ValueError("anchors value must be a JSON array of strings")
        updates = {"anchors": parsed}
    else:
        updates = {field: value}
    doc = domain_v3.apply_area_updates(entity["fields"], updates)
    transaction.replace("area", ident, doc, entity["body"], before_entity=entity)
    return ident


def _thread_update(transaction, arguments):
    entity = _entity(transaction, "backlog", "backlog")
    doc = deepcopy(entity["fields"])
    domain_v3.update_thread_status(doc, None, name=arguments["name"], status=arguments["status"],
                                   reason=arguments.get("reason", ""))
    transaction.replace("backlog", "backlog", doc, entity["body"], before_entity=entity)
    return arguments["name"]


def _project_set(transaction, arguments):
    document = deepcopy(arguments["document"])
    try:
        entity = _entity(transaction, "project", "__project__")
    except KeyError:
        return transaction.create("project", document, None, requested_id="__project__")
    if arguments.get("create_only", False):
        # The row read inside the writer transaction is the existence check;
        # a projection-file probe let two initializers both scaffold.
        raise ValueError("this project already has a manifest — refusing to overwrite (edit it directly)")
    transaction.replace("project", "__project__", document, entity["body"], before_entity=entity)
    return "__project__"


def _linear_link(transaction, arguments):
    task_id, external_key = arguments["task_id"], arguments["external_key"]
    alias = arguments["workspace_alias"]
    entity = _entity(transaction, "task", task_id)
    task = deepcopy(entity["fields"])
    existing = task.get("tracker_id")
    if existing:
        raise ValueError(f"task {task_id!r} already has tracker_id {existing!r} — unlink first")
    tracker_id = domain_v3.make_tracker_id("linear", alias, external_key)
    if _exists(transaction, "tracker", tracker_id):
        raise ValueError(f"tracker {tracker_id} already exists — it may be linked to another task")
    transaction.create("tracker", domain_v3.build_tracker_doc(
        external_system="linear", instance_alias=alias, external_key=external_key,
        title=task.get("title", external_key), status=task.get("status", "todo")),
        "", requested_id=tracker_id)
    task["tracker_id"] = tracker_id
    transaction.replace("task", task_id, task, entity["body"], before_entity=entity)
    return tracker_id


def _linear_unlink(transaction, arguments):
    task_id = arguments["task_id"]
    entity = _entity(transaction, "task", task_id)
    task = deepcopy(entity["fields"])
    if not task.pop("tracker_id", None):
        return task_id   # idempotent: nothing was linked
    transaction.replace("task", task_id, task, entity["body"], before_entity=entity)
    return task_id


_HANDLERS = {
    "task.create": _task_create, "task.update": _task_update, "task.pick": _task_pick,
    "task.complete": _task_complete, "task.archive": _task_archive, "task.gate": _task_gate,
    "task.gate_skip": _task_gate_skip, "task.gate_clear": _task_gate_clear,
    "task.merge": _task_merge, "task.spec_review": _task_spec_review,
    "task.spec_review_clear": _task_spec_review_clear,
    "epic.create": _epic_create, "epic.update": _epic_update, "epic.archive": _epic_archive,
    "phase.create": _phase_create, "phase.update": _phase_update, "phase.advance": _phase_advance,
    "bug.promote": _bug_promote, "link.create": _link_create, "link.remove": _link_remove,
    "area.create": _area_create, "area.update": _area_update, "thread.update": _thread_update,
    "project.set": _project_set, "linear.link": _linear_link, "linear.unlink": _linear_unlink,
}
