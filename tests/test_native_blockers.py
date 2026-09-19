"""User intent: prove N09's exit criterion at its source — missing mandatory
context is never reported as clear. One resolver answers "what blocks this task",
the blocker set is fixed in code, and every producer that cannot answer degrades
to a blocker rather than to silence.
"""
from contextlib import closing
import json
import sqlite3

import pytest

from taskmaster.native.blockers import (
    BLOCKING_BUG_SEVERITIES,
    MANDATORY_BLOCKER_KINDS,
    Claim,
    Facts,
    Unknown,
    probe,
    resolve,
)
from taskmaster.native.workflow import _bugs_found_in
from test_native_commands import native, envelope  # noqa: F401
from test_native_migration import legacy  # noqa: F401
from test_native_workflow import created, run, workspace, _hand_edit_found_in  # noqa: F401


def task(**overrides):
    """A task document on the express lane with its one blocking gate passed."""
    document = {
        "id": "demo-001", "title": "First", "status": "in-progress", "lane": "express",
        "gates": {"review-gate": {"verdict": "pass"}}, "depends_on": [],
    }
    document.update(overrides)
    return document


def clear_facts(**overrides):
    """Facts where every mandatory producer answered, and answered "nothing"."""
    facts = {"task": task(), "dependencies": {}, "bugs": [], "handovers": [],
             "claim": None, "session": "alpha"}
    facts.update(overrides)
    return Facts(**facts)


def kinds(resolution):
    return [blocker.kind for blocker in resolution.blockers]


def by_kind(resolution, kind):
    return [blocker for blocker in resolution.blockers if blocker.kind == kind]


# ── The clear answer, and what it costs to earn it ──────────────────────────


def test_every_producer_answered_nothing_reads_as_clear():
    resolution = resolve(clear_facts())
    assert resolution.clear is True
    assert resolution.blockers == ()
    assert resolution.as_dict()["clear"] is True


def test_a_producer_nobody_supplied_is_unknown_not_absent():
    """The defaults must not read as "nothing blocks". A caller that forgets a
    producer gets a blocker naming it, never `clear: true`."""
    resolution = resolve(Facts(task=task()))
    assert resolution.clear is False
    assert {blocker.id for blocker in by_kind(resolution, "unknown")} == {
        "dependencies", "bugs", "handovers", "claim"}
    assert all(blocker.reason == "not_supplied" for blocker in by_kind(resolution, "unknown"))


# ── Each blocker kind ───────────────────────────────────────────────────────


def test_a_pending_blocking_gate_blocks_and_names_its_lane():
    resolution = resolve(clear_facts(task=task(lane="full", gates={})))
    gates = by_kind(resolution, "gate")
    assert [blocker.id for blocker in gates] == ["spec-review", "plan-review", "review-gate"]
    assert gates[0].state == "pending" and gates[0].source == "lane:full"
    assert resolution.gate_state == "spec-review:pending"
    assert resolution.clear is False


def test_a_failed_gate_blocks_with_its_verdict():
    resolution = resolve(clear_facts(
        task=task(gates={"review-gate": {"verdict": "fail"}})))
    assert [(b.id, b.state) for b in by_kind(resolution, "gate")] == [("review-gate", "fail")]


def test_a_status_gate_is_a_progress_marker_and_never_blocks():
    resolution = resolve(clear_facts(task=task(lane="express", gates={
        "review-gate": {"verdict": "pass"}})))
    assert resolution.clear is True


def test_a_laneless_task_is_gate_exempt_not_unknown():
    resolution = resolve(clear_facts(task=task(lane="", gates={})))
    assert resolution.clear is True
    assert resolution.gate_state == ""


def test_an_unrecognised_lane_is_unknown_rather_than_exempt():
    resolution = resolve(clear_facts(task=task(lane="turbo", gates={})))
    assert resolution.clear is False
    assert [(b.id, b.reason) for b in by_kind(resolution, "unknown")] == [("gates", "unknown_lane")]


def test_an_unfinished_dependency_blocks_with_its_status():
    resolution = resolve(clear_facts(
        task=task(depends_on=["demo-002", "demo-003"]),
        dependencies={"demo-002": "done", "demo-003": "in-progress"}))
    assert [(b.id, b.state) for b in by_kind(resolution, "dependency")] == [
        ("demo-003", "in-progress")]


def test_a_dependency_id_that_cannot_be_resolved_blocks_as_missing():
    """The three shipped sites disagree here: pick/next_available/_derive_context
    silently treat a missing id as `todo`, while backlog_dependencies renders
    `NOT FOUND`. One answer: it blocks, and it says the id was not resolvable."""
    resolution = resolve(clear_facts(task=task(depends_on=["ghost-001"]), dependencies={}))
    missing = by_kind(resolution, "dependency")
    assert [(b.id, b.state) for b in missing] == [("ghost-001", "missing")]
    assert missing[0].as_dict()["unresolved"] is True
    assert resolution.clear is False


