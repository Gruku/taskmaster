"""User intent: on a native store, `backlog_resolve_conflict(take="file")` keeps the
flagged file's version by importing exactly its observed bytes through the explicit
sync path (N13 step 7), with identity, base and changed-file preconditions, and never
erases unrelated rows or unknown authored fields.
"""
from __future__ import annotations

import base64
import json

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native.queries import Repository
from native_twins import make_twins, native_connection

REL = "tasks/test-epic-001.md"


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev", notes="seeded notes")
    bs.backlog_add_task(title="Second", epic="test-epic", phase="dev", notes="other notes")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _path(twins, rel=REL):
    return twins.native / ".taskmaster" / rel


def _owner(twins):
    from tests.native_coordinator_helpers import _owners
    return _owners[twins.native.resolve()]


def _entity(twins, kind="task", ident="test-epic-001"):
    with native_connection(twins.native) as connection, Repository(connection).snapshot() as snapshot:
        entity = snapshot.get(kind, ident, include_body=True)
    return {"revision": entity["revision"], "fields": entity["fields"], "body": entity["body"]}


def _flag(twins, change):
    """Hand-edit the file, then a store write on the same entity meets it and flags it."""
    path = _path(twins)
    edited = change(path.read_bytes())
    path.write_bytes(edited)
    with twins.at(twins.native):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store side")
    assert f"export pending: {REL} is flagged" in answer, answer
    return edited


def _hand_edit(content: bytes) -> bytes:
    first, newline, rest = content.decode("utf-8").partition("\n")  # the export may write CRLF
    text = first + newline + "x_custom: keep me" + ("\r" if first.endswith("\r") else "") + newline + rest
    return (text.replace("title: First", "title: Taken from file") + "\nHand edit the user made.\n").encode("utf-8")


def _flagged(twins):
    with native_connection(twins.native) as connection:
        return [row[0] for row in connection.execute("SELECT file FROM projection_conflict")]


def test_take_file_imports_the_flagged_bytes_and_keeps_unknown_fields(twins):
    edited = _flag(twins, _hand_edit)
    other = _entity(twins, ident="test-epic-002")
    with twins.at(twins.native):
        answer = bs.backlog_resolve_conflict(file=REL, take="file")
        listing = bs.backlog_resolve_conflict()
    assert answer.startswith(f"Resolved {REL}: kept the file version."), answer
    assert listing == "No flagged files."
    task = _entity(twins)
    assert task["fields"]["title"] == "Taken from file"
    assert task["fields"]["x_custom"] == "keep me"
    assert task["fields"]["notes"] == "seeded notes"  # the file's side, not "store side"
    assert "Hand edit the user made." in (task["body"] or "")
    assert _entity(twins, ident="test-epic-002") == other
    # The observed bytes stay in history beside the replaced store values.
    with native_connection(twins.native) as connection:
        before, after = connection.execute(
            "SELECT before,after FROM domain_events WHERE op='sync.apply' ORDER BY seq DESC LIMIT 1").fetchone()
    assert base64.b64decode(json.loads(before)["file_base64"]) == edited
    assert json.loads(after)["mode"] == "apply"
    on_disk = _path(twins).read_text(encoding="utf-8")
    assert "Taken from file" in on_disk and "x_custom: keep me" in on_disk and "Hand edit the user made." in on_disk
    with twins.at(twins.native):
        later = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="after resolve")
    assert "export pending" not in later
    assert "after resolve" in _path(twins).read_text(encoding="utf-8")


def test_take_file_with_the_wrong_identity_refuses_and_changes_nothing(twins):
    edited = _flag(twins, lambda content: content.replace(b"id: test-epic-001", b"id: test-epic-002"))
    before = _entity(twins)
    with twins.at(twins.native):
        answer = bs.backlog_resolve_conflict(file=REL, take="file")
    assert answer.startswith("Error: ") and "Nothing was changed." in answer, answer
    assert _path(twins).read_bytes() == edited
    assert _entity(twins) == before
    assert _flagged(twins) == [REL]


