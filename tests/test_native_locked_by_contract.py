"""User intent: only the claim tools (pick, and `backlog_claim` renew/release)
set or clear `locked_by`, on both stores. A bare field write or a batch line
could name any holder or erase a peer's claim, and a status change silently
dropped a live peer's claim — each moved a claim the claim contract guards.
Decided with the user 2026-09-21 (N09 scope §4e, §4f).
"""
from __future__ import annotations

import json
import os

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from native_twins import make_twins

TASK = "test-epic-001"


def _session(pid):
    return f"{store._local_host()}-{pid}-0badc0de"


def _dead_pid():
    return next(pid for pid in range(4_000_000, 4_100_000) if not store._local_pid_alive(pid))


LIVE_PEER = _session(os.getpid())


@pytest.fixture
def twins(tmp_path, monkeypatch):
    def seed():
        bs.backlog_update_epic(epic_id="test-epic", field="status", value="active")
        bs.backlog_add_task(title="Held", epic="test-epic", phase="dev")
        bs.backlog_add_task(title="Other", epic="test-epic", phase="dev")
    return make_twins(tmp_path, monkeypatch, seed)


def _peer_picks(twins, holder, task_id=TASK):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bs, "SESSION_ID", holder)
        for text in twins.same("backlog_pick_task", task_id=task_id):
            assert text.startswith(f"Picked `{task_id}`"), text


def _holder(twins, task_id=TASK):
    holders = [json.loads(text)["holder"]
               for text in twins.same("backlog_claim", action="status", task_id=task_id)]
    return holders[0]


# ── Only the claim tools write the holder ───────────────────────────────────


@pytest.mark.parametrize("value", ["someone-else", bs.SESSION_ID, "", "none"],
                         ids=["a-peer", "this-session", "empty", "none"])
def test_update_task_refuses_to_write_locked_by_and_names_the_claim_tool(twins, value):
    _peer_picks(twins, LIVE_PEER)
    for text in twins.same("backlog_update_task", task_id=TASK, field="locked_by", value=value):
        assert text.startswith("Error:") and "backlog_claim" in text, text
    assert _holder(twins) == LIVE_PEER
    twins.assert_state_matches()


def test_a_batch_line_writing_locked_by_is_refused_and_the_other_lines_still_apply(twins):
    _peer_picks(twins, LIVE_PEER)
    for text in twins.same("backlog_batch_update",
                           operations=f"update {TASK} locked_by none\n"
                                      f"update test-epic-002 locked_by {LIVE_PEER}\n"
                                      f"update test-epic-002 title Renamed"):
        assert text.count("backlog_claim") == 2, text
    assert _holder(twins) == LIVE_PEER
    for text in twins.same("backlog_claim", action="status", task_id="test-epic-002"):
        assert json.loads(text)["holder"] == ""
    for text in twins.same("backlog_get_task", task_id="test-epic-002"):
        assert "Renamed" in text
    twins.assert_state_matches()


# ── A status change does not drop a live peer's claim ───────────────────────


STATUS_CHANGES = {
    "update_task": [("backlog_update_task", {"task_id": TASK, "field": "status", "value": "blocked"})],
    "batch_status": [("backlog_batch_update", {"operations": f"status {TASK} blocked"})],
    "batch_update_status": [("backlog_batch_update", {"operations": f"update {TASK} status blocked"})],
    "complete_to_review": [
        ("backlog_update_task", {"task_id": TASK, "field": "human_action", "value": "Approve it"}),
        ("backlog_complete_task", {"task_id": TASK, "target_status": "in-review",
                                   "human_action": "Approve it"})],
    "archive": [("backlog_update_task", {"task_id": TASK, "field": "status", "value": "blocked"}),
                ("backlog_archive_task", {"task_id": TASK, "reason": "wont-fix"})],
    "batch_archive": [("backlog_update_task", {"task_id": TASK, "field": "status", "value": "blocked"}),
                      ("backlog_batch_update", {"operations": f"archive {TASK} wont-fix"})],
}


@pytest.mark.parametrize("change", sorted(STATUS_CHANGES))
def test_a_status_change_keeps_a_live_peers_claim(twins, change):
    _peer_picks(twins, LIVE_PEER)
    for tool, kwargs in STATUS_CHANGES[change]:
        for text in twins.same(tool, **kwargs):
            assert not text.startswith("Error"), text
    assert _holder(twins) == LIVE_PEER
    twins.assert_state_matches()


@pytest.mark.parametrize("change", sorted(STATUS_CHANGES))
def test_the_holders_own_status_change_still_releases_its_claim(twins, change):
    for text in twins.same("backlog_pick_task", task_id=TASK):
        assert text.startswith(f"Picked `{TASK}`"), text
    for tool, kwargs in STATUS_CHANGES[change]:
        for text in twins.same(tool, **kwargs):
            assert not text.startswith("Error"), text
    assert _holder(twins) == ""
    twins.assert_state_matches()


def test_a_status_change_still_drops_a_dead_peers_claim(twins):
    _peer_picks(twins, _session(_dead_pid()))
    twins.same("backlog_update_task", task_id=TASK, field="status", value="blocked")
    assert _holder(twins) == ""
    twins.assert_state_matches()
