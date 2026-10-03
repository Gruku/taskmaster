# User intent: serve typed links (create/remove/query/validate), areas and the
# viewer prefs and board opening from the native core (N08), answering exactly as the legacy
# tools do — including the links they synthesize from legacy fields.
"""Link, area and viewer-prefs adapters."""
from __future__ import annotations

from copy import deepcopy
import json

from taskmaster import backlog_server as bs
from taskmaster import taskmaster_v3 as v3
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


def _kind(snapshot, ident):
    """The kind of the linkable entity `ident` names, by lookup, as the tool resolves it."""
    return v3.resolve_link_kind(ident, lambda kind, eid: reads.get(snapshot, kind, eid) is not None)


def _stored(snapshot, ident, kind):
    """The entity's stored document: the links its fields derive are not in it."""
    entity = reads.get(snapshot, kind, ident, body=True)
    return reads.document(entity) if entity is not None else None


def _anywhere(snapshot, ident, kind=None):
    """`read_entity_anywhere`: the entity, with legacy links synthesized."""
    kind = kind or _kind(snapshot, ident)
    if kind is None:
        return None
    entity = reads.get(snapshot, kind, ident, body=True)
    if entity is None:
        return None
    document = reads.document(entity)
    v3._fallback_links_if_absent(document, kind)
    return document


# ── Links ───────────────────────────────────────────────────────────────────


@adapter("backlog_link", actions=("create", "remove", "query", "validate"),
         unknown=lambda action: json.dumps({"error": f"unknown action {action!r}"}))
def link(call, *, action, source, target, type, note, depth, write):
    if action == "create":
        return _create(call, source, target, type, note)
    if action == "remove":
        return _remove(call, source, target, type)
    with call.read() as snapshot:
        if action == "query":
            return _query(snapshot, source, target, type, depth)
        return _validate(snapshot)


def _create(call, source, target, link_type, note):
    if link_type not in v3.LINK_TYPES:
        return f"Error: invalid link type {link_type!r} (valid: {sorted(v3.LINK_TYPES)})"
    with call.read() as snapshot:
        source_kind = _kind(snapshot, source)
        if source_kind is None:
            return f"Error: source {source!r} not found"
        target_kind = _kind(snapshot, target)
        if target_kind is None:
            return f"Error: target {target!r} not found"
        if not v3.is_valid_link(link_type, source_kind, target_kind):
            return (f"Error: invalid link — type {link_type!r} cannot go from "
                    f"{source_kind} ({source}) to {target_kind} ({target})")
        if link_type in v3.TASK_DEPENDENCY_LINK_TYPES:
            return f"Error: {v3.task_dependency_link_refusal(link_type, source, target)}"
        source_entity = _stored(snapshot, source, source_kind)
    added = v3.add_link(deepcopy(source_entity), link_type, target)
    refusal = _run(call, "link.create", {"source": source, "target": target, "type": link_type, "note": note})
    if refusal:
        return refusal
    suffix = "" if added else " (no-op, link already present)"
    note_part = f" -- {note}" if note else ""
    return call.finish(f"ok: linked {source} -[{link_type}]-> {target}{suffix}{note_part}")


def _remove(call, source, target, link_type):
    with call.read() as snapshot:
        source_kind = _kind(snapshot, source)
        source_entity = _stored(snapshot, source, source_kind) if source_kind else None
        between_tasks = source_kind == "task" and _kind(snapshot, target) == "task"
    if source_entity is None:
        return f"Error: source {source!r} not found"
    if between_tasks and link_type in v3.TASK_DEPENDENCY_LINK_TYPES:
        return f"Error: {v3.task_dependency_link_refusal(link_type, source, target, remove=True)}"
    if link_type:
        if link_type not in v3.LINK_TYPES:
            return f"Error: invalid link type {link_type!r}"
        types = [link_type]
    else:
        types = sorted({item["type"] for item in v3.entity_links(source_entity) if item["target"] == target
                        and not (between_tasks and item["type"] in v3.TASK_DEPENDENCY_LINK_TYPES)})
    if not types:
        return f"ok: no-op (no links from {source} to {target})"
    probe = deepcopy(source_entity)
    removed = False
    for item_type in types:
        removed = v3.remove_link(probe, item_type, target) or removed
    refusal = _run(call, "link.remove", {"source": source, "target": target, "type": link_type})
    if refusal:
        return refusal
    if removed:
        return call.finish(f"ok: removed {len(types)} link(s) between {source} and {target}")
    return call.finish(f"ok: no-op (links not present between {source} and {target})")


