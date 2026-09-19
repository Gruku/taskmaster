# User intent: serve session continuity — handovers, the thread board and resume,
# continuity items and the last-session read — from the native core (N08), with
# answers, refusals and the handover index exactly as the legacy tools produce.
"""Handover, thread and continuity adapters."""
from __future__ import annotations

from copy import deepcopy
import json

from taskmaster import backlog_server as bs
from taskmaster import taskmaster_v3 as v3

from . import derived, reads
from .registry import adapter
from .runtime import error_text


def _run(call, operation, arguments):
    try:
        call.execute(operation, arguments)
    except (ValueError, KeyError) as exc:
        return error_text(exc)
    return None


def _backlog_document(snapshot) -> dict:
    """The backlog row with its derived indexes, plus the row map reads consult."""
    entity = reads.get(snapshot, "backlog", "__backlog__")
    data = derived.apply(snapshot, deepcopy(entity["fields"]) if entity else {})
    data["_rows"] = reads.NativeRows(snapshot)
    return data


def _thread_name(snapshot, task_ids, tldr, bundle_slug):
    """`derive_thread_name` over just the epics that hold the named tasks, in legacy order."""
    epics = {}
    for task_id in task_ids:
        found = reads.find_task(snapshot, task_id)
        if found is not None:
            epic = epics.setdefault(found[1]["id"], {"id": found[1]["id"], "tasks": []})
            epic["tasks"].append({"id": task_id})
    ranks = {row[0]: row[1] for row in snapshot.connection.execute(
        "SELECT public_id,entity_key FROM entity_core WHERE kind='epic' AND deleted=0")}
    ordered = sorted(epics.values(), key=lambda epic: ranks.get(epic["id"], 0))
    return v3.derive_thread_name(task_ids, tldr, {"epics": ordered}, bundle_slug=bundle_slug)


@adapter("backlog_handover_create")
def handover_create(call, *, tldr, next_action, body, task_ids, session_kind, thread, supersedes, flag_for_review,
                    options):
    options = options or {}
    branch = options.get("branch", "") or ""
    tip_commit = options.get("tip_commit", "") or ""
    context_size_at_write = options.get("context_size_at_write", "") or ""
    review_reason = options.get("review_reason", "") or ""
    backlog = bs._backlog_path()
    if not backlog.exists():
        return f"Error: no backlog found at {backlog}. Run `backlog_init` first."
    with call.read() as snapshot:
        thread_name = (thread or "").strip()
        if not thread_name:
            bundle = bs._get_session_bundle() or {}
            thread_name = _thread_name(snapshot, task_ids or [], tldr, bundle.get("slug", "") or "")
        superseded_exists = bool(supersedes) and reads.get(snapshot, "handover", supersedes) is not None
    arguments = {"tldr": tldr, "next_action": next_action, "body": body, "task_ids": task_ids or [],
                 "session_kind": session_kind, "thread": thread_name,
                 "context_size_at_write": context_size_at_write or None, "supersedes": supersedes or None,
                 "branch": branch or None, "tip_commit": tip_commit or None}
    try:
        v3.build_handover_doc(**arguments)
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "handover.create", dict(arguments, flag_for_review=bool(flag_for_review),
                                                   review_reason=review_reason))
    if refusal:
        return refusal
    hid = next(item["id"] for item in call.receipts[-1]["affected"]
               if item["kind"] == "handover" and item["id"] != supersedes)
    target = v3.handover_path(backlog, hid)
    with call.read() as snapshot:
        entries = len(_backlog_document(snapshot).get("handovers") or [])
    lines = [f"Handover written: {hid}", f"- File: {target.relative_to(bs.ROOT)}", f"- Path: {target.resolve()}",
             f"- Index entries: {entries}"]
    if supersedes and superseded_exists:
        lines.append(f"- Superseded: {supersedes}")
    if supersedes and not superseded_exists:
        lines.append(f"- WARNING: supersedes={supersedes} not found on disk; old handover not updated.")
    if flag_for_review:
        lines.append(f"- Flagged for review: {review_reason}")
    lines.append(f"Resume: {thread_name} — {next_action or tldr}")
    return call.finish("\n".join(lines))


