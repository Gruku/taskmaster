"""User intent: close the six defects adversarial review C found in the N09 blocker
collapse and the `locked_by` contract, on both stores (N09 scope §4f):

- a claim only protects work in flight: a move to a terminal status (done,
  archived) releases `locked_by` whoever makes it, and a claim already stranded
  on a terminal task is always releasable;
- a batch `pick` line refuses a peer's claim as `backlog_pick_task` does, and the
  batch preview names unmet dependencies as the resolver does;
- every reader of `depends_on` reads it through one normaliser, so a malformed
  value is reported, never raised on or guessed from;
- viewer writes cannot set `locked_by` and follow the tools' claim rules;
- `next_available` says "1 task", not "1 tasks".
"""
from __future__ import annotations

import json
import os

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from native_twins import hand_edit_task, hand_set_holder, make_twins
from test_native_routing_viewer import same as viewer_same

UP, DOWN = "test-epic-001", "test-epic-002"
LIVE_PEER = f"{store._local_host()}-{os.getpid()}-0badc0de"
# Another machine, no stamped expiry: liveness cannot be judged, so the claim
# never expires on its own.
REMOTE = "other-box-4242-deadbeef"


def _laneless(ident):
    """No lane, so completion is not gated and a test can reach `done` directly."""
    hand_edit_task(ident, lambda doc: doc.pop("lane", None))


def _seed(*, laneless=True):
    bs.backlog_update_epic(epic_id="test-epic", field="status", value="active")
    bs.backlog_add_task(title="Up", epic="test-epic", phase="dev")
    bs.backlog_add_task(title="Down", epic="test-epic", phase="dev")
    if laneless:
        _laneless(UP)
        _laneless(DOWN)


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _peer_picks(twins, holder=LIVE_PEER, task_id=UP):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bs, "SESSION_ID", holder)
        for text in twins.same("backlog_pick_task", task_id=task_id):
            assert text.startswith(f"Picked `{task_id}`"), text


def _holder(twins, task_id=UP):
    return json.loads(twins.same("backlog_claim", action="status", task_id=task_id)[0])["holder"]


def _status(twins, task_id=UP):
    from native_twins import committed
    return committed(twins.legacy)[("task", task_id)][0].get("status")


# ── 1. A terminal status releases the claim, whoever moves the task there ────


TERMINAL_CHANGES = {
    "update_status_done": [("backlog_update_task", {"task_id": UP, "field": "status", "value": "done"})],
    "complete_done": [("backlog_complete_task", {"task_id": UP, "target_status": "done"})],
    "batch_complete": [("backlog_batch_update", {"operations": f"complete {UP}"})],
    "batch_status_done": [("backlog_batch_update", {"operations": f"status {UP} done"})],
    "archive": [("backlog_update_task", {"task_id": UP, "field": "status", "value": "blocked"}),
                ("backlog_archive_task", {"task_id": UP, "reason": "wont-fix"})],
    "batch_archive": [("backlog_update_task", {"task_id": UP, "field": "status", "value": "blocked"}),
                      ("backlog_batch_update", {"operations": f"archive {UP} wont-fix"})],
    "epic_archive": [("backlog_archive_epic", {"epic_id": "test-epic", "reason": "superseded"})],
}


@pytest.mark.parametrize("change", sorted(TERMINAL_CHANGES))
def test_a_terminal_status_releases_a_live_peers_claim(twins, change):
    _peer_picks(twins)
    for tool, kwargs in TERMINAL_CHANGES[change]:
        for text in twins.same(tool, **kwargs):
            assert not text.startswith("Error"), text
    assert _status(twins) in ("done", "archived")
    assert _holder(twins) == ""
    twins.assert_state_matches()


def _stranded(status, holder):
    def seed():
        _seed()
        hand_edit_task(UP, lambda doc: doc.update(status=status, locked_by=holder,
                                                  completed="2026-09-01T00:00:00Z"))
    return seed


