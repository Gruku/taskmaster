"""User intent: `backlog_changes_since` is what lets a session resume by asking
what moved instead of re-reading the whole backlog, so it must work on the stores
that exist today — every real project is still legacy — and answer the same shape
on both, with a cursor that never crosses between them.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins


@pytest.fixture
def twins(tmp_path, monkeypatch):
    def seed():
        bs.backlog_note(action="create", text="Seeded note", pinned=False)
    return make_twins(tmp_path, monkeypatch, seed)


def answer(root, twins_, **kwargs):
    with twins_.at(root):
        return json.loads(bs.backlog_changes_since(**kwargs))


KEYS = {"store_id", "sequence", "commits", "cursor", "more", "resync_required", "reason"}


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_first_call_answers_a_cursor_only_and_the_next_call_reports_what_moved(twins, side):
    root = getattr(twins, side)
    start = answer(root, twins, )
    assert set(start) == KEYS and start["commits"] == [] and start["more"] is False
    assert start["resync_required"] is False and start["reason"] is None and start["cursor"]
    with twins.at(root):
        bs.backlog_note(action="create", text="Moved after the cursor", pinned=False)
    moved = answer(root, twins, cursor=start["cursor"])
    changes = [(c["kind"], c["id"], c["op"]) for commit in moved["commits"] for c in commit["changes"]]
    assert ("note", "NOTE-002", "create") in changes
    assert moved["resync_required"] is False


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_history_can_be_read_from_the_start_and_paged_without_losing_a_change(twins, side):
    root = getattr(twins, side)
    whole = answer(root, twins, since_seq=0, limit=500)
    every = [(c["kind"], c["id"], c["op"]) for commit in whole["commits"] for c in commit["changes"]]
    assert every and whole["more"] is False
    paged, cursor = [], answer(root, twins, since_seq=0, limit=1)
    while True:
        paged += [(c["kind"], c["id"], c["op"]) for commit in cursor["commits"] for c in commit["changes"]]
        if not cursor["more"]:
            break
        cursor = answer(root, twins, cursor=cursor["cursor"], limit=1)
    assert paged == every


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_group_commits_false_answers_changes_instead_of_commits(twins, side):
    root = getattr(twins, side)
    flat = answer(root, twins, since_seq=0, limit=500, group_commits=False)
    assert "commits" not in flat and flat["changes"]
    assert set(flat["changes"][0]) == {"seq", "ts", "session", "operation", "kind", "id", "op", "fields"}


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_an_unusable_cursor_answers_resync_with_a_typed_reason_not_an_error(twins, side):
    root = getattr(twins, side)
    tampered = answer(root, twins, cursor="not-a-cursor-this-store-issued")
    assert tampered["resync_required"] is True and tampered["reason"] == "cursor_unreadable"
    assert tampered["commits"] == [] and tampered["cursor"]
    start = answer(root, twins)
    rescoped = answer(root, twins, cursor=start["cursor"], kinds=["task"])
    assert rescoped["resync_required"] is True and rescoped["reason"] == "scope_changed"
    # One call recovers: the fresh cursor is answerable in the new scope.
    assert answer(root, twins, cursor=rescoped["cursor"], kinds=["task"])["resync_required"] is False


def test_a_cursor_cannot_cross_between_the_legacy_store_and_its_native_copy(twins):
    """The twins hold the same entities and the same `creation_token`, so only the
    digest tells them apart — and it must, or a cursor would resume against
    history that is not the one it was issued for."""
    legacy_cursor = answer(twins.legacy, twins)["cursor"]
    crossed = answer(twins.native, twins, cursor=legacy_cursor)
    assert crossed["resync_required"] is True and crossed["reason"] == "store_rebuilt"


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_bad_arguments_refuse_as_json_on_both_stores(twins, side):
    root = getattr(twins, side)
    for kwargs in ({"kinds": ["nonsense"]}, {"limit": 0}, {"limit": 501},
                   {"cursor": "x", "since_seq": 1}, {"since_seq": -1}):
        refusal = answer(root, twins, **kwargs)
        assert set(refusal) == {"error"} and refusal["error"]


def test_the_two_stores_report_the_same_event_stream(twins):
    """Twin parity for the shared shape: grouping is a native fact the legacy
    `changes` table cannot express, but the events themselves must agree."""
    for root in (twins.legacy, twins.native):
        with twins.at(root):
            bs.backlog_note(action="create", text="A change on both sides", pinned=True)

    def stream(root):
        flat = answer(root, twins, since_seq=0, limit=500, group_commits=False)
        return [(c["kind"], c["id"], c["op"], c["fields"]) for c in flat["changes"]]

    assert stream(twins.native) == stream(twins.legacy)


def test_the_legacy_store_reports_each_change_as_its_own_commit(twins):
    """Stated rather than implied: `changes` has no commit row to group by, so a
    legacy multi-entity write reads as several single-entity commits."""
    whole = answer(twins.legacy, twins, since_seq=0, limit=500)
    for commit in whole["commits"]:
        assert len(commit["changes"]) == 1
        assert commit["first_seq"] == commit["final_seq"] == commit["commit_seq"]


# ── Epic scope is membership at the time of each event (review B, item 3) ────


def _move(twins_, root, task_id, epic):
    """Reassign a task's epic the one way either store can: the viewer's write."""
    if root == twins_.native:
        from taskmaster.native_routing import runtime

        with runtime.open_call(root / ".taskmaster" / "local" / "store.db", root / ".taskmaster",
                               bs.SESSION_ID) as call:
            call.execute("task.viewer_update", {"id": task_id, "patch": {"epic": epic}, "if_match": ""})
    else:
        bs._viewer_update_task(task_id, {"epic": epic})


@pytest.fixture
def epic_twins(tmp_path, monkeypatch):
    def seed():
        bs.backlog_add_epic(epic_id="other-epic", name="Other", done_when="x")
        bs.backlog_add_task(title="Mover", epic="test-epic", phase="dev")
        bs.backlog_add_task(title="Stayer", epic="test-epic", phase="dev")
        bs.backlog_add_task(title="Joiner", epic="other-epic", phase="dev")
    return make_twins(tmp_path, monkeypatch, seed)


@pytest.mark.parametrize("grouped", [False, True])
def test_an_epic_scope_keeps_what_happened_inside_it_and_nothing_from_outside(epic_twins, grouped):
    """A change made while a task was in the epic stays in the epic's feed after
    the task leaves; a change made before a task joined never enters it."""
    streams = {}
    for side in ("legacy", "native"):
        root = getattr(epic_twins, side)
        start = answer(root, epic_twins, epic="test-epic", group_commits=grouped)["cursor"]
        with epic_twins.at(root):
            bs.backlog_update_task(task_id="test-epic-001", field="priority", value="high")
            bs.backlog_update_task(task_id="other-epic-001", field="priority", value="high")
            _move(epic_twins, root, "test-epic-001", "other-epic")
            bs.backlog_update_task(task_id="test-epic-001", field="notes", value="after leaving")
            _move(epic_twins, root, "other-epic-001", "test-epic")
            bs.backlog_update_task(task_id="other-epic-001", field="notes", value="after joining")
        got = answer(root, epic_twins, cursor=start, epic="test-epic", group_commits=grouped)
        changes = ([c for commit in got["commits"] for c in commit["changes"]] if grouped
                   else got["changes"])
        streams[side] = [(c["id"], "epic" in c["fields"], "priority" in c["fields"], "notes" in c["fields"])
                         for c in changes]
    expected = [("test-epic-001", False, True, False),    # made inside, before leaving
                ("test-epic-001", True, False, False),    # the move out
                ("other-epic-001", True, False, False),   # the move in
                ("other-epic-001", False, False, True)]   # made inside, after joining
    assert streams["legacy"] == expected, streams
    assert streams["native"] == expected, streams
