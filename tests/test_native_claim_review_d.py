"""User intent: close the three issues the second adversarial pass found in the
N09 claim contract, on both stores (N09 scope §4f):

- a viewer write cannot forge a claim's expiry (`claim_expires`,
  `claim_expires_for`) any more than its holder;
- the batch preview refuses a `pick` of a peer's claim exactly as the batch does;
- leaving a terminal status always clears any claim, so a claim stranded on a
  `done` row cannot come back to life when the task is reopened.
"""
from __future__ import annotations

import json
import os

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from native_twins import committed, hand_edit_task, hand_set_holder, make_twins
from test_native_routing_viewer import same as viewer_same

UP, DOWN = "test-epic-001", "test-epic-002"
LIVE_PEER = f"{store._local_host()}-{os.getpid()}-0badc0de"
FORGED = {"claim_expires": "2000-01-01T00:00:00Z", "claim_expires_for": LIVE_PEER}


def _seed():
    bs.backlog_update_epic(epic_id="test-epic", field="status", value="active")
    bs.backlog_add_task(title="Up", epic="test-epic", phase="dev")
    bs.backlog_add_task(title="Down", epic="test-epic", phase="dev")
    for ident in (UP, DOWN):
        hand_edit_task(ident, lambda doc: doc.pop("lane", None))


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _peer_picks(twins, task_id=UP):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bs, "SESSION_ID", LIVE_PEER)
        for text in twins.same("backlog_pick_task", task_id=task_id):
            assert text.startswith(f"Picked `{task_id}`"), text


def _claim(twins, task_id=UP):
    return json.loads(twins.same("backlog_claim", action="status", task_id=task_id)[0])


# ── 1. A viewer write cannot forge a claim's expiry ─────────────────────────


@pytest.mark.parametrize("method", ["PATCH", "PUT"])
def test_a_viewer_write_cannot_forge_a_claims_expiry(twins, method):
    _peer_picks(twins)
    before = _claim(twins)
    assert before["holder"] == LIVE_PEER and before["expired"] is False
    if method == "PUT":
        payload = json.loads(viewer_same(twins, "GET", f"/api/task/{UP}")[0][1])
        payload.update(FORGED, title="Put")
    else:
        payload = dict(FORGED, title="Patched")
    viewer_same(twins, method, f"/api/tasks/{UP}", payload)
    after = _claim(twins)
    assert after["expired"] is False and after["expires_at"] == before["expires_at"], after
    for text in twins.same("backlog_context", focus=UP, include=[]):
        assert ("claim", UP) in {(b["kind"], b["id"]) for b in json.loads(text)["mandatory"]["blockers"]}
    for text in twins.same("backlog_claim", action="release", task_id=UP):
        assert json.loads(text)["error"] == "claim_conflict", text
    twins.assert_state_matches()


def test_a_viewer_create_stores_no_claim_field(twins):
    viewer_same(twins, "POST", "/api/tasks", {"epic": "test-epic", "title": "New",
                                              "locked_by": LIVE_PEER, **FORGED})
    doc = committed(twins.legacy)[("task", "test-epic-003")][0]
    assert not {"locked_by", "claim_expires", "claim_expires_for"} & set(doc), doc
    twins.assert_state_matches()


def test_the_board_form_treats_the_claim_fields_as_system_managed():
    form = (bs.Path(__file__).resolve().parents[1] / "viewer" / "js" / "components" / "edit"
            / "forms" / "task-form.js").read_text(encoding="utf-8")
    managed = form.partition("systemManaged:")[2].partition("]")[0]
    for name in ("locked_by", "claim_expires", "claim_expires_for"):
        assert f"'{name}'" in managed, name


# ── 2. The batch preview refuses a peer's claim as the batch does ───────────


@pytest.mark.parametrize("status", ["todo", "in-progress", "in-review"])
def test_the_batch_preview_refuses_a_pick_of_a_peers_claim_as_the_batch_does(tmp_path, monkeypatch, status):
    def seed():
        _seed()
        hand_edit_task(UP, lambda doc: doc.update(status=status, human_action="Approve it"))
        hand_set_holder(UP, LIVE_PEER)
    twins = make_twins(tmp_path, monkeypatch, seed)
    refusal = f"`{UP}`: locked by another session (`{LIVE_PEER}`)"
    for text in twins.same("backlog_batch_preview", operations=f"pick {UP}"):
        assert f"- {refusal}" in text, text
    for text in twins.same("backlog_batch_update", operations=f"pick {UP}"):
        assert refusal in text, text


# ── 3. Leaving a terminal status clears any claim ───────────────────────────


REOPENERS = {
    "update_task": lambda target: ("backlog_update_task", {"task_id": UP, "field": "status", "value": target}),
    "batch_status": lambda target: ("backlog_batch_update", {"operations": f"status {UP} {target}"}),
    "batch_update_status": lambda target: ("backlog_batch_update",
                                           {"operations": f"update {UP} status {target}"}),
}


# Leaving `archived` is only legal back to `todo`.
REOPENINGS = ([("done", target) for target in ("todo", "in-progress", "blocked", "in-review")]
              + [("archived", "todo")])


@pytest.mark.parametrize("writer", sorted(REOPENERS) + ["viewer"])
@pytest.mark.parametrize("terminal,target", REOPENINGS)
def test_leaving_a_terminal_status_clears_a_stranded_claim(tmp_path, monkeypatch, terminal, writer, target):
    def seed():
        _seed()
        hand_edit_task(UP, lambda doc: doc.update(status=terminal, completed="2026-09-01T00:00:00Z",
                                                  human_action="Approve it", locked_by=LIVE_PEER))
    twins = make_twins(tmp_path, monkeypatch, seed)
    if writer == "viewer":
        viewer_same(twins, "PATCH", f"/api/tasks/{UP}", {"status": target})
    else:
        tool, kwargs = REOPENERS[writer](target)
        twins.same(tool, **kwargs)
    doc = committed(twins.legacy)[("task", UP)][0]
    assert doc.get("status") == target, doc
    assert "locked_by" not in doc, doc
    assert _claim(twins)["holder"] == ""
    twins.assert_state_matches()