@pytest.mark.parametrize("holder", [LIVE_PEER, REMOTE], ids=["live-peer", "unjudgeable"])
def test_the_phase_advance_archive_releases_the_claim_on_both_stores(tmp_path, monkeypatch, holder):
    twins = make_twins(tmp_path, monkeypatch, _stranded("done", holder))
    for text in twins.same("backlog_advance_phase"):
        assert not text.startswith("Error"), text
    assert _status(twins) == "archived"
    assert _holder(twins) == ""
    twins.assert_state_matches()


@pytest.mark.parametrize("status", ["done", "archived"])
@pytest.mark.parametrize("holder", [LIVE_PEER, REMOTE], ids=["live-peer", "unjudgeable"])
def test_a_claim_stranded_on_a_terminal_task_is_always_releasable(tmp_path, monkeypatch, status, holder):
    twins = make_twins(tmp_path, monkeypatch, _stranded(status, holder))
    for text in twins.same("backlog_claim", action="release", task_id=UP):
        answer = json.loads(text)
        assert answer["ok"] is True and answer["state"] == "released", answer
    assert _holder(twins) == ""
    twins.assert_state_matches()


# ── 3. Batch pick lines and the batch preview ───────────────────────────────


def test_a_batch_pick_line_refuses_a_live_peers_claim(tmp_path, monkeypatch):
    def seed():
        _seed()
        hand_set_holder(UP, LIVE_PEER)
    twins = make_twins(tmp_path, monkeypatch, seed)
    for text in twins.same("backlog_batch_update", operations=f"pick {UP}\npick {DOWN}"):
        assert f"`{UP}`: locked by another session (`{LIVE_PEER}`)" in text, text
        assert "backlog_pick_task" in text and "force=true" in text, text
    assert _holder(twins) == LIVE_PEER and _status(twins) == "todo"
    assert _status(twins, DOWN) == "in-progress"
    twins.assert_state_matches()


def _depends(value):
    def seed():
        _seed()
        hand_edit_task(DOWN, lambda doc: doc.__setitem__("depends_on", value))
    return seed


MALFORMED = {"a_number": 5, "a_mapping": {UP: True}, "a_list_holding_a_number": [5]}
PREVIEW_CASES = {"a_missing_id": (["ghost-9"], ["ghost-9"]),
                 **{name: (value, ["depends_on (unreadable)"]) for name, value in MALFORMED.items()}}


@pytest.mark.parametrize("name", sorted(PREVIEW_CASES))
def test_the_batch_preview_names_what_the_resolver_reports(tmp_path, monkeypatch, name):
    value, waited = PREVIEW_CASES[name]
    twins = make_twins(tmp_path, monkeypatch, _depends(value))
    for text in twins.same("backlog_batch_preview", operations=f"pick {DOWN}"):
        assert "Unmet dependencies: " + ", ".join(f"`{i}`" for i in waited) in text, text


# ── 4. Every reader of a malformed `depends_on` ─────────────────────────────


@pytest.mark.parametrize("expand_links", [False, True])
@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_get_task_reports_an_unreadable_depends_on(tmp_path, monkeypatch, name, expand_links):
    twins = make_twins(tmp_path, monkeypatch, _depends(MALFORMED[name]))
    for text in twins.same("backlog_get_task", task_id=DOWN, verbose=True, expand_links=expand_links):
        assert "**Depends on:** unreadable" in text, text


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_validate_reports_an_unreadable_depends_on_as_an_issue(tmp_path, monkeypatch, name):
    twins = make_twins(tmp_path, monkeypatch, _depends(MALFORMED[name]))
    for text in twins.same("backlog_validate"):
        assert f"`{DOWN}`: depends_on is unreadable" in text, text
        assert "All clear" not in text, text


