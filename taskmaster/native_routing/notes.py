# User intent: sticky notes are the first family the native core serves for real
# (N08); every answer, refusal and file must match the legacy note tools exactly.
"""`backlog_note` over the native core."""
from __future__ import annotations

from taskmaster import backlog_server as bs
from taskmaster.taskmaster_v3 import apply_note_updates, build_note_doc, note_path

from .registry import adapter
from .runtime import error_text


def _note(call, note_id: str):
    with call.read() as snapshot:
        try:
            entity = snapshot.get("note", note_id, include_body=True)
        except KeyError:
            return None
    return entity


def _records(call, include_archived: bool) -> list[dict]:
    records, cursor = [], None
    with call.read() as snapshot:
        while True:
            page = snapshot.list("note", fields=None, include_archived=include_archived, limit=500, cursor=cursor)
            for entity in page["items"]:
                records.append(entity)
            cursor = page["cursor"]
            if cursor is None:
                break
        keys = [(entity["kind"], entity["id"]) for entity in records]
        bodies = {}
        for kind, ident in keys:
            bodies[ident] = snapshot.get(kind, ident, fields=[], include_body=True)["body"]
    out = []
    for entity in records:
        if not include_archived and entity["fields"].get("archived"):
            continue
        out.append({**entity["fields"], "body": (bodies[entity["id"]] or "").rstrip("\n")})
    return bs._order_note_records(out)


@adapter("backlog_note", actions=("create", "list", "get", "update", "archive"))
def backlog_note(call, *, action, note_id, text, pinned, include_archived, limit):
    if action == "create":
        return _create(call, text, bool(pinned))
    if not bs._backlog_path().exists():
        return "No backlog found."
    if action == "list":
        return bs._render_note_list(_records(call, include_archived), limit)
    if action == "get":
        entity = _note(call, note_id)
        if entity is None:
            return f"Note not found: {note_id}"
        return bs._render_note_get(entity["fields"], entity["body"])
    if action == "update":
        return _update(call, note_id, text or None, pinned)
    return _archive(call, note_id)


def _create(call, text: str, pinned: bool) -> str:
    backlog = bs._backlog_path()
    if not backlog.exists():
        return f"Error: no backlog found at {backlog}. Run `backlog_init` first."
    try:
        build_note_doc(text=text, author="claude", pinned=pinned)
    except ValueError as exc:
        return f"Error: {exc}"
    try:
        receipt = call.execute("note.create", {"text": text, "author": "claude", "pinned": pinned})
    except (ValueError, KeyError) as exc:
        return error_text(exc)
    nid = receipt["affected"][0]["id"]
    target = note_path(backlog, nid)
    try:
        rel = target.relative_to(bs.ROOT)
    except ValueError:
        rel = target
    return call.finish(f"Note created: {nid}\nFile: {rel}")


def _update(call, note_id: str, text, pinned) -> str:
    entity = _note(call, note_id)
    if entity is None:
        return f"Note not found: {note_id}"
    try:
        apply_note_updates(entity["fields"], entity["body"] or "", text=text, pinned=pinned)
    except ValueError as exc:
        return f"Error: {exc}"
    arguments = {"id": note_id}
    if text is not None:
        arguments["text"] = text
    if pinned is not None:
        arguments["pinned"] = pinned
    try:
        call.execute("note.update", arguments)
    except (ValueError, KeyError) as exc:
        return error_text(exc)
    return call.finish(f"Note updated: {note_id}")


def _archive(call, note_id: str) -> str:
    if _note(call, note_id) is None:
        return f"Note not found: {note_id}"
    try:
        call.execute("note.archive", {"id": note_id})
    except (ValueError, KeyError) as exc:
        return error_text(exc)
    return call.finish(f"Note archived: {note_id}")