def test_take_file_refuses_when_the_store_changed_after_parse(twins):
    edited = _flag(twins, _hand_edit)
    owner = _owner(twins)
    fired = []

    def checkpoint(stage):
        if stage == "sync_prepared" and not fired:
            fired.append(True)
            owner.submit({"protocol": 2, "store_id": owner.identity["store_id"], "caller_scope": "interleaved",
                          "request_id": "concurrent", "operation": "task.patch",
                          "arguments": {"id": "test-epic-001", "set": {"title": "Concurrent store write"}},
                          "expected_revisions": []}).result(timeout=10)
    owner.checkpoint = checkpoint
    try:
        with twins.at(twins.native):
            answer = bs.backlog_resolve_conflict(file=REL, take="file")
    finally:
        owner.checkpoint = lambda stage: None
    assert fired and answer.startswith("Error: ") and "Nothing was changed." in answer, answer
    assert _path(twins).read_bytes() == edited
    assert _entity(twins)["fields"]["title"] == "Concurrent store write"
    assert _flagged(twins) == [REL]


@pytest.mark.allow_projection_bypass  # the checkpoint edits the file on the coordinator's stack
def test_take_file_refuses_a_file_changed_after_parse(twins):
    edited = _flag(twins, _hand_edit)
    newer = edited.replace(b"Taken from file", b"Edited again")
    owner = _owner(twins)
    owner.checkpoint = lambda stage: _path(twins).write_bytes(newer) if stage == "sync_prepared" else None
    before = _entity(twins)
    try:
        with twins.at(twins.native):
            answer = bs.backlog_resolve_conflict(file=REL, take="file")
    finally:
        owner.checkpoint = lambda stage: None
    assert answer.startswith("Error: ") and "changed after parse" in answer, answer
    assert _path(twins).read_bytes() == newer
    assert _entity(twins) == before
    assert _flagged(twins) == [REL]


def test_take_file_that_does_not_parse_refuses_without_quarantining(twins):
    edited = _flag(twins, lambda content: content.replace(b"title: First", b"title: [unclosed"))
    before = _entity(twins)
    with twins.at(twins.native):
        answer = bs.backlog_resolve_conflict(file=REL, take="file")
    assert answer.startswith("Error: ") and "Nothing was changed." in answer, answer
    assert _path(twins).read_bytes() == edited and _entity(twins) == before
    assert _flagged(twins) == [REL]
    with native_connection(twins.native) as connection:
        assert connection.execute("SELECT quarantined FROM projection WHERE file=?", (REL,)).fetchone()[0] == 0


def test_take_file_on_backlog_yaml_never_erases_absent_rows(twins):
    # Written for a store with no merge base for backlog.yaml. Activation now seeds one
    # (N16); with a base, take_file refuses the removed epic/phase entries outright (sync_prepare
    # `dropped`), which erases nothing either. Pin the premise this test was written for.
    with native_connection(twins.native) as connection:
        connection.execute("DELETE FROM projection_base WHERE file='backlog.yaml'")
    path = _path(twins, "backlog.yaml")
    head, _, _ = path.read_text(encoding="utf-8").partition("epics:\n")
    path.write_text(head + "x_team_note: kept\nepics: []\n", encoding="utf-8")
    with twins.at(twins.native):
        flagged = bs.backlog_update_epic(epic_id="test-epic", field="name", value="Renamed")
        assert "export pending: backlog.yaml is flagged" in flagged
        answer = bs.backlog_resolve_conflict(file="backlog.yaml", take="file")
    assert answer.startswith("Resolved backlog.yaml: kept the file version."), answer
    with native_connection(twins.native) as connection:
        live = set(connection.execute(
            "SELECT kind,public_id FROM entity_core WHERE deleted=0 AND kind IN ('epic','phase','task')"))
    assert {("epic", "test-epic"), ("phase", "dev"), ("task", "test-epic-001"), ("task", "test-epic-002")} <= live
    assert _entity(twins, "backlog", "__backlog__")["fields"]["x_team_note"] == "kept"
    assert _flagged(twins) == []


