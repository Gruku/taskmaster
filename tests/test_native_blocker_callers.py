"""User intent: every tool that answers "is this task blocked, available or
pickable" gives the answer the one mandatory-blocker resolver gives, on both
stores. N09 built `native.blockers` to collapse the divergent readings; this
pins the callers to it, so a fourth reading cannot quietly reappear.

The resolver's answer is read through `backlog_context`, the tool that already
calls it. Every other reader — `backlog_next_available`, the `next_up` list
`backlog_status` shows, `backlog_pick_task`'s unmet-dependency warning and its
claim refusal, and `backlog_dependencies` — must agree with it.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
import re
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from native_twins import make_twins

UP, DOWN = "test-epic-001", "test-epic-002"


def _patch(ident, change):
    """Hand-edit one legacy task row, the way a hand-edited or half-migrated
    backlog arrives. Done before the twins are copied, so both stores carry it."""
    store.reset_for_tests()
    with closing(sqlite3.connect(bs.ROOT / ".taskmaster" / "local" / "store.db",
                                 isolation_level=None)) as connection:
        doc = json.loads(connection.execute(
            "SELECT doc FROM entities WHERE kind='task' AND id=?", (ident,)).fetchone()[0])
        change(doc)
        connection.execute("UPDATE entities SET doc=? WHERE kind='task' AND id=?",
                           (json.dumps(doc), ident))
    store.reset_for_tests()


def _depends(value):
    return lambda: _patch(DOWN, lambda doc: doc.__setitem__("depends_on", value))


def _up_status(status):
    def change(doc):
        if status is None:
            doc.pop("status", None)
        else:
            doc["status"] = status
    return lambda: (_depends([UP])(), _patch(UP, change))


# name -> (hand edit, is DOWN blocked by its dependencies?)
DEPENDENCY_CASES = {
    "no_dependencies": (lambda: None, False),
    "met": (_up_status("done"), False),
    "met_as_a_bare_string": (lambda: (_depends(UP)(), _up_status("done")()), False),
    "unmet": (_depends([UP]), True),
    "an_id_that_resolves_to_nothing": (_depends(["ghost-999"]), True),
    "a_dependency_in_an_unknown_status": (_up_status("cancelled"), True),
    "a_dependency_with_no_status": (_up_status(None), True),
    "an_archived_dependency": (_up_status("archived"), True),
    "duplicated_and_unmet": (_depends([UP, UP]), True),
    # `depends_on:` left empty in hand-written YAML reads back as null: no
    # dependencies, as the resolver has always read it.
    "null": (_depends(None), False),
    # Shapes no tool writes. The resolver cannot read them, so it reports an
    # `unknown` dependency blocker rather than guessing ids out of them.
    "a_number": (_depends(5), True),
    "a_mapping": (_depends({UP: True}), True),
    "a_list_holding_a_number": (_depends([5]), True),
}


@pytest.fixture(params=sorted(DEPENDENCY_CASES))
def case(request, tmp_path, monkeypatch):
    edit, blocked = DEPENDENCY_CASES[request.param]

    def seed():
        bs.backlog_update_epic(epic_id="test-epic", field="status", value="active")
        bs.backlog_add_task(title="Up", epic="test-epic", phase="dev")
        bs.backlog_add_task(title="Down", epic="test-epic", phase="dev")
        edit()
    return make_twins(tmp_path, monkeypatch, seed), blocked, request.param


def _resolver(root, twins, focus):
    with twins.at(root):
        return json.loads(bs.backlog_context(focus=focus, include=[]))["mandatory"]["blockers"]


def _dependency_blockers(blockers):
    return [b for b in blockers
            if b["kind"] == "dependency" or (b["kind"] == "unknown" and b["id"] == "dependencies")]


def _call(root, twins, tool, **kwargs):
    with twins.at(root):
        return getattr(bs, tool)(**kwargs)


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_every_availability_reader_agrees_with_the_resolver_about_dependencies(case, side):
    twins, blocked, _name = case
    root = getattr(twins, side)
    judged = _dependency_blockers(_resolver(root, twins, DOWN))
    assert bool(judged) is blocked, judged

    available = _call(root, twins, "backlog_next_available")
    ready, _, waiting = available.partition("blocked by dependencies")
    assert (f"`{DOWN}` — Down (" in ready) is not blocked, available
    assert (f"`{DOWN}` — Down (waiting on" in waiting) is blocked, available

    status = _call(root, twins, "backlog_status")
    next_up = re.search(r"Next Up:\*\*(.*?)\n\n", status, re.S)
    assert next_up is not None, status
    assert (f"`{DOWN}`" in next_up.group(1)) is not blocked, status

    dependencies = _call(root, twins, "backlog_dependencies", task_id=DOWN)
    assert ("All dependencies met: **No**" in dependencies) is blocked, dependencies


# A pick is a write, so it runs on both stores at once and their states are
# compared; a write also derives link edges from `depends_on`, which must not
# raise on a shape the resolver reports as unreadable.
def test_a_pick_warns_about_exactly_what_the_resolver_reports(case):
    twins, blocked, _name = case
    for picked in twins.same("backlog_pick_task", task_id=DOWN):
        assert picked.startswith(f"Picked `{DOWN}`"), picked
        assert ("Unmet dependencies" in picked) is blocked, picked


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_the_ids_a_reader_says_it_waits_on_are_the_resolvers_dependency_blockers(case, side):
    twins, blocked, _name = case
    if not blocked:
        return
    root = getattr(twins, side)
    judged = _dependency_blockers(_resolver(root, twins, DOWN))
    named = {b["id"] for b in judged if b["kind"] == "dependency"}
    available = _call(root, twins, "backlog_next_available")
    line = next(line for line in available.splitlines() if line.startswith(f"- `{DOWN}`"))
    waited = set(re.findall(r"`([^`]+)`", line.partition("waiting on")[2]))
    if named:
        assert waited == named, (line, judged)
    else:
        # The resolver could not read `depends_on`; the reader says so, and does
        # not invent ids out of a shape it cannot read.
        assert waited == {"depends_on (unreadable)"}, line


def test_both_stores_give_every_availability_reader_the_same_answer(case):
    twins, _blocked, _name = case
    for tool, kwargs in (("backlog_next_available", {}), ("backlog_status", {}),
                         ("backlog_dependencies", {"task_id": DOWN})):
        twins.same(tool, **kwargs)


# ── Claims: what `backlog_context` reports as held, a pick does not take ─────


def _peer(pid):
    return f"{store._local_host()}-{pid}-0badc0de"


def _dead_pid():
    return next(pid for pid in range(4_000_000, 4_100_000) if not store._local_pid_alive(pid))


@pytest.fixture
def claimed(tmp_path, monkeypatch):
    """A peer's claim on a task that is not in progress: `backlog_update_task`
    documents `locked_by` as the way to claim, so a claim does not need an
    in-progress status to exist."""
    def seed():
        bs.backlog_update_epic(epic_id="test-epic", field="status", value="active")
        bs.backlog_add_task(title="Todo, held by a live peer", epic="test-epic", phase="dev")
        bs.backlog_add_task(title="In review, held by a live peer", epic="test-epic", phase="dev")
        bs.backlog_add_task(title="Todo, held by a dead peer", epic="test-epic", phase="dev")
        bs.backlog_update_task(task_id="test-epic-002", field="status", value="in-progress")
        bs.backlog_update_task(task_id="test-epic-002", field="human_action", value="Approve it")
        bs.backlog_update_task(task_id="test-epic-002", field="status", value="in-review")
        for ident, pid in (("test-epic-001", os.getpid()), ("test-epic-002", os.getpid()),
                           ("test-epic-003", _dead_pid())):
            bs.backlog_update_task(task_id=ident, field="locked_by", value=_peer(pid))
    return make_twins(tmp_path, monkeypatch, seed)


@pytest.mark.parametrize("ident", ["test-epic-001", "test-epic-002"])
def test_a_pick_refuses_a_task_the_resolver_reports_a_live_peer_holding(claimed, ident):
    for text in claimed.same("backlog_get_task", task_id=ident):
        assert "in-progress" not in text, text
    for side in ("legacy", "native"):
        blockers = _resolver(getattr(claimed, side), claimed, ident)
        assert ("claim", ident) in {(b["kind"], b["id"]) for b in blockers}
    for text in claimed.same("backlog_pick_task", task_id=ident):
        assert text.startswith(f"Error: task `{ident}` is locked by another session"), text
        assert "already in-progress elsewhere" not in text
    for text in claimed.same("backlog_claim", action="status", task_id=ident):
        assert json.loads(text)["holder"] == _peer(os.getpid())
    claimed.assert_state_matches()


def test_force_still_takes_a_peers_claim_on_a_task_not_in_progress(claimed):
    for text in claimed.same("backlog_pick_task", task_id="test-epic-001", force=True):
        assert text.startswith("Picked `test-epic-001`"), text
    for text in claimed.same("backlog_claim", action="status", task_id="test-epic-001"):
        assert json.loads(text)["holder"] == bs.SESSION_ID


def test_a_dead_peers_claim_on_a_todo_task_is_refused_informed_and_released_without_force(claimed):
    for text in claimed.same("backlog_pick_task", task_id="test-epic-003"):
        assert "locked by another session" in text and "expired" in text, text
    claimed.same("backlog_claim", action="release", task_id="test-epic-003")
    for text in claimed.same("backlog_pick_task", task_id="test-epic-003"):
        assert text.startswith("Picked `test-epic-003`"), text
    claimed.assert_state_matches()


def test_a_task_a_peer_has_claimed_is_not_offered_as_ready_to_pick(claimed):
    for text in claimed.same("backlog_next_available"):
        ready = text.partition("claimed by another session")[0]
        assert "test-epic-001" not in ready and "test-epic-003" not in ready, text
        assert "**2 tasks claimed by another session:**" in text, text
        assert f"`test-epic-001` — Todo, held by a live peer (claimed by `{_peer(os.getpid())}`)" in text
    for text in claimed.same("backlog_status"):
        next_up = re.search(r"Next Up:\*\*(.*?)\n\n", text, re.S).group(1)
        assert "test-epic-001" not in next_up and "test-epic-003" not in next_up, text


def test_a_task_this_session_has_claimed_is_still_ready_for_this_session(tmp_path, monkeypatch):
    def seed():
        bs.backlog_update_epic(epic_id="test-epic", field="status", value="active")
        bs.backlog_add_task(title="Mine", epic="test-epic", phase="dev")
        bs.backlog_update_task(task_id="test-epic-001", field="locked_by", value=bs.SESSION_ID)
    twins = make_twins(tmp_path, monkeypatch, seed)
    for text in twins.same("backlog_next_available"):
        assert "`test-epic-001` — Mine (" in text and "claimed by another session" not in text
    for text in twins.same("backlog_pick_task", task_id="test-epic-001"):
        assert text.startswith("Picked `test-epic-001`"), text
