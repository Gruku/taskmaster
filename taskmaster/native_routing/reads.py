# User intent: give adapters the legacy-shaped documents their shared presentation
# code expects (a task with `_body`, normalized priority/created, its epic) from
# bounded native snapshot queries, never from the whole-backlog compatibility dict.
"""Legacy-shaped reads over one native snapshot."""
from __future__ import annotations

from copy import deepcopy

from taskmaster import backlog_server as bs
from taskmaster.native.queries import MAX_PAGE
from taskmaster.taskmaster_v3 import BODY_KEY


def page(snapshot, kind, **filters) -> list[dict]:
    items, cursor = [], None
    while True:
        result = snapshot.list(kind, fields=None, limit=MAX_PAGE, cursor=cursor, **filters)
        items.extend(result["items"])
        cursor = result["cursor"]
        if cursor is None:
            return items


def get(snapshot, kind, ident, *, body=False):
    try:
        return snapshot.get(kind, ident, include_body=body)
    except KeyError:
        return None


def document(entity, *, body=True) -> dict:
    """An entity as the legacy dict carried it: fields, plus `_body` when it has prose."""
    doc = deepcopy(entity["fields"])
    if body and entity.get("body"):
        doc[BODY_KEY] = entity["body"]
    return doc


def legacy_task(entity) -> dict:
    return bs._normalize_task(document(entity))


def epic_of(snapshot, task_fields) -> dict | None:
    epic_id = task_fields.get("epic")
    if not isinstance(epic_id, str) or not epic_id:
        return None
    entity = get(snapshot, "epic", epic_id, body=True)
    return document(entity) if entity is not None else None


def find_task(snapshot, task_id) -> "tuple[dict, dict] | None":
    """`(task, epic)` the way `_find_task` answers: a task under a missing epic is not found."""
    entity = get(snapshot, "task", task_id, body=True)
    if entity is None:
        return None
    epic = epic_of(snapshot, entity["fields"])
    if epic is None:
        return None
    return legacy_task(entity), epic


def epic_tasks(snapshot, epic_id) -> list[dict]:
    """The epic's task list in the legacy dict's order (order, then id)."""
    tasks = [legacy_task(entity) for entity in page(snapshot, "task", epic=epic_id, include_archived=True)]
    tasks.sort(key=lambda task: (float(task.get("order", 0.0)), str(task.get("id", ""))))
    return tasks


def epics(snapshot) -> list[dict]:
    """Every epic in the legacy dict's order (creation order)."""
    items = page(snapshot, "epic", include_archived=True)
    keys = {row[1]: row[0] for row in snapshot.connection.execute(
        "SELECT entity_key,public_id FROM entity_core WHERE kind='epic' AND deleted=0")}
    items.sort(key=lambda entity: keys[entity["id"]])
    return [document(entity) for entity in items]


def phases(snapshot) -> list[dict]:
    items = page(snapshot, "phase", include_archived=True)
    keys = {row[1]: row[0] for row in snapshot.connection.execute(
        "SELECT entity_key,public_id FROM entity_core WHERE kind='phase' AND deleted=0")}
    items.sort(key=lambda entity: keys[entity["id"]])
    return [document(entity) for entity in items]


def find_phase(snapshot, phase_id) -> dict | None:
    """`_find_phase`: exact id, then normalized name, then substring match."""
    return bs._find_phase({"phases": phases(snapshot)}, phase_id)


def area_ids(snapshot) -> list[str]:
    return sorted(entity["id"] for entity in page(snapshot, "area", include_archived=True))


def validate_area_ref(snapshot, area) -> str | None:
    known = area_ids(snapshot)
    if area not in known:
        return f"Error: unknown area `{area}`. Valid: {', '.join(known) or '(none defined)'}"
    return None


def bundle_members(snapshot, slug) -> list[dict]:
    """`_find_tasks_by_bundle`: live members across epics that exist."""
    rows = snapshot.connection.execute(
        "SELECT c.public_id FROM memberships m JOIN entity_core c ON c.entity_key=m.entity_key "
        "WHERE m.field='bundle' AND c.kind='task' AND c.deleted=0 "
        "AND json_extract(m.value_json,'$')=? ORDER BY c.public_id", (slug,)).fetchall()
    members = []
    for (ident,) in rows:
        found = find_task(snapshot, ident)
        if found is not None and found[0].get("status") != "archived" and found[0].get("bundle") == slug:
            members.append(found)
    members.sort(key=lambda pair: (_epic_rank(snapshot, pair[1]["id"]),
                                   float(pair[0].get("order", 0.0)), str(pair[0].get("id", ""))))
    return [task for task, _epic in members]


def _epic_rank(snapshot, epic_id):
    row = snapshot.connection.execute(
        "SELECT entity_key FROM entity_core WHERE kind='epic' AND public_id=?", (epic_id,)).fetchone()
    return row[0] if row else 0


def rows(snapshot, kind, *, include_archived=False) -> list[tuple[str, dict, str]]:
    """`_dict_rows`: `(id, doc, body)` sorted by id, archived rows only on request."""
    out = []
    ids = [entity["id"] for entity in page(snapshot, kind, include_archived=True)]
    for ident in sorted(ids):
        entity = snapshot.get(kind, ident, include_body=True)
        doc = deepcopy(entity["fields"])
        if not include_archived and doc.get("archived"):
            continue
        out.append((ident, doc, entity["body"]))
    return out


def committed(receipts) -> dict:
    """The `{(kind, id): document}` map legacy renderers read, from native receipts."""
    documents = {}
    for receipt in receipts:
        for item in receipt["affected"]:
            documents[(item["kind"], item["id"])] = deepcopy(item["fields"])
    return documents
