# User intent: let the viewer's HTTP routes serve a native store (N08) — board and
# detail reads from native snapshots, edit-in-UI writes through native commands —
# with the status codes, JSON bodies and If-Match contract the legacy routes have.
"""Viewer adapters. The handler keeps its routes; these are the store seams it calls."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import re

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster import taskmaster_v3 as v3
from taskmaster.native.contracts import Conflict

from . import derived, gate, reads, runtime


def database():
    """The project's native database for this request, or None for a legacy store.

    `NativeUnavailable` is deliberately not caught: None is the handler's sentinel
    for a legacy store, so answering it for a native store this runtime cannot
    serve sends every route into the legacy body, where admission refuses anyway —
    past `handle_one_request`, as a bare 500 and a dropped connection, and for the
    write verbs only after `_transaction()` has opened. The refusal is the answer.
    """
    try:
        backlog_path = bs._backlog_path()
    except RuntimeError:
        return None
    return gate.native_database(backlog_path)


def _open(database):
    return runtime.open_call(database, database.parent.parent, bs.SESSION_ID)


def _etag(snapshot) -> str:
    identity = snapshot.identity
    return f"{identity['store_id']}:{int(identity['event_high_water'])}"


def _materialized(snapshot, data: dict) -> dict:
    """A tree that outlives its snapshot: every row kind decoded now."""
    rows = data.get("_rows")
    if isinstance(rows, reads.NativeRows):
        data["_rows"] = {kind: rows[kind] for kind in reads.ROW_KINDS}
    return data


def snapshot(database):
    """`(data, etag)` for one request, as `_load_snapshot` answers on a legacy store."""
    if not bs._backlog_path().exists():
        return None
    with _open(database) as call, call.read() as snap:
        return _materialized(snap, reads.tree(snap)), _etag(snap)


def prefs(database) -> dict:
    with _open(database) as call, call.read() as snap:
        entity = reads.get(snap, "backlog", "__backlog__")
    v4 = v3.detect_schema_version(entity["fields"] if entity else {}) >= v3.SCHEMA_V4
    return v3.load_viewer_prefs(bs._backlog_path(), v4=v4)


def task_full(database, task_id):
    backlog_path = bs._backlog_path()
    if not backlog_path.exists():
        return None, ""
    data, etag = snapshot(database)
    return bs._task_full_from(data, etag, backlog_path, task_id)


def epic_full(database, epic_id):
    if not bs._backlog_path().exists():
        return None, ""
    data, etag = snapshot(database)
    return bs._epic_full_from(data, etag, epic_id)


def recent_events(database, since_iso):
    try:
        since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    except Exception as exc:
        raise ValueError(f"invalid since: {exc}")
    data, _etag = snapshot(database)
    return bs._recent_events_from(data, since)


def threads(database) -> dict:
    with _open(database) as call, call.read() as snap:
        entity = reads.get(snap, "backlog", "__backlog__")
        return derived.apply(snap, deepcopy(entity["fields"]) if entity else {})


def related(database, task_id):
    """`_load_related_for_task` from rows: what the tool's top-level file globs reach."""
    backlog_path = bs._backlog_path()
    if not backlog_path.exists():
        return None
    with _open(database) as call, call.read() as snap:
        dependencies, unblocks = _related_dependencies(snap, task_id)
        if dependencies is None:
            return None
        # Only the two continuity kinds the panel names: no task is enumerated.
        rows = reads.rows_only(snap)["_rows"]
        rows = {kind: rows[kind] for kind in ("handover", "issue")}
    handovers, issues = [], []
    for ident, fm, body in sorted((i, d, b) for i, (d, b) in rows["handover"].items()):
        if fm.get("archived") or task_id not in list(fm.get("task_ids") or []):
            continue
        text = body or ""
        handovers.append({"id": fm.get("id") or ident, "kind": fm.get("kind"), "session": fm.get("session"),
                          "created": fm.get("created"), "status": fm.get("status", "todo"),
                          "quote": text.strip().splitlines()[0] if text.strip() else "",
                          "_path": str(v3.handover_path(backlog_path, ident))})
    for ident, fm, _body in sorted((i, d, b) for i, (d, b) in rows["issue"].items()):
        if fm.get("archived") or task_id not in list(fm.get("task_ids") or []):
            continue
        issues.append({"id": fm.get("id") or ident, "severity": fm.get("severity"), "status": fm.get("status"),
                       "title": fm.get("title") or "", "_path": str(v3.issue_path(backlog_path, ident))})
    return {"task_id": task_id, "handovers": handovers, "issues": issues, "dependencies": dependencies,
            "unblocks": unblocks}