def _file_entities(snapshot, include_archived=False):
    """`(id, kind)` of every non-task entity a link can join, as the tool's `_linkable_rows` lists them."""
    for kind in v3.LINKABLE_KINDS:
        if kind == "task":
            continue
        for entity in sorted(reads.page(snapshot, kind, include_archived=True), key=lambda e: e["id"]):
            if include_archived or kind in bs._LINK_LISTING_KEEPS_ARCHIVED or not entity["archived"]:
                yield entity["id"], kind, entity["archived"]


def _query(snapshot, source, target, link_type, depth):
    def edges_from(ident):
        kind = _kind(snapshot, ident)
        entity = _anywhere(snapshot, ident, kind) if kind else None
        if entity is None:
            return []
        edges = [{"source": ident, "target": item["target"], "type": item["type"]} for item in v3.entity_links(entity)]
        if kind != "task":
            return edges
        # A task's `blocks` side is derived from the tasks that depend on it.
        return edges + [{"source": ident, "target": task["id"], "type": "blocks"}
                        for task in reads.dependent_tasks(snapshot, ident)]

    if source and _anywhere(snapshot, source) is None:
        return f"Error: source {source!r} not found"
    if source:
        results = list(edges_from(source))
        if depth > 1 and link_type:
            seen = {(edge["source"], edge["target"]) for edge in results}
            frontier = [edge["target"] for edge in results if edge["type"] == link_type]
            for _ in range(depth - 1):
                following = []
                for node in frontier:
                    for edge in edges_from(node):
                        key = (edge["source"], edge["target"])
                        if edge["type"] != link_type or key in seen:
                            continue
                        seen.add(key)
                        results.append(edge)
                        following.append(edge["target"])
                frontier = following
    else:
        results = []
        tasks = [task for epic in reads.epics(snapshot) for task in reads.epic_tasks(snapshot, epic["id"])]
        for task in tasks:
            for item in v3.link_view(task, "task"):
                results.append({"source": task["id"], "target": item["target"], "type": item["type"]})
        for task in tasks:
            for dependency in bs._dependency_ids(task.get("depends_on")) or []:
                results.append({"source": dependency, "target": task["id"], "type": "blocks"})
        for ident, kind, _archived in _file_entities(snapshot):
            for item in v3.entity_links(_anywhere(snapshot, ident, kind)):
                results.append({"source": ident, "target": item["target"], "type": item["type"]})
    if target:
        results = [edge for edge in results if edge["target"] == target]
    if link_type:
        results = [edge for edge in results if edge["type"] == link_type]
    return json.dumps(results)


def _validate(snapshot):
    entities = {}
    for epic in reads.epics(snapshot):
        for task in reads.epic_tasks(snapshot, epic["id"]):
            if task.get("id"):
                entities[task["id"]] = {**task, "links": v3.link_view(task, "task")}
    archived = set()
    for ident, kind, is_archived in _file_entities(snapshot, include_archived=True):
        entities[ident] = _anywhere(snapshot, ident, kind)
        if is_archived:
            archived.add(ident)
    orphans, asymmetric, archived_targets, graph = [], [], [], {}
    for ident, entity in entities.items():
        for item in v3.entity_links(entity):
            item_target, item_type = item["target"], item["type"]
            if item_target not in entities:
                orphans.append({"source": ident, "target": item_target, "type": item_type})
                continue
            peer = entities[item_target]
            if peer.get("status") == "archived" or peer.get("archived") or item_target in archived:
                archived_targets.append({"source": ident, "target": item_target, "type": item_type})
            inverse = v3.REVERSE_TYPE.get(item_type)
            if inverse is None or item_type in v3.TASK_DEPENDENCY_LINK_TYPES:
                if item_type == "depends_on":
                    graph.setdefault(ident, []).append(item_target)
                    graph.setdefault(item_target, graph.get(item_target, []))
                continue
            if {"type": inverse, "target": ident} not in v3.entity_links(peer):
                asymmetric.append({"source": ident, "target": item_target, "type": item_type,
                                   "missing_inverse": inverse})
            if item_type == "depends_on":
                graph.setdefault(ident, []).append(item_target)
                graph.setdefault(item_target, graph.get(item_target, []))
    cycles, remaining = [], {key: list(value) for key, value in graph.items()}
    for _ in range(5):
        cycle = v3.find_cycle(remaining)
        if cycle is None:
            break
        cycles.append(cycle)
        if len(cycle) >= 2 and cycle[1] in remaining.get(cycle[0], []):
            remaining[cycle[0]].remove(cycle[1])
    return json.dumps({"orphans": orphans, "asymmetric": asymmetric, "cycles": cycles,
                       "archived_targets": archived_targets})


# ── Areas ───────────────────────────────────────────────────────────────────


