# User intent: keep the pre-store entity tests asserting the same behaviour after
# step 3 deleted `taskmaster_v3`'s writers, by offering the same call shapes
# backed by real store transactions instead of raw markdown writes.
"""Store-backed stand-ins for the entity writers `taskmaster_v3` used to own.

Every function here commits through `taskmaster.store`, so a test that seeds a
bug or a handover exercises the same path production does. Signatures match the
deleted `taskmaster_v3` writers so the test suites that predate the migration
keep testing behaviour rather than being rewritten around it.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from taskmaster import store
from taskmaster import taskmaster_v3 as tm


@contextmanager
def transaction(backlog_path: Path, tool: str = "test:entity-helper"):
    """The caller's open transaction, or one of our own."""
    active = store.active_transaction()
    if active is not None:
        yield active
        return
    with store.open_store(Path(backlog_path)).transaction(tool=tool) as tx:
        yield tx


@contextmanager
def server_transaction(backlog_path: Path, tool: str = "test:entity-helper"):
    """A full server transaction frame pointed at `backlog_path`.

    The composite creates below call `backlog_server`'s own `_*_in_tx` helpers
    rather than restating what they do, and those need the server's frame
    (`_load`, `_mutate_and_save`) and its path resolver. Both are restored on
    the way out.
    """
    from taskmaster import backlog_server as bs

    resolved = Path(backlog_path)
    original = bs._backlog_path
    bs._backlog_path = lambda: resolved
    try:
        if bs._active_tx() is not None:
            yield bs
            return
        with bs._transaction(tool=tool, backlog_path=resolved) as data:
            yield bs
            # The block's row writes are discarded unless something latches;
            # the helpers latch themselves, and this covers a read-only body.
            bs._mutate_and_save(data)
    finally:
        bs._backlog_path = original


def _doc_and_body(tx, kind: str, ident: str) -> tuple[dict[str, Any], str]:
    doc = tx.get(kind, ident)
    return doc, doc.pop(tm.BODY_KEY, "") or ""


def rows(backlog_path: Path, kind: str, *, include_archived: bool = False) -> list:
    """`(id, doc, body)` rows of one kind, straight off the store."""
    with transaction(backlog_path, tool="test:list") as tx:
        return tx.list(kind, include_archived=include_archived)


# ── handovers ────────────────────────────────────────────────────────────


def write_handover(backlog_path: Path, **kwargs) -> tuple[str, Path]:
    """Seed one handover through the server's own create path."""
    with server_transaction(backlog_path, tool="test:write_handover") as bs:
        outcome = bs._handover_create_in_tx(**kwargs)
    if isinstance(outcome, str):
        raise ValueError(outcome)
    return outcome[0], tm.handover_path(backlog_path, outcome[0])


def apply_supersession(backlog_path: Path, *, old_id: str, new_id: str) -> Path:
    with transaction(backlog_path) as tx:
        if not tx.id_taken("handover", new_id):
            raise FileNotFoundError(new_id)
        try:
            doc, body = _doc_and_body(tx, "handover", old_id)
        except KeyError as exc:
            raise FileNotFoundError(old_id) from exc
        new_doc, new_body = tm.supersede_handover_doc(doc, body, new_id=new_id)
        tx.put("handover", old_id, new_doc, body=new_body)
    return tm.handover_path(backlog_path, old_id)


def apply_handover_review_flag(
    backlog_path: Path, *, handover_id: str, review_reason: str
) -> Path:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "handover", handover_id)
        except KeyError as exc:
            raise FileNotFoundError(handover_id) from exc
        tx.put(
            "handover",
            handover_id,
            tm.flag_handover_doc_for_review(doc, review_reason=review_reason),
            body=body,
        )
    return tm.handover_path(backlog_path, handover_id)


def update_handover_status(
    backlog_path: Path, *, handover_id: str, status: str, reason: str = ""
) -> tuple[dict[str, Any], Path]:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "handover", handover_id)
        except KeyError as exc:
            raise FileNotFoundError(handover_id) from exc
        updated = tm.set_handover_status_doc(doc, status=status, reason=reason)
        tx.put("handover", handover_id, updated, body=body)
    return updated, tm.handover_path(backlog_path, handover_id)