def test_a_depends_on_of_the_wrong_shape_is_unknown():
    resolution = resolve(clear_facts(task=task(depends_on={"demo-002": True}), dependencies={}))
    assert [(b.id, b.reason) for b in by_kind(resolution, "unknown")] == [
        ("dependencies", "malformed_depends_on")]


def test_a_string_depends_on_is_read_as_one_dependency():
    resolution = resolve(clear_facts(task=task(depends_on="demo-002"),
                                     dependencies={"demo-002": "todo"}))
    assert [(b.id, b.state) for b in by_kind(resolution, "dependency")] == [("demo-002", "todo")]


def test_an_open_high_severity_bug_found_in_the_task_blocks():
    resolution = resolve(clear_facts(bugs=[
        {"id": "B-118", "status": "open", "severity": "P1"},
        {"id": "B-119", "status": "open", "severity": "P3"}]))
    bugs = by_kind(resolution, "bug")
    assert [b.id for b in bugs] == ["B-118"]
    assert bugs[0].as_dict() == {"kind": "bug", "id": "B-118", "state": "open",
                                 "source": "found_in", "severity": "P1"}
    assert "P1" in BLOCKING_BUG_SEVERITIES and "P3" not in BLOCKING_BUG_SEVERITIES


def test_an_open_bug_with_no_severity_blocks_because_it_cannot_be_ruled_out():
    """Severity is optional on bugs, and `task.complete` already refuses on any
    open bug via found_in. An unstated severity must not read as low."""
    resolution = resolve(clear_facts(bugs=[{"id": "B-120", "status": "open", "severity": None}]))
    assert [(b.id, b.as_dict()["severity"]) for b in by_kind(resolution, "bug")] == [
        ("B-120", None)]
    assert resolution.clear is False


def test_an_open_handover_carrying_a_next_action_blocks():
    resolution = resolve(clear_facts(handovers=[
        {"id": "HAND-004", "next_action": "finish the resolver"},
        {"id": "HAND-005", "next_action": ""}]))
    handovers = by_kind(resolution, "handover")
    assert [b.id for b in handovers] == ["HAND-004"]
    assert handovers[0].as_dict()["next_action"] == "finish the resolver"


def test_a_human_action_on_the_task_blocks():
    resolution = resolve(clear_facts(task=task(human_action="add OPENAI_API_KEY to .env")))
    action = by_kind(resolution, "human_action")
    assert [(b.id, b.state) for b in action] == [("demo-001", "waiting")]
    assert resolution.as_dict()["human_action"] == "add OPENAI_API_KEY to .env"


def test_a_live_peers_claim_blocks_and_this_sessions_claim_does_not():
    peer = resolve(clear_facts(claim=Claim(holder="sess-7f2", live=True)))
    assert [(b.id, b.state, b.as_dict()["by"], b.as_dict()["live"])
            for b in by_kind(peer, "claim")] == [("demo-001", "held", "sess-7f2", True)]
    mine = resolve(clear_facts(claim=Claim(holder="alpha", live=True)))
    assert mine.clear is True


def test_a_claim_held_by_a_session_that_is_not_live_does_not_block():
    resolution = resolve(clear_facts(claim=Claim(holder="sess-dead", live=False)))
    assert resolution.clear is True


def test_a_claim_whose_liveness_could_not_be_decided_blocks_as_unknown():
    resolution = resolve(clear_facts(claim=Claim(holder="sess-7f2", live=None)))
    assert [(b.id, b.reason) for b in by_kind(resolution, "unknown")] == [
        ("claim", "claim_liveness_unknown")]
    assert resolution.clear is False


# ── Never clear when unknown ────────────────────────────────────────────────


@pytest.mark.parametrize("producer", ["task", "dependencies", "bugs", "handovers", "claim"])
def test_a_producer_that_could_not_answer_is_never_reported_as_clear(producer):
    resolution = resolve(clear_facts(**{producer: Unknown("producer_failed", "boom")}))
    assert resolution.clear is False
    unknown = by_kind(resolution, "unknown")
    assert producer in {blocker.id for blocker in unknown}
    assert all(blocker.state == "unknown" for blocker in unknown)


def test_an_unreadable_task_does_not_suppress_the_other_producers():
    resolution = resolve(clear_facts(task=Unknown("unreadable_row"),
                                     claim=Claim(holder="sess-7f2", live=True)))
    assert resolution.clear is False
    assert {b.id for b in by_kind(resolution, "unknown")} == {"task"}
    assert by_kind(resolution, "claim")


def test_a_malformed_gates_bag_is_unknown_rather_than_no_gates():
    resolution = resolve(clear_facts(task=task(gates=["review-gate"])))
    assert [(b.id, b.reason) for b in by_kind(resolution, "unknown")] == [
        ("gates", "malformed_gates")]