def _related_dependencies(snap, task_id):
    """`bs._related_dependencies` through the canonical reverse index: `(None, None)`
    when the task is not in the tree, as the legacy panel answers no task."""
    found = reads.find_task(snap, task_id)
    if found is None:
        return None, None
    me = found[0]

    def row(t):
        return {"id": t["id"], "title": t.get("title", ""), "status": t.get("status", "")}
    mine = bs._dependency_ids(me.get("depends_on")) or []
    return ([row(t) for t in reads.tasks_in_tree_order(snap, mine)],
            [row(t) for t in reads.dependent_tasks(snap, task_id)])


# ── Writes ──────────────────────────────────────────────────────────────────


def _payload(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length).decode("utf-8") if length else ""
    return json.loads(raw) if raw else {}


def _committed(handler, call):
    """Stamp the commit's sequence and export notices the way `_send_json` reads them."""
    bs._TX_STATE.last_seq = call.seq
    bs._TX_STATE.export_warnings = list(call.notices)


def _refused(exc) -> str:
    return runtime.error_text(exc)


def write(handler, method, database):
    """Every viewer write route on a native store; unmatched paths answer as the legacy routes do."""
    path = handler.path
    clean = path.split("?")[0].rstrip("/")
    if method == "PUT" and path == "/api/viewer/prefs":
        return _prefs_put(handler, database)
    if method in ("PUT", "PATCH") and (m := re.fullmatch(r"/api/tasks/([A-Za-z0-9_\-]+)", path)):
        return _task_update(handler, database, m.group(1), method)
    if method != "POST":
        if method == "PUT":
            handler.send_response(404)
            handler.end_headers()
        else:
            handler.send_error(404)
        return None
    routes = (
        (r"/api/ideas", _idea_create), (r"/api/notes", _note_create),
        (r"/api/notes/([A-Za-z0-9_\-]+)/(update|archive)", _note_change),
        (r"/api/handover/([A-Za-z0-9_\-\.]+)/status", _handover_status),
        (r"/api/tasks/validate", _task_validate), (r"/api/tasks", _task_create),
        (r"/api/tasks/([A-Za-z0-9_\-]+)/archive", _task_archive),
        (r"/api/decisions/([A-Za-z0-9_\-]+)/resolve", _decision_resolve),
        (r"/api/decisions/([A-Za-z0-9_\-]+)/drop", _decision_drop),
    )
    for pattern, route in routes:
        if m := re.fullmatch(pattern, path):
            return route(handler, database, *m.groups())
    bug_routes = ((r"/api/bugs", _bug_create), (r"/api/bugs/pattern-scan", _bug_scan),
                  (r"/api/bugs/promote", _bug_promote), (r"/api/bugs/([A-Za-z0-9_\-]+)/archive", _bug_archive),
                  (r"/api/bugs/([A-Za-z0-9_\-]+)", _bug_update))
    for pattern, route in bug_routes:
        if m := re.fullmatch(pattern, clean):
            return route(handler, database, *m.groups())
    handler.send_response(404)
    handler.end_headers()
    return None


def _json_or_400(handler):
    try:
        return _payload(handler), None
    except Exception as exc:
        handler._send_json(400, {"ok": False, "error": f"invalid JSON: {exc}"})
        return None, True


def _prefs_put(handler, database):
    patch, failed = _json_or_400(handler)
    if failed:
        return
    if not isinstance(patch, dict):
        handler._send_json(400, {"ok": False, "error": "patch must be a JSON object"})
        return
    current = prefs(database)
    bs._deep_merge(current, patch)
    with _open(database) as call, call.read() as snap:
        entity = reads.get(snap, "backlog", "__backlog__")
    v4 = v3.detect_schema_version(entity["fields"] if entity else {}) >= v3.SCHEMA_V4
    v3.save_viewer_prefs(bs._backlog_path(), current, v4=v4)
    handler._send_json(200, {"ok": True})


def _execute(handler, database, operation, arguments):
    """Run one command; `(call, None)` on success or `(None, exception)` on refusal."""
    context = _open(database)
    call = context.__enter__()
    try:
        call.execute(operation, arguments)
    except (ValueError, KeyError) as exc:
        context.__exit__(None, None, None)
        return None, exc
    except BaseException:
        context.__exit__(None, None, None)
        raise
    _committed(handler, call)
    context.__exit__(None, None, None)
    return call, None