def archive_handover(backlog_path: Path, handover_id: str) -> Path:
    with transaction(backlog_path) as tx:
        try:
            tx.archive("handover", handover_id)
        except KeyError as exc:
            raise FileNotFoundError(handover_id) from exc
    year = handover_id[:4] if handover_id[:4].isdigit() else "unknown"
    return tm.handover_dir(backlog_path) / "_archive" / year / f"{handover_id}.md"


def smart_auto_close_handovers(
    backlog_path: Path, *, triggering_task_id: str, done_or_archived_ids: set
) -> dict[str, list[str]]:
    with transaction(backlog_path) as tx:
        plan = tm.smart_auto_close_handovers(
            tx.list("handover"),
            triggering_task_id=triggering_task_id,
            done_or_archived_ids=done_or_archived_ids,
        )
        for hid, doc, body in plan["closed"] + plan["flagged"]:
            tx.put("handover", hid, doc, body=body)
    return {
        "closed": [hid for hid, _doc, _body in plan["closed"]],
        "flagged": [hid for hid, _doc, _body in plan["flagged"]],
    }


def backfill_handover_status(backlog_data: dict, backlog_path: Path) -> list[str]:
    with transaction(backlog_path) as tx:
        flipped = tm.backfill_handover_status(
            backlog_data, tx.list("handover", include_archived=True)
        )
        for hid, doc, body in flipped:
            tx.put("handover", hid, doc, body=body)
    return [hid for hid, _doc, _body in flipped]


def migrate_handover_statuses(
    backlog_data: dict, backlog_path: Path, *, done_or_archived_ids: set
) -> dict[str, list[str]]:
    with transaction(backlog_path) as tx:
        plan = tm.migrate_handover_statuses(
            backlog_data,
            tx.list("handover", include_archived=True),
            done_or_archived_ids=done_or_archived_ids,
        )
        for hid, doc, body in plan["migrated"]:
            tx.put("handover", hid, doc, body=body)
    return {"migrated": [hid for hid, _doc, _body in plan["migrated"]]}


def backfill_threads(backlog_path: Path, backlog_data: dict | None = None) -> dict:
    with transaction(backlog_path) as tx:
        plan = tm.backfill_threads(tx.list("handover"), backlog_data=backlog_data)
        for hid, doc, body in plan["stamped"]:
            tx.put("handover", hid, doc, body=body)
    return {"stamped": [hid for hid, _doc, _body in plan["stamped"]], "groups": plan["groups"]}


def sync_handover_index(backlog_data: dict, backlog_path: Path, cap: int | None = None) -> dict:
    with transaction(backlog_path) as tx:
        kwargs = {} if cap is None else {"cap": cap}
        return tm.sync_handover_index(backlog_data, tx.list("handover"), tx=tx, **kwargs)


def sync_thread_registry(backlog_data: dict, backlog_path: Path) -> dict:
    return tm.sync_thread_registry(backlog_data, rows(backlog_path, "handover"))


# ── bugs ─────────────────────────────────────────────────────────────────


def write_bug(backlog_path: Path, **kwargs) -> tuple[str, Path]:
    body = kwargs.pop("body", "")
    doc = tm.build_bug_doc(**kwargs)
    with transaction(backlog_path) as tx:
        bid = tx.create("bug", doc, body=body)
    return bid, tm.bug_path(backlog_path, bid)


def update_bug(backlog_path: Path, bug_id: str, **updates) -> tuple[dict[str, Any], str]:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "bug", bug_id)
        except KeyError as exc:
            raise FileNotFoundError(bug_id) from exc
        new_body = updates.pop("body", body)
        fm = tm.apply_bug_updates(doc, **updates)
        tx.put("bug", bug_id, fm, body=new_body)
    return fm, new_body


def archive_bug(backlog_path: Path, bug_id: str) -> Path:
    with transaction(backlog_path) as tx:
        try:
            doc, _body = _doc_and_body(tx, "bug", bug_id)
        except KeyError as exc:
            raise FileNotFoundError(bug_id) from exc
        tm.assert_bug_archivable(doc)
        tx.archive("bug", bug_id)
    return tm.bug_path(backlog_path, bug_id, archived=True)


def sync_bug_index(backlog_data: dict, backlog_path: Path) -> dict:
    return tm.sync_bug_index(backlog_data, rows(backlog_path, "bug"))


