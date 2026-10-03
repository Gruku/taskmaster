# User intent: give adapters the legacy-shaped documents their shared presentation
# code expects (a task with `_body`, normalized priority/created, its epic) from
# bounded native snapshot queries, never from the whole-backlog compatibility dict.
"""Legacy-shaped reads over one native snapshot."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from taskmaster import backlog_server as bs
from taskmaster.native.queries import MAX_PAGE
from taskmaster.taskmaster_v3 import BODY_KEY


def page(snapshot, kind, *, fields=None, **filters) -> list[dict]:
    items, cursor = [], None
    while True:
        result = snapshot.list(kind, fields=fields, limit=MAX_PAGE, cursor=cursor, **filters)
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
    from taskmaster.native.domain import find_phase as find
    return find(phases(snapshot), phase_id)


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


def tasks_in_tree_order(snapshot, ids) -> list[dict]:
    """The tasks `find_task` finds among `ids`, once each, in the order a scan of the
    legacy tree lists them (epic creation, then order, then id)."""
    found = [pair for pair in (find_task(snapshot, ident) for ident in dict.fromkeys(ids)) if pair is not None]
    found.sort(key=lambda pair: (_epic_rank(snapshot, pair[1]["id"]),
                                 float(pair[0].get("order", 0.0)), str(pair[0].get("id", ""))))
    return [task for task, _epic in found]


def dependent_tasks(snapshot, task_id) -> list[dict]:
    """Tasks whose `depends_on` names `task_id`, from the canonical reverse index
    rather than a scan of every task, in the legacy scan's order."""
    from taskmaster.native import dependency_graph
    return tasks_in_tree_order(snapshot, dependency_graph.dependents(snapshot.connection, task_id))


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


def unchanged(receipts) -> dict:
    """`{(kind, id): document}` for entities a command wrote but left exactly as stored.

    The document is the one the command observed inside its transaction, so it is
    the stored value at commit, never a later read that a peer may have changed.
    """
    documents = {}
    for receipt in receipts:
        for item in receipt.get("unchanged", ()):
            documents[(item["kind"], item["id"])] = deepcopy(item["fields"])
    return documents


ROW_KINDS = ("bug", "issue", "handover", "decision", "idea", "note", "area", "tracker")


def bodies(snapshot, kind) -> dict:
    """`{id: body}` for every entity of a kind that has prose, in one query."""
    return dict(snapshot.connection.execute(
        "SELECT c.public_id,d.body FROM entity_core c JOIN entity_documents d USING(entity_key) "
        "WHERE c.kind=? AND c.deleted=0 AND d.body IS NOT NULL", (kind,)).fetchall())


class NativeRows(Mapping):
    """The legacy dict's `_rows` — `{kind: {id: (doc, body)}}` — decoded per kind on first use."""

    def __init__(self, snapshot):
        self._snapshot = snapshot
        self._parsed: dict = {}

    def __getitem__(self, kind):
        if kind not in ROW_KINDS:
            raise KeyError(kind)
        if kind not in self._parsed:
            prose = bodies(self._snapshot, kind)
            items = page(self._snapshot, kind, include_archived=True)
            self._parsed[kind] = {entity["id"]: (deepcopy(entity["fields"]), prose.get(entity["id"]))
                                  for entity in sorted(items, key=lambda e: e["id"])}
        return self._parsed[kind]

    def ids(self, kind) -> list[str]:
        """Every id of one kind, archived included, without decoding a document."""
        return [row[0] for row in self._snapshot.connection.execute(
            "SELECT public_id FROM entity_core WHERE kind=? AND deleted=0 ORDER BY public_id", (kind,))]

    def __iter__(self):
        return iter(ROW_KINDS)

    def __len__(self):
        return len(ROW_KINDS)


def tree(snapshot, *, context=True) -> dict:
    """The compatibility dict's shape — backlog fields, epics with their tasks, phases,
    lazily decoded `_rows` and the derived `context` — built from native queries.

    It reads every task, as the legacy dict does; it exists so the whole-backlog
    read tools render through their own shared presentation code rather than a
    second copy. The dashboard reads `dashboard_tree` instead.

    Whole-tree consumers left on it on purpose (N16-B), because their renderers
    read arbitrary task fields or prose: epic/phase status and phase advance,
    blast radius, the search fallback (FTS unavailable), `backlog_validate`,
    `backlog_continuity_items`, and the viewer's compatibility snapshot. None is
    on a per-command path.
    """
    return _tree(snapshot, context=context, task_fields=None)


