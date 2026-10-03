"""User intent: a `backlog_context` answer spends its bytes on facts, not on
boilerplate. The N09 journey harness measured several hundred bytes of fixed JSON
envelope per answer, which cost the orient and close journeys more bytes than the
short answers they replaced. Nothing the caller did not already know, and nothing
that says "zero" or "false", is spelled out; required context is untouched.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native.blockers import Resolution
from taskmaster.native.budget import Selection, budget
from native_twins import make_twins

ALLOWED = {"sequence", "focus", "mandatory", "selected", "budget", "provenance", "cursor"}


def payload(answer):
    return json.loads(answer.text)


def test_a_complete_answer_reports_only_its_byte_count():
    answer = budget(envelope={"sequence": 1}, mandatory={"clear": True, "blockers": []},
                    selections=[Selection("notes", [{"id": "N-1"}], 1)], limit_bytes=8000)
    assert payload(answer)["budget"] == {"used_bytes": len(answer.text.encode("utf-8"))}


def test_an_omission_and_an_overflow_are_spelled_out_when_they_happen():
    blockers = [{"kind": "gate", "id": f"gate-{n}", "state": "pending"} for n in range(20)]
    answer = budget(envelope={"sequence": 1}, mandatory={"clear": False, "blockers": blockers},
                    selections=[Selection("handovers", [{"id": "H-1"}], 4)], limit_bytes=200)
    block = payload(answer)["budget"]
    assert block["over_budget"] is True and block["mandatory_bytes"] > 200
    assert block["omitted"] == {"handovers": 4} and block["omitted_total"] == 4


def test_a_partial_answer_names_only_the_sections_it_left_rows_out_of():
    answer = budget(envelope={"sequence": 1}, mandatory={"clear": True, "blockers": []},
                    selections=[Selection("notes", [{"id": "N-1"}], 3),
                                Selection("bugs", [{"id": "B-1"}], 1)], limit_bytes=8000)
    assert payload(answer)["budget"]["omitted"] == {"notes": 2}


def test_empty_gate_state_and_human_action_are_left_out_of_mandatory():
    assert Resolution(clear=True, blockers=(), gate_state="", human_action="").as_dict() == {
        "clear": True, "blockers": []}
    held = Resolution(clear=False, blockers=(), gate_state="review-gate:pending",
                      human_action="sign it").as_dict()
    assert held["gate_state"] == "review-gate:pending" and held["human_action"] == "sign it"


FOCUS = "test-epic-002"


@pytest.fixture()
def twins(tmp_path, monkeypatch):
    def seed():
        bs.backlog_update_epic(epic_id="test-epic", field="status", value="active")
        for n in range(1, 16):
            bs.backlog_add_task(title=f"Task {n}", epic="test-epic", phase="dev")
        bs.backlog_note(action="create", text="An unpinned desk note")
        bs.backlog_note(action="create", text="A pinned desk note", pinned=True)
    return make_twins(tmp_path, monkeypatch, seed)


def ask(twins, side, **kwargs):
    with twins.at(getattr(twins, side)):
        text = bs.backlog_context(**kwargs)
    return text, json.loads(text)


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_complete_answer_carries_no_envelope_boilerplate(twins, side):
    text, answered = ask(twins, side, focus=FOCUS, scope="task", include=["siblings"])
    assert set(answered) <= ALLOWED, set(answered) - ALLOWED
    assert answered["focus"] == FOCUS and isinstance(answered["sequence"], int)
    assert "cursor" not in answered and "provenance" not in answered
    assert answered["budget"] == {"used_bytes": len(text.encode("utf-8"))}
    assert len(answered["selected"]["siblings"]) == 14


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_truncated_section_is_named_and_the_answer_continues(twins, side):
    whole = ask(twins, side, focus=FOCUS, scope="task", include=["siblings"])[1]
    # Trimming adds an omission entry and a cursor, so the first limit that
    # truncates sits some way under the whole answer.
    for limit in range(whole["budget"]["used_bytes"] - 1, 0, -10):
        _text, page = ask(twins, side, focus=FOCUS, scope="task", include=["siblings"],
                          budget_bytes=limit)
        if page["selected"].get("siblings"):
            if "provenance" in page:
                break
    else:
        raise AssertionError("no limit truncated the section")
    assert page["provenance"] == {"siblings": {"truncated": True}}
    assert page["cursor"]
    assert page["budget"]["omitted"]["siblings"] >= 1


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_the_notes_section_is_the_whole_desk_pinned_first(twins, side):
    """Orientation shows every desk note, pinned first; selecting pinned notes only
    lost the unpinned ones the old `backlog_note` list showed."""
    notes = ask(twins, side, scope="project", include=["notes"])[1]["selected"]["notes"]
    assert [(row["text"], row.get("pinned", False)) for row in notes] == [
        ("A pinned desk note", True), ("An unpinned desk note", False)]


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_project_answer_has_no_focus_key(twins, side):
    answered = ask(twins, side, scope="project", include=[])[1]
    assert "focus" not in answered
    assert answered["mandatory"]["clear"] is False


# ── Blockers carry no field their kind already fixes ────────────────────────

from taskmaster.native.blockers import Blocker  # noqa: E402


def test_a_blocker_leaves_out_the_state_and_source_its_kind_fixes():
    assert Blocker(kind="bug", id="B-1", state="open", source="found_in",
                   extra={"severity": "P3"}).as_dict() == {"kind": "bug", "id": "B-1", "severity": "P3"}
    assert Blocker(kind="handover", id="H", state="open", source="next_action",
                   extra={"next_action": "go"}).as_dict() == {"kind": "handover", "id": "H", "next_action": "go"}
    assert Blocker(kind="dependency", id="T-2", state="todo", source="depends_on").as_dict() == {
        "kind": "dependency", "id": "T-2", "state": "todo"}
    # A gate's lane and state vary, so both stay.
    assert Blocker(kind="gate", id="review-gate", state="pending", source="lane:express").as_dict() == {
        "kind": "gate", "id": "review-gate", "state": "pending", "source": "lane:express"}


def test_gate_state_is_left_out_when_a_gate_blocker_already_says_it():
    gate = Blocker(kind="gate", id="review-gate", state="pending", source="lane:express")
    assert "gate_state" not in Resolution(clear=False, blockers=(gate,),
                                          gate_state="review-gate:pending").as_dict()
    assert Resolution(clear=True, blockers=(), gate_state="review-gate:pass").as_dict()[
        "gate_state"] == "review-gate:pass"


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_handover_row_carries_no_date_its_id_already_spells(twins, side):
    with twins.at(getattr(twins, side)):
        # Separate threads: within one, the second would supersede the first.
        bs.backlog_handover_create(tldr="Quiet one", task_ids=[FOCUS], thread="quiet")
        bs.backlog_handover_create(tldr="Asking one", next_action="do the thing", task_ids=[FOCUS], thread="asking")
        rows = json.loads(bs.backlog_context(focus=FOCUS, scope="task", include=["handovers"]))[
            "selected"]["handovers"]
    assert sorted(rows, key=lambda r: r["id"]) == [
        {"id": "2026-09-17-asking-one", "next_action": "do the thing"},
        {"id": "2026-09-17-quiet-one"}]