def promote_bugs_to_issue(
    backlog_path: Path,
    *,
    bug_ids: list,
    title: str,
    severity: str,
    evidence_text: str,
    components: list | None = None,
    body: str = "",
) -> str:
    """Promote bugs through the server's own composite, not a copy of it."""
    with server_transaction(backlog_path, tool="test:promote_bugs") as bs:
        result = bs._promote_bugs_in_tx(
            bug_ids=list(bug_ids),
            title=title,
            severity=severity,
            evidence_text=evidence_text,
            components=components,
            body=body,
        )
    if isinstance(result, str):
        raise ValueError(result)
    return result[0]


# ── issues ───────────────────────────────────────────────────────────────


def write_issue(backlog_path: Path, **kwargs) -> tuple[str, Path]:
    body = kwargs.pop("body", "")
    doc = tm.build_issue_doc(**kwargs)
    with transaction(backlog_path) as tx:
        iid = tx.create("issue", doc, body=body)
    return iid, tm.issue_path(backlog_path, iid)


def update_issue(backlog_path: Path, issue_id: str, **updates) -> tuple[dict[str, Any], str]:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "issue", issue_id)
        except KeyError as exc:
            raise FileNotFoundError(issue_id) from exc
        new_body = updates.pop("body", body)
        fm = tm.apply_issue_updates(doc, **updates)
        tx.put("issue", issue_id, fm, body=new_body)
    return fm, new_body


def sync_issue_index(backlog_data: dict, backlog_path: Path) -> dict:
    return tm.sync_issue_index(backlog_data, rows(backlog_path, "issue"))


# ── decisions ────────────────────────────────────────────────────────────


def write_decision(backlog_path: Path, **kwargs) -> tuple[str, Path]:
    body = kwargs.pop("body", "")
    doc = tm.build_decision_doc(**kwargs)
    with transaction(backlog_path) as tx:
        did = tx.create("decision", doc, body=body)
    return did, tm.decision_path(backlog_path, did)


def update_decision(backlog_path: Path, decision_id: str, patch: dict) -> dict[str, Any]:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "decision", decision_id)
        except KeyError as exc:
            raise FileNotFoundError(decision_id) from exc
        fm = tm.apply_decision_patch(doc, patch)
        tx.put("decision", decision_id, fm, body=body)
    return fm


def resolve_decision(backlog_path: Path, decision_id: str, **kwargs) -> dict[str, Any]:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "decision", decision_id)
        except KeyError as exc:
            raise FileNotFoundError(decision_id) from exc
        fm = tm.resolve_decision_doc(doc, **kwargs)
        tx.put("decision", decision_id, fm, body=body)
    return fm


def drop_decision(backlog_path: Path, decision_id: str, *, reason: str) -> dict[str, Any]:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "decision", decision_id)
        except KeyError as exc:
            raise FileNotFoundError(decision_id) from exc
        fm = tm.drop_decision_doc(doc, reason=reason)
        tx.put("decision", decision_id, fm, body=body)
    return fm


def link_decision_to_handover(
    backlog_path: Path, decision_id: str, handover_id: str
) -> dict[str, Any]:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "decision", decision_id)
        except KeyError as exc:
            raise FileNotFoundError(decision_id) from exc
        linked = tm.link_decision_doc_to_handover(doc, handover_id)
        if linked is None:
            return doc
        tx.put("decision", decision_id, linked, body=body)
    return linked


# ── ideas ────────────────────────────────────────────────────────────────


def write_idea(backlog_path: Path, **kwargs) -> tuple[str, Path]:
    body = kwargs.pop("body", "")
    doc = tm.build_idea_doc(**kwargs)
    with transaction(backlog_path) as tx:
        iid = tx.create("idea", doc, body=body)
    return iid, tm.idea_path(backlog_path, iid)


def update_idea(backlog_path: Path, idea_id: str, **updates) -> tuple[dict[str, Any], str]:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "idea", idea_id)
        except KeyError as exc:
            raise FileNotFoundError(f"Idea not found: {idea_id}") from exc
        new_body = updates.pop("body", body)
        archived = updates.pop("archived", None)
        fm = tm.apply_idea_updates(doc, **updates)
        tx.put("idea", idea_id, fm, body=new_body)
        if archived is True:
            tx.archive("idea", idea_id)
            fm["archived"] = True
        elif archived is False:
            tx.unarchive("idea", idea_id)
            fm.pop("archived", None)
    return fm, new_body