def _idea_create(handler, database):
    payload, failed = _json_or_400(handler)
    if failed:
        return
    title = (payload.get("title") or "").strip()
    if not title:
        handler._send_json(400, {"ok": False, "error": "title is required"})
        return
    backlog = bs._backlog_path()
    if not backlog.exists():
        handler._send_json(400, {"ok": False, "error": f"no backlog at {backlog}"})
        return
    arguments = {"title": title, "tags": payload.get("tags") or [], "status": payload.get("status", ""),
                 "related_tasks": payload.get("related_tasks") or [], "related_issues": payload.get("related_issues") or [],
                 "created_by": payload.get("created_by", "user")}
    try:
        v3.build_idea_doc(**arguments)
    except ValueError as exc:
        handler._send_json(400, {"ok": False, "error": str(exc)})
        return
    call, refusal = _execute(handler, database, "idea.create",
                             dict(arguments, body=payload.get("body", ""), auto_link=False))
    if refusal:
        handler._send_json(400, {"ok": False, "error": _refused(refusal)})
        return
    iid = call.receipts[-1]["affected"][0]["id"]
    handler._send_json(201, {"ok": True, "id": iid, "path": str(v3.idea_path(backlog, iid))})


def _note_create(handler, database):
    payload, failed = _json_or_400(handler)
    if failed:
        return
    text = (payload.get("text") or "").strip()
    if not text:
        handler._send_json(400, {"ok": False, "error": "text is required"})
        return
    backlog = bs._backlog_path()
    if not backlog.exists():
        handler._send_json(400, {"ok": False, "error": f"no backlog at {backlog}"})
        return
    call, refusal = _execute(handler, database, "note.create",
                             {"text": text, "author": "user", "pinned": bool(payload.get("pinned", False))})
    if refusal:
        handler._send_json(400, {"ok": False, "error": _refused(refusal)})
        return
    handler._send_json(201, {"ok": True, "id": call.receipts[-1]["affected"][0]["id"]})


def _note_change(handler, database, note_id, action):
    payload, failed = _json_or_400(handler)
    if failed:
        return
    with _open(database) as call, call.read() as snap:
        entity = reads.get(snap, "note", note_id, body=True)
    if entity is None:
        handler._send_json(404, {"ok": False, "error": f"Note not found: {note_id}"})
        return
    if action == "archive":
        arguments = {"id": note_id}
        operation = "note.archive"
    else:
        text, pinned = payload.get("text"), payload.get("pinned")
        text = text.strip() if isinstance(text, str) and text.strip() else None
        pinned = bool(pinned) if pinned is not None else None
        try:
            v3.apply_note_updates(entity["fields"], entity["body"] or "", text=text, pinned=pinned)
        except ValueError as exc:
            handler._send_json(400, {"ok": False, "error": f"Error: {exc}"})
            return
        arguments = {"id": note_id, **({"text": text} if text is not None else {}),
                     **({"pinned": pinned} if pinned is not None else {})}
        operation = "note.update"
    _call, refusal = _execute(handler, database, operation, arguments)
    if refusal:
        handler._send_json(400, {"ok": False, "error": _refused(refusal)})
        return
    handler._send_json(200, {"ok": True, "id": note_id})


def _handover_status(handler, database, handover_id):
    payload, failed = _json_or_400(handler)
    if failed:
        return
    status, reason = payload.get("status", ""), payload.get("reason", "")
    with _open(database) as call, call.read() as snap:
        entity = reads.get(snap, "handover", handover_id)
    if entity is None:
        handler._send_json(404, {"ok": False, "error": f"Handover not found: {handover_id}"})
        return
    try:
        v3.set_handover_status_doc(entity["fields"], status=status, reason=reason)
    except ValueError as exc:
        handler._send_json(400, {"ok": False, "error": f"Error: {exc}"})
        return
    _call, refusal = _execute(handler, database, "handover.status",
                              {"id": handover_id, "status": status, "reason": reason})
    if refusal:
        handler._send_json(400, {"ok": False, "error": _refused(refusal)})
        return
    handler._send_json(200, {"ok": True, "id": handover_id, "status": status})