@adapter("backlog_handover_list")
def handover_list(call, *, task_id, session_kind, since, status, limit, verbose):
    if not bs._backlog_path().exists():
        return "No backlog found."
    with call.read() as snapshot:
        return bs._handover_list_text(_backlog_document(snapshot), task_id, session_kind, since, status, limit, verbose)


@adapter("backlog_handover_get")
def handover_get(call, *, handover_id, verbose, sections, expand_links):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return "No backlog found."
    with call.read() as snapshot:
        return bs._handover_get_text(reads.rows_only(snapshot), handover_id, verbose, sections, expand_links,
                                     backlog, reads.NativeLinks(snapshot, backlog))


@adapter("backlog_handover_supersede")
def handover_supersede(call, *, old_id, new_id):
    if not bs._backlog_path().exists():
        return "No backlog found."
    with call.read() as snapshot:
        taken = snapshot.connection.execute(
            "SELECT 1 FROM entity_core WHERE kind='handover' AND public_id=? UNION ALL "
            "SELECT 1 FROM id_reservations WHERE kind='handover' AND public_id=? LIMIT 1", (new_id, new_id)).fetchone()
        old = reads.get(snapshot, "handover", old_id)
    if not taken:
        return f"Error: handover not found: {new_id}."
    if old is None:
        return f"Error: handover not found: {old_id}."
    refusal = _run(call, "handover.supersede", {"id": old_id, "new_id": new_id})
    if refusal:
        return refusal
    return call.finish(f"Superseded {old_id} → {new_id} ({old_id}.md updated).")


@adapter("backlog_handover_update_status")
def handover_update_status(call, *, handover_id, status, reason):
    if not bs._backlog_path().exists():
        return "No backlog found."
    with call.read() as snapshot:
        entity = reads.get(snapshot, "handover", handover_id)
    if entity is None:
        return f"Handover not found: {handover_id}"
    try:
        v3.set_handover_status_doc(entity["fields"], status=status, reason=reason)
    except ValueError as exc:
        return f"Error: {exc}"
    refusal = _run(call, "handover.status", {"id": handover_id, "status": status, "reason": reason})
    if refusal:
        return refusal
    return call.finish(f"Handover {handover_id} → status={status} (user-set).")


@adapter("backlog_thread_list")
def thread_list(call, *, include_closed):
    if not bs._backlog_path().exists():
        return "No backlog found."
    with call.read() as snapshot:
        return bs._thread_list_text(_backlog_document(snapshot), include_closed)


@adapter("backlog_thread_resume")
def thread_resume(call, *, ref):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return "No backlog found."
    with call.read() as snapshot:
        def find_handover(ident):
            entity = reads.get(snapshot, "handover", ident)
            return deepcopy(entity["fields"]) if entity else None
        return bs._thread_resume_text(_backlog_document(snapshot), backlog, ref, find_handover)


@adapter("backlog_thread_update")
def thread_update(call, *, name, status, reason):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return "No backlog found."
    with call.read() as snapshot:
        data = _backlog_document(snapshot)
    data.pop("_rows", None)
    try:
        v3.update_thread_status(data, backlog, name=name, status=status, reason=reason)
    except ValueError as exc:
        return f"Error: {exc}"
    except KeyError:
        return f"Error: no thread named {name!r}. See `backlog_thread_list()`."
    refusal = _run(call, "thread.update", {"name": name, "status": status, "reason": reason})
    if refusal:
        return refusal
    return call.finish(f"Thread {name} → {status}." + (f" ({reason})" if reason else ""))


@adapter("backlog_continuity_items")
def continuity_items(call, *, view, include_auto_stage):
    backlog = bs._backlog_path()
    if not backlog.exists():
        return json.dumps({"items": [], "view": view, "error": "no backlog"})
    with call.read() as snapshot:
        tree = reads.tree(snapshot)
        items = v3.continuity_items(backlog, include_auto_stage=include_auto_stage,
                                    handover_rows=reads.rows(snapshot, "handover"), data=tree)
    return json.dumps({"items": items, "view": view}, default=str)


@adapter("backlog_last_session")
def last_session(call):
    backlog, legacy_progress = bs._resolve_paths()
    with call.read() as snapshot:
        entity = reads.get(snapshot, "backlog", "__backlog__")
    return bs._last_session_text(bs._progress_path_for(entity["fields"] if entity else {}, backlog, legacy_progress))