def test_the_viewer_related_panel_reads_a_bare_string_as_one_id(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _depends(UP))
    legacy, _native = viewer_same(twins, "GET", f"/api/task/{DOWN}/related")
    assert [row["id"] for row in json.loads(legacy[1])["dependencies"]] == [UP]
    legacy, _native = viewer_same(twins, "GET", f"/api/task/{UP}/related")
    assert [row["id"] for row in json.loads(legacy[1])["unblocks"]] == [DOWN]


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_the_viewer_related_panel_and_write_gate_survive_a_malformed_row(tmp_path, monkeypatch, name):
    twins = make_twins(tmp_path, monkeypatch, _depends(MALFORMED[name]))
    for path in (f"/api/task/{UP}/related", f"/api/task/{DOWN}/related"):
        legacy, _native = viewer_same(twins, "GET", path)
        assert legacy[0] == 200, legacy
    legacy, _native = viewer_same(twins, "POST", "/api/tasks/validate",
                                  {"task_id": UP, "patch": {"depends_on": []}})
    assert legacy[0] == 200 and json.loads(legacy[1])["ok"] is True, legacy


def test_the_viewer_write_gate_reads_a_patched_depends_on_through_the_normaliser(twins):
    legacy, _native = viewer_same(twins, "POST", "/api/tasks/validate",
                                  {"task_id": DOWN, "patch": {"depends_on": UP}})
    assert json.loads(legacy[1])["ok"] is True, legacy
    legacy, _native = viewer_same(twins, "POST", "/api/tasks/validate",
                                  {"task_id": DOWN, "patch": {"depends_on": 5}})
    assert "depends_on" in json.loads(legacy[1])["errors"], legacy


# ── 5. Viewer writes ────────────────────────────────────────────────────────


def test_a_viewer_patch_cannot_set_or_erase_a_holder(twins):
    _peer_picks(twins)
    viewer_same(twins, "PATCH", f"/api/tasks/{UP}", {"locked_by": "someone-else", "title": "Renamed"})
    viewer_same(twins, "PATCH", f"/api/tasks/{UP}", {"locked_by": None})
    viewer_same(twins, "PATCH", f"/api/tasks/{DOWN}", {"locked_by": "someone-else"})
    viewer_same(twins, "POST", "/api/tasks", {"epic": "test-epic", "title": "New", "locked_by": "someone-else"})
    assert _holder(twins) == LIVE_PEER
    assert _holder(twins, DOWN) == ""
    assert _holder(twins, "test-epic-003") == ""
    twins.assert_state_matches()


def test_a_viewer_status_change_follows_the_tools_claim_rules(twins):
    _peer_picks(twins)
    viewer_same(twins, "PATCH", f"/api/tasks/{UP}", {"status": "blocked"})
    assert _holder(twins) == LIVE_PEER  # a live peer's claim survives a non-terminal change
    viewer_same(twins, "POST", f"/api/tasks/{UP}/archive", {})
    assert _holder(twins) == ""  # a terminal one releases it
    for text in twins.same("backlog_pick_task", task_id=DOWN):
        assert text.startswith(f"Picked `{DOWN}`"), text
    viewer_same(twins, "PATCH", f"/api/tasks/{DOWN}", {"status": "todo"})
    assert _holder(twins, DOWN) == ""  # this session's own claim is released
    twins.assert_state_matches(ignore={("task", UP): {"archived"}})


def test_a_viewer_move_to_done_releases_a_live_peers_claim(twins):
    _peer_picks(twins)
    viewer_same(twins, "PATCH", f"/api/tasks/{UP}", {"status": "done"})
    assert _holder(twins) == ""
    twins.assert_state_matches()


# ── 6. Wording ──────────────────────────────────────────────────────────────


def test_one_claimed_task_is_counted_in_the_singular(tmp_path, monkeypatch):
    def seed():
        _seed()
        hand_set_holder(UP, LIVE_PEER)
    twins = make_twins(tmp_path, monkeypatch, seed)
    for text in twins.same("backlog_next_available"):
        assert "**1 task claimed by another session:**" in text, text