def _task_errors(database, task_id, patch):
    """The legacy write gate — `validate_task_write` plus status and transition — over the native tree."""
    with _open(database) as call, call.read() as snap:
        epics = [{"id": e["id"], "tasks": []} for e in reads.page(snap, "epic", fields=("id",), include_archived=True)]
        by_id = {e["id"]: e for e in epics}
        # Validation needs this task and the reachable dependency closure, not
        # every task's prose or every continuity entity in the repository.
        pending = [task_id] + (bs._dependency_ids(patch.get("depends_on")) or [])
        seen = set()
        while pending:
            ident = pending.pop()
            if ident in seen:
                continue
            seen.add(ident)
            found = reads.find_task(snap, ident)
            if found:
                task, epic = found
                by_id[epic["id"]]["tasks"].append(task)
                if "depends_on" in patch:
                    pending.extend(bs._dependency_ids(task.get("depends_on")) or [])
        data = {"epics": epics, "phases": [e["fields"] for e in reads.page(snap, "phase", fields=("id",), include_archived=True)]}
        areas = reads.area_ids(snap)
    errors = v3.validate_task_write(task_id, patch, bs._backlog_path(), data=data, area_ids=areas)
    return data, errors


def _task_validate(handler, database):
    try:
        payload = _payload(handler)
    except Exception as exc:
        handler._send_json(400, {"ok": False, "error": f"invalid JSON: {exc}"})
        return
    tid, patch = payload.get("task_id") or "<new>", payload.get("patch") or {}
    data, errors = _task_errors(database, tid, patch)
    errors.update(bs._invalid_status_error(patch))
    if tid != "<new>":
        found = bs._find_task(data, tid)
        if found is not None:
            errors.update(bs._archived_transition_error(found[0], patch))
            if patch.get("status") == "done" and found[0].get("status") != "done":
                block = bs._completion_block_reason(found[0])
                if block:
                    errors["status"] = block
    handler._send_json(200, {"ok": len(errors) == 0, "errors": errors})


def _task_create(handler, database):
    try:
        payload = _payload(handler)
    except Exception as exc:
        handler._send_json(400, {"ok": False, "error": f"invalid JSON: {exc}"})
        return
    try:
        epic_id = payload.get("epic")
        if not epic_id:
            raise ValueError("epic is required")
        data, errors = _task_errors(database, "<new>", payload)
        errors.update(bs._invalid_status_error(payload))
        if errors:
            handler._send_json(422, {"ok": False, "errors": errors})
            return
        if bs._find_epic(data, epic_id) is None:
            raise KeyError(f"epic {epic_id} not found")
        call, refusal = _execute(handler, database, "task.viewer_create", {"epic": epic_id, "payload": payload})
        if refusal:
            raise refusal
        new_id = call.receipts[-1]["affected"][0]["id"]
        task = task_full(database, new_id)[0] or {"id": new_id}
        handler._send_json(201, {"ok": True, "task": task})
    except (KeyError, ValueError) as exc:
        handler._send_json(400, {"ok": False, "error": str(exc)})


def _stale_or(handler, task_id, refusal):
    if isinstance(refusal, Conflict) and str(refusal).startswith("stale:"):
        handler._send_stale(task_id, str(refusal)[len("stale:"):])
        return True
    return False


def _task_archive(handler, database, task_id):
    _call, refusal = _execute(handler, database, "task.viewer_archive",
                              {"id": task_id, "if_match": handler.headers.get("If-Match") or ""})
    if refusal is None:
        from taskmaster.viewer_detail import read
        detail = read(task_id, database)
        handler._send_json(200, {"ok": True}, etag=detail["etag"])
    elif not _stale_or(handler, task_id, refusal):
        code = 404 if isinstance(refusal, KeyError) else 500
        handler._send_json(code, {"ok": False, "error": str(refusal)})