def _with_inline_task_stubs(path):
    """backlog.yaml naming task rows under epics[].tasks, the legacy inline shape."""
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    epic = next(epic for epic in data["epics"] if epic["id"] == "test-epic")
    epic["tasks"] = [{"id": "test-epic-001", "title": "Stub"}, {"id": "test-epic-099", "title": "Invented"}]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path.read_bytes()


def test_take_file_on_backlog_yaml_never_replaces_task_rows_from_inline_stubs(twins):
    path = _path(twins, "backlog.yaml")
    edited = _with_inline_task_stubs(path)
    before = _entity(twins)
    assert len(before["fields"]) > 3
    with twins.at(twins.native):
        flagged = bs.backlog_update_epic(epic_id="test-epic", field="name", value="Renamed")
        assert "export pending: backlog.yaml is flagged" in flagged
        answer = bs.backlog_resolve_conflict(file="backlog.yaml", take="file")
    assert answer.startswith("Error: ") and "task" in answer and "Nothing was changed." in answer, answer
    assert _entity(twins) == before  # not {epic, id, title: Stub}
    with native_connection(twins.native) as connection:
        assert connection.execute("SELECT 1 FROM entity_core WHERE public_id='test-epic-099'").fetchone() is None
    assert path.read_bytes() == edited
    assert _flagged(twins) == ["backlog.yaml"]


def test_take_file_names_its_import_even_when_other_pending_results_fill_the_budget():
    from taskmaster.coordinator import sync_worker
    others = [f"tasks/t-{n:05}.md" for n in range(6000)]
    result = dict(state="pending", unresolved=list(others),
                  notices=[f"sync pending: {rel}: flagged" for rel in others],
                  imports=[dict(file=REL, state="accepted", reason="explicit file resolution", commit_seq=9,
                                request_id="k" * 64, caller_scope="sync-" + "a" * 64)],
                  warnings=[], receipt_scope="s")
    assert sync_worker.bound(result)["imports_omitted"] == 1  # the budget really is exhausted
    bounded = sync_worker.bound(result, named=[REL])
    assert bounded["imports"] == result["imports"]
    assert bounded["notices_omitted"] > 0


def test_take_file_with_its_import_omitted_reports_an_unknown_outcome(twins, monkeypatch):
    from taskmaster.coordinator.adapter import NativeCall
    _flag(twins, _hand_edit)
    monkeypatch.setattr(NativeCall, "sync", lambda self, files, take_file=False: dict(
        state="pending", imports=[], imports_omitted=1, unresolved=[], notices=[], receipt_scope="sync-scope"))
    with twins.at(twins.native):
        answer = bs.backlog_resolve_conflict(file=REL, take="file")
    assert answer.startswith("Error: ") and "unknown" in answer and "sync-scope" in answer, answer
    assert "Nothing was changed" not in answer


def test_take_file_retried_after_a_lost_reply_reports_the_committed_resolution(twins, monkeypatch):
    from taskmaster.coordinator.client import Client
    _flag(twins, _hand_edit)
    other = _path(twins, "tasks/test-epic-002.md")  # a second flag keeps each sync run pending
    other.write_bytes(other.read_bytes().replace(b"title: Second", b"title: Second edited"))
    with twins.at(twins.native):
        assert "is flagged" in bs.backlog_update_task(task_id="test-epic-002", field="notes", value="store")
    real, lost = Client._send, []

    def send(self, record, method, **arguments):
        answer = real(self, record, method, **arguments)
        if method == "sync" and not lost:
            lost.append(answer)
            raise ConnectionError("reply lost after the coordinator committed")
        return answer
    monkeypatch.setattr(Client, "_send", send)
    with twins.at(twins.native):
        answer = bs.backlog_resolve_conflict(file=REL, take="file")
    assert lost and lost[0]["imports"][0]["state"] == "accepted"
    assert answer.startswith(f"Resolved {REL}: kept the file version."), answer
    assert _entity(twins)["fields"]["title"] == "Taken from file"
    assert _flagged(twins) == ["tasks/test-epic-002.md"]