def test_a_malformed_gate_record_is_unknown_rather_than_unsatisfied():
    resolution = resolve(clear_facts(task=task(lane="express", gates={"review-gate": "pass"})))
    assert [(b.id, b.reason) for b in by_kind(resolution, "unknown")] == [
        ("gates", "malformed_gate_record")]


def test_clear_is_true_only_when_the_blocker_list_is_empty():
    for facts in (clear_facts(task=task(lane="full", gates={})),
                  clear_facts(bugs=[{"id": "B-1", "status": "open", "severity": "P0"}]),
                  clear_facts(handovers=[{"id": "H-1", "next_action": "go"}]),
                  clear_facts(claim=Claim(holder="peer", live=True)),
                  clear_facts(task=Unknown("unreadable_row"))):
        resolution = resolve(facts)
        assert resolution.clear == (resolution.blockers == ())
        assert resolution.clear is False


def test_probe_turns_a_raising_producer_into_an_unknown():
    def boom():
        raise ValueError("bug `B-9` has a malformed found_in of type list")

    answer = probe(boom)
    assert isinstance(answer, Unknown)
    assert answer.reason == "producer_failed" and "malformed found_in" in answer.detail
    assert probe(lambda: []) == []


# ── The fixed set ───────────────────────────────────────────────────────────


def test_the_constant_and_the_resolver_agree_on_the_blocker_kinds():
    """Every kind the constant names is reachable, and `Blocker` refuses any kind
    it does not name — so the two cannot drift apart in either direction."""
    every = Facts(
        task=task(lane="full", gates={}, depends_on=["ghost-001"],
                  human_action="add the key"),
        dependencies={},
        bugs=[{"id": "B-118", "status": "open", "severity": "P0"}],
        handovers=[{"id": "HAND-004", "next_action": "resume"}],
        claim=Claim(holder="sess-7f2", live=True),
        session="alpha")
    reachable = set(kinds(resolve(every)))
    reachable |= set(kinds(resolve(clear_facts(bugs=Unknown("producer_failed", "boom")))))
    assert reachable == set(MANDATORY_BLOCKER_KINDS)


def test_a_kind_outside_the_constant_cannot_be_constructed():
    from taskmaster.native.blockers import Blocker

    with pytest.raises(ValueError, match="not a mandatory blocker kind"):
        Blocker(kind="vibes", id="demo-001", state="pending")


def test_blockers_are_ordered_by_kind_then_id():
    resolution = resolve(Facts(
        task=task(lane="full", gates={}, depends_on=["ghost-002", "ghost-001"],
                  human_action="add the key"),
        dependencies={},
        bugs=[{"id": "B-2", "status": "open", "severity": "P0"},
              {"id": "B-1", "status": "open", "severity": "P0"}],
        handovers=[{"id": "H-2", "next_action": "a"}, {"id": "H-1", "next_action": "b"}],
        claim=Claim(holder="peer", live=True),
        session="alpha"))
    order = [MANDATORY_BLOCKER_KINDS.index(kind) for kind in kinds(resolution)]
    assert order == sorted(order)
    assert [b.id for b in by_kind(resolution, "bug")] == ["B-1", "B-2"]
    assert [b.id for b in by_kind(resolution, "dependency")] == ["ghost-001", "ghost-002"]


# ── Against a real store: the shapes N08 had to refuse ──────────────────────


def test_a_list_shaped_found_in_degrades_to_a_blocker_not_a_missed_bug(workspace):
    """N08 made `_bugs_found_in` raise on a non-string `found_in` rather than
    silently miss the bug. The resolver keeps faith with that: the refusal
    becomes an `unknown` blocker, so the task is never reported clear."""
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        bug = created(connection, "bug.create", {"title": "Crash", "found_in": "demo-001"}, "bug")
        _hand_edit_found_in(connection, bug, json.dumps(["demo-001"]))
        answer = probe(lambda: _bugs_found_in(connection, "demo-001"))
    assert isinstance(answer, Unknown)
    resolution = resolve(clear_facts(bugs=answer))
    assert resolution.clear is False
    unknown = by_kind(resolution, "unknown")
    assert [b.id for b in unknown] == ["bugs"]
    assert f"bug `{bug}` has a malformed found_in" in unknown[0].detail


def test_a_string_shaped_found_in_still_produces_the_bug_blocker(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        bug = created(connection, "bug.create",
                      {"title": "Crash", "found_in": "demo-001", "severity": "P1"}, "bug")
        open_bugs, _fixed = probe(lambda: _bugs_found_in(connection, "demo-001"))
    resolution = resolve(clear_facts(bugs=[
        {"id": ident, "status": "open", "severity": "P1"} for ident in open_bugs]))
    assert [b.id for b in by_kind(resolution, "bug")] == [bug]