def _task_update(handler, database, task_id, method):
    try:
        patch = _payload(handler)
    except Exception as exc:
        handler._send_json(400, {"ok": False, "error": f"invalid JSON: {exc}"})
        return
    if not isinstance(patch, dict):
        handler._send_json(400, {"ok": False, "error": "body must be object" if method == "PUT" else "patch must be object"})
        return
    if_match = handler.headers.get("If-Match") or ""
    from taskmaster.viewer_detail import task_etag
    with _open(database) as reader, reader.read() as snap:
        current = task_etag(snap.connection, snap.identity["store_id"], task_id, native=True) if if_match.strip('"').startswith("t1:") else _etag(snap)
        if if_match and if_match.strip('"') != current:
            handler._send_stale(task_id, current)
            return
    data, errors = _task_errors(database, task_id, patch)
    if "_task" in errors:
        handler._send_json(404, {"ok": False, "error": repr(errors["_task"])})
        return
    found = bs._find_task(data, task_id)
    errors.update(bs._invalid_status_error(patch))
    errors.update(bs._archived_transition_error(found[0], patch))
    moved_to = patch.get("epic")
    if not errors and moved_to and moved_to != (found[0].get("epic") or found[1].get("id")) \
            and bs._find_epic(data, moved_to) is None:
        errors = {"epic": f"unknown epic: {moved_to}"}
    if errors:
        handler._send_json(422, {"ok": False, "errors": errors})
        return
    if patch.get("status") == "done" and found[0].get("status") != "done":
        block = bs._completion_block_reason(found[0])
        if block:
            handler._send_json(409, {"ok": False, "error": block})
            return
    call, refusal = _execute(handler, database, "task.viewer_update",
                             {"id": task_id, "patch": patch, "if_match": if_match})
    if refusal is not None:
        if _stale_or(handler, task_id, refusal):
            return
        handler._send_json(404 if isinstance(refusal, KeyError) else 500, {"ok": False, "error": str(refusal)})
        return
    from taskmaster.viewer_detail import write_response
    task, etag = write_response(task_id, database)
    handler._send_json(200, {"ok": True, "task": task}, etag=etag)


def _decision_resolve(handler, database, decision_id):
    payload, failed = _json_or_400(handler)
    if failed:
        return
    resolved_with = payload.get("resolved_with")
    if resolved_with is None:
        handler._send_json(400, {"ok": False, "error": "resolved_with is required"})
        return
    _decision(handler, database, decision_id, "decision.resolve",
              {"id": decision_id, "resolved_with": int(resolved_with), "rationale": payload.get("rationale", "")},
              lambda doc: v3.resolve_decision_doc(doc, resolved_with=int(resolved_with),
                                                  rationale=payload.get("rationale", ""), resolved_in=None))


def _decision_drop(handler, database, decision_id):
    payload, failed = _json_or_400(handler)
    if failed:
        return
    reason = payload.get("reason", "")
    _decision(handler, database, decision_id, "decision.drop", {"id": decision_id, "reason": reason},
              lambda doc: v3.drop_decision_doc(doc, reason=reason))


def _decision(handler, database, decision_id, operation, arguments, rule):
    with _open(database) as call, call.read() as snap:
        entity = reads.get(snap, "decision", decision_id)
    if entity is None:
        handler._send_json(404, {"ok": False, "error": f"Error: Decision not found: {decision_id}"})
        return
    try:
        document = rule(entity["fields"])
    except ValueError as exc:
        handler._send_json(400, {"ok": False, "error": f"Error: {exc}"})
        return
    _call, refusal = _execute(handler, database, operation, arguments)
    if refusal:
        handler._send_json(400, {"ok": False, "error": _refused(refusal)})
        return
    handler._send_json(200, {"ok": True, "id": decision_id, "status": document.get("status")})


def _bug_create(handler, database):
    payload, failed = _json_or_400(handler)
    if failed:
        return
    title = (payload.get("title") or "").strip()
    if not title:
        handler._send_json(400, {"ok": False, "error": "title is required"})
        return
    backlog = bs._backlog_path()
    if not backlog.exists():
        handler._send_json(400, {"ok": False, "error": f"no backlog at {backlog}"})
        return
    arguments = {"title": title, "found_in": payload.get("found_in") or None,
                 "discovered_by": payload.get("discovered_by", "user"), "severity": payload.get("severity") or None,
                 "components": payload.get("components") or [], "location": payload.get("location") or []}
    try:
        v3.build_bug_doc(**arguments)
    except ValueError as exc:
        handler._send_json(400, {"ok": False, "error": str(exc)})
        return
    call, refusal = _execute(handler, database, "bug.create", dict(arguments, body=payload.get("body", "")))
    if refusal:
        handler._send_json(400, {"ok": False, "error": _refused(refusal)})
        return
    bid = call.receipts[-1]["affected"][0]["id"]
    handler._send_json(201, {"ok": True, "id": bid, "path": str(v3.bug_path(backlog, bid))})