def list_ideas(backlog_path: Path, **kwargs):
    from taskmaster import backlog_server as bs

    return bs._idea_records(store.open_store(Path(backlog_path)).load_dict(), **kwargs)


# ── notes ────────────────────────────────────────────────────────────────


def write_note(backlog_path: Path, **kwargs) -> tuple[str, Path]:
    doc, body = tm.build_note_doc(**kwargs)
    with transaction(backlog_path) as tx:
        nid = tx.create("note", doc, body=body)
    return nid, tm.note_path(backlog_path, nid)


def update_note(
    backlog_path: Path, note_id: str, *, text: str | None = None, pinned: bool | None = None
) -> tuple[dict[str, Any], str]:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "note", note_id)
        except KeyError as exc:
            raise FileNotFoundError(f"Note not found: {note_id}") from exc
        fm, new_body = tm.apply_note_updates(doc, body, text=text, pinned=pinned)
        tx.put("note", note_id, fm, body=new_body)
    return fm, new_body


def archive_note(backlog_path: Path, note_id: str) -> dict[str, Any]:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "note", note_id)
        except KeyError as exc:
            raise FileNotFoundError(f"Note not found: {note_id}") from exc
        fm = tm.archive_note_doc(doc)
        tx.put("note", note_id, fm, body=body)
        tx.archive("note", note_id)
        fm["archived"] = True
    return fm


def list_notes(backlog_path: Path, include_archived: bool = False):
    from taskmaster import backlog_server as bs

    return bs._note_records(
        store.open_store(Path(backlog_path)).load_dict(), include_archived=include_archived
    )


# ── areas ────────────────────────────────────────────────────────────────


def write_area(backlog_path: Path, fm: dict, body: str = "") -> Path:
    doc = tm.validate_area_doc(fm)
    with transaction(backlog_path) as tx:
        if tx.id_taken("area", doc["id"]):
            raise ValueError(f"area `{doc['id']}` already exists")
        tx.create("area", doc, body=body)
    return tm.area_path(backlog_path, doc["id"])


def update_area(backlog_path: Path, area_id: str, updates: dict) -> tuple[dict[str, Any], str]:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "area", area_id)
        except KeyError as exc:
            raise FileNotFoundError(f"Area not found: {area_id}") from exc
        fm = tm.apply_area_updates(doc, updates)
        tx.put("area", area_id, fm, body=body)
    return fm, body


def list_areas(backlog_path: Path) -> list[dict[str, Any]]:
    return [dict(doc) for _aid, doc, _body in rows(backlog_path, "area")]


# ── trackers ─────────────────────────────────────────────────────────────


def write_tracker(backlog_path: Path, **kwargs) -> tuple[str, Path]:
    body = kwargs.pop("body", "")
    doc = tm.build_tracker_doc(**kwargs)
    tid = doc["id"]
    with transaction(backlog_path) as tx:
        # Upsert by id, the way the writer this replaced behaved: the same
        # (system, alias, key) triple always resolves to one tracker.
        if tx.id_taken("tracker", tid):
            tx.put("tracker", tid, doc, body=body)
        else:
            tx.create("tracker", doc, body=body)
    return tid, tm.tracker_path(backlog_path, tid)


def update_tracker(backlog_path: Path, tracker_id: str, **updates) -> tuple[dict[str, Any], str]:
    with transaction(backlog_path) as tx:
        try:
            doc, body = _doc_and_body(tx, "tracker", tracker_id)
        except KeyError as exc:
            raise FileNotFoundError(tracker_id) from exc
        if doc.get("id") != tracker_id:
            raise ValueError(
                f"stored tracker id {doc.get('id')!r} does not match requested {tracker_id!r}"
            )
        new_body = updates.pop("body", body)
        fm = tm.apply_tracker_updates(doc, **updates)
        tx.put("tracker", tracker_id, fm, body=new_body)
    return fm, new_body


def sync_tracker_index(backlog_data: dict, backlog_path: Path) -> dict:
    return tm.sync_tracker_index(backlog_data, rows(backlog_path, "tracker"))


def taskmaster_backlog(tmp_path: Path) -> Path:
    """A canonical `<tmp>/.taskmaster/backlog.yaml` path, its directory created.

    The store refuses a backlog outside `.taskmaster/`, so fixtures written
    before it existed point here instead of at the bare temp directory.
    """
    backlog_path = Path(tmp_path) / ".taskmaster" / "backlog.yaml"
    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    return backlog_path