# Every task key `_derive_context`, `_status_text` and `_render_progress_dashboard`
# read (with `_normalize_task`, `_find_task`, the dependency resolver and the claim
# check). `test_native_dashboard_reads` records the keys those renderers touch on a
# full tree and fails if one is missing here, so a renderer change cannot silently
# read a field this slim read leaves out.
DASHBOARD_TASK_FIELDS = ("id", "title", "status", "epic", "phase", "priority", "order",
                         "created", "started", "completed", "branch", "locked_by",
                         "blockers", "depends_on", "last_referenced")


def dashboard_tree(snapshot) -> dict:
    """`tree` for the dashboard (`backlog_status`, PROGRESS.md): the same document,
    with each task carrying only `DASHBOARD_TASK_FIELDS` and no prose.

    Decoding every task's full document and body is most of a whole-tree read's
    cost, and the dashboard renders none of it. Output is byte-identical to the
    same renderers over `tree` (N16-B oracle).
    """
    return _tree(snapshot, context=True, task_fields=DASHBOARD_TASK_FIELDS)


def _tree(snapshot, *, context, task_fields) -> dict:
    from . import derived
    backlog = get(snapshot, "backlog", "__backlog__")
    data = derived.apply(snapshot, deepcopy(backlog["fields"]) if backlog else {})
    epic_list = epics(snapshot)
    by_id = {}
    for epic in epic_list:
        epic["tasks"] = []
        by_id[epic["id"]] = epic
    prose = bodies(snapshot, "task") if task_fields is None else {}
    orphans = []
    # `fields` is freshly decoded per page and shared with nothing, so it is
    # handed on without the defensive deep copy a cached document would need.
    for entity in page(snapshot, "task", fields=task_fields, include_archived=True):
        task = entity["fields"]
        if prose.get(entity["id"]):
            task[BODY_KEY] = prose[entity["id"]]
        epic = by_id.get(task.get("epic"))
        if epic is None:
            orphans.append(entity["id"])
        else:
            epic["tasks"].append(bs._normalize_task(task))
    for epic in epic_list:
        epic["tasks"].sort(key=lambda task: (float(task.get("order", 0.0)), str(task.get("id", ""))))
    data["epics"] = epic_list
    data["phases"] = phases(snapshot)
    data["context"] = {}
    data["_orphan_tasks"] = orphans
    if context:
        bs._derive_context(data)
    data["_rows"] = NativeRows(snapshot)
    return data


def rows_only(snapshot) -> dict:
    """A compatibility dict carrying only `_rows`, for reads that touch no task tree."""
    return {"_rows": NativeRows(snapshot)}


class _TldrIndex:
    """`build_tldr_index` answered per id: tasks, live issues and handovers, and ideas."""

    def __init__(self, snapshot):
        self._snapshot, self._cache = snapshot, {}

    def get(self, ident, default=None):
        if ident not in self._cache:
            value = None
            for kind, archived_ok in (("task", True), ("issue", False), ("handover", False), ("idea", True)):
                entity = get(self._snapshot, kind, ident) if isinstance(ident, str) else None
                if entity is None or (entity["archived"] and not archived_ok):
                    continue
                if kind == "task" and entity["archived"] and epic_of(self._snapshot, entity["fields"]) is None:
                    continue
                if entity["fields"].get("tldr"):
                    value = entity["fields"]["tldr"]
            self._cache[ident] = value
        return self._cache[ident] if self._cache[ident] is not None else default

    def __getitem__(self, ident):
        value = self.get(ident)
        if value is None:
            raise KeyError(ident)
        return value

    def __contains__(self, ident):
        return self.get(ident) is not None


class NativeLinks:
    """`_LegacyLinks` over a native snapshot: bounded lookups instead of a tree load and file glob."""

    def __init__(self, snapshot, backlog_path):
        self._snapshot, self._backlog_path = snapshot, backlog_path

    def tldr_index(self):
        return _TldrIndex(self._snapshot)

    def peer(self, target):
        from taskmaster.taskmaster_v3 import resolve_link_kind
        if not self._backlog_path.exists():
            return None
        # By lookup, as the legacy `read_entity_anywhere` resolves it: a task's
        # id carries its epic's prefix, not a kind's.
        kind = resolve_link_kind(target, lambda kind, ident: get(self._snapshot, kind, ident) is not None)
        if kind is None:
            return None
        entity = get(self._snapshot, kind, target, body=True)
        return document(entity) if entity is not None else None