def _bug_scan(handler, database):
    payload, failed = _json_or_400(handler)
    if failed:
        return
    current = snapshot(database)
    if current is None:
        handler._send_json(200, {"groups": []})
        return
    data, etag = current
    include_archive = payload.get("mode", "all") != "end_of_task"
    rows = [(ident, doc, body) for ident, (doc, body) in sorted(data["_rows"]["bug"].items())
            if include_archive or not doc.get("archived")]
    handler._send_json(200, {"groups": v3.scan_bug_patterns(rows)}, etag=etag)


def _bug_promote(handler, database):
    payload, failed = _json_or_400(handler)
    if failed:
        return
    bug_ids = payload.get("bug_ids") or []
    for name, value in (("bug_ids", bug_ids), ("title", (payload.get("title") or "").strip()),
                        ("severity", (payload.get("severity") or "").strip()),
                        ("evidence_text", (payload.get("evidence_text") or "").strip())):
        if not value:
            handler._send_json(400, {"ok": False, "error": f"{name} is required"})
            return
    title, severity = payload["title"].strip(), payload["severity"].strip()
    evidence = payload["evidence_text"].strip()
    with _open(database) as call, call.read() as snap:
        sources = {bid: reads.get(snap, "bug", bid) for bid in bug_ids}
    for bid, entity in sources.items():
        if entity is None:
            handler._send_json(400, {"ok": False, "error": f"bug {bid} not found"})
            return
    components = payload.get("components") or None
    try:
        v3.build_issue_doc(title=title, severity=severity, impact=evidence, evidence=evidence,
                           components=components or sorted({c for e in sources.values()
                                                            for c in (e["fields"].get("components") or [])}),
                           promoted_from=list(bug_ids))
    except ValueError as exc:
        handler._send_json(400, {"ok": False, "error": str(exc)})
        return
    arguments = {"bug_ids": list(bug_ids), "title": title, "severity": severity, "evidence_text": evidence,
                 "body": payload.get("body", "")}
    if components:
        arguments["components"] = list(components)
    call, refusal = _execute(handler, database, "bug.promote", arguments)
    if refusal:
        handler._send_json(400, {"ok": False, "error": _refused(refusal)})
        return
    issue_id = next(item["id"] for item in call.receipts[-1]["affected"] if item["kind"] == "issue")
    handler._send_json(201, {"ok": True, "issue_id": issue_id})


def _bug_archive(handler, database, bug_id):
    length = int(handler.headers.get("Content-Length") or 0)
    handler.rfile.read(length)
    with _open(database) as call, call.read() as snap:
        entity = reads.get(snap, "bug", bug_id)
    if entity is None:
        handler._send_json(404, {"ok": False, "error": f"Bug not found: {bug_id}"})
        return
    try:
        v3.assert_bug_archivable(entity["fields"])
    except ValueError as exc:
        handler._send_json(400, {"ok": False, "error": f"Error: {exc}"})
        return
    _call, refusal = _execute(handler, database, "bug.archive", {"id": bug_id})
    if refusal:
        handler._send_json(400, {"ok": False, "error": _refused(refusal)})
        return
    handler._send_json(200, {"ok": True, "id": bug_id})


def _bug_update(handler, database, bug_id):
    payload, failed = _json_or_400(handler)
    if failed:
        return
    updates = {}
    if "status" in payload:
        if payload["status"] not in v3.BUG_STATUSES:
            handler._send_json(400, {"ok": False, "error": f"status must be one of {v3.BUG_STATUSES}"})
            return
        updates["status"] = payload["status"]
    for field in ("title", "severity", "fix_commit", "adopted_into", "promoted_to", "body"):
        if field in payload and payload[field]:
            updates[field] = payload[field]
    for field in ("components", "location"):
        if field in payload and payload[field] is not None:
            updates[field] = payload[field]
    with _open(database) as call, call.read() as snap:
        entity = reads.get(snap, "bug", bug_id)
    if entity is None:
        handler._send_json(404, {"ok": False, "error": f"Bug not found: {bug_id}"})
        return
    try:
        document = v3.apply_bug_updates(entity["fields"], **{k: v for k, v in updates.items() if k != "body"})
    except ValueError as exc:
        handler._send_json(400, {"ok": False, "error": f"Error: {exc}"})
        return
    _call, refusal = _execute(handler, database, "bug.update", {"id": bug_id, "patch": updates})
    if refusal:
        handler._send_json(400, {"ok": False, "error": _refused(refusal)})
        return
    handler._send_json(200, {"ok": True, "id": bug_id, "status": document["status"]})