@adapter("backlog_area_create")
def area_create(call, *, area_id, name, description, anchors):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return f"Error: no backlog found at {backlog}. Run `backlog_init` first."
    try:
        anchors = bs._anchor_items(anchors)
    except ValueError as exc:
        return f"Error: {exc}"
    try:
        v3.validate_area_doc({"id": area_id, "name": name, "description": description,
                              "anchors": list(anchors) if anchors else [], "created": domain.now_stamp()})
    except ValueError as exc:
        return f"Error: {exc}"
    with call.read() as snapshot:
        taken = snapshot.connection.execute(
            "SELECT 1 FROM entity_core WHERE kind='area' AND public_id=? UNION ALL "
            "SELECT 1 FROM id_reservations WHERE kind='area' AND public_id=? LIMIT 1", (area_id, area_id)).fetchone()
    if taken:
        return f"Error: area `{area_id}` already exists"
    refusal = _run(call, "area.create", {"area_id": area_id, "name": name, "description": description,
                                          "anchors": list(anchors) if anchors else []})
    if refusal:
        return refusal
    target = v3.area_path(backlog, area_id)
    try:
        rel = target.relative_to(bs.ROOT)
    except ValueError:
        rel = target
    return call.finish(f"Area created: {area_id} — {name}\nFile: {rel}")


@adapter("backlog_area_list")
def area_list(call, *, limit):
    if not bs._backlog_path().exists():
        return "No backlog found."
    with call.read() as snapshot:
        return bs._area_list_text(reads.rows_only(snapshot), limit)


@adapter("backlog_area_get")
def area_get(call, *, area_id):
    if not bs._backlog_path().exists():
        return "No backlog found."
    with call.read() as snapshot:
        epics = reads.epics(snapshot)
        by_id = {epic["id"]: epic for epic in epics}
        for epic in epics:
            epic["tasks"] = []
        # Only what the area summary counts, not every task's document.
        for entity in reads.page(snapshot, "task", fields=("id", "status", "area", "epic"), include_archived=True):
            epic = by_id.get(entity["fields"].get("epic"))
            if epic is not None:
                epic["tasks"].append(entity["fields"])
        return bs._area_get_text(reads.rows_only(snapshot), area_id, epics)


@adapter("backlog_area_update")
def area_update(call, *, area_id, field, value):
    if field not in domain.ALLOWED_AREA_FIELDS:
        return f"Error: field `{field}` not allowed. Allowed: {', '.join(sorted(domain.ALLOWED_AREA_FIELDS))}"
    if not bs._backlog_path().exists():
        return "No backlog found."
    with call.read() as snapshot:
        entity = reads.get(snapshot, "area", area_id)
    if entity is None:
        return f"Area not found: {area_id}"
    if field == "anchors":
        try:
            final = bs._edited_anchors(entity["fields"].get("anchors") or [], value)
        except ValueError as exc:
            return f"Error: {exc}"
        updates = {"anchors": final}
        value = json.dumps(final)  # the command takes the whole list, as a JSON array
    elif not isinstance(value, str):
        return f"Error: `{field}` takes a string"
    else:
        updates = {field: value}
    try:
        v3.apply_area_updates(entity["fields"], updates)
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "area.update", {"id": area_id, "field": field, "value": value})
    if refusal:
        return refusal
    return call.finish(f"Area updated: {area_id} — field `{field}`")


def _prefs_v4(call) -> bool:
    # Viewer preferences are machine-local host state, not store authority; the
    # schema that places the file comes from the backlog row, not backlog.yaml.
    with call.read() as snapshot:
        entity = reads.get(snapshot, "backlog", "__backlog__")
    return v3.detect_schema_version(entity["fields"] if entity else {}) >= v3.SCHEMA_V4


@adapter("viewer_prefs_get")
def prefs_get(call):
    return json.dumps(v3.load_viewer_prefs(bs._backlog_path(), v4=_prefs_v4(call)), indent=2)


@adapter("viewer_prefs_set")
def prefs_set(call, *, patch_json):
    try:
        patch = json.loads(patch_json)
    except Exception as exc:
        return f"Error: invalid JSON ({exc})"
    if not isinstance(patch, dict):
        return "Error: patch must be a JSON object"
    v4 = _prefs_v4(call)
    prefs = v3.load_viewer_prefs(bs._backlog_path(), v4=v4)
    bs._deep_merge(prefs, patch)
    v3.save_viewer_prefs(bs._backlog_path(), prefs, v4=v4)
    return "ok"


@adapter("backlog_open_viewer")
def open_viewer(call):
    # A host action: the board it opens is served by the native viewer routes.
    port = bs._start_viewer_server()
    url = f"http://127.0.0.1:{port}/"
    bs.webbrowser.open(url)
    return f"Opened backlog viewer at {url}"
