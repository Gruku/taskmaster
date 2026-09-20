"""User intent: `backlog_context` is the one call an agent makes to learn what it
needs before touching a task, so it has to work on the stores that exist today —
every real project is still legacy (D8) — and both stores have to agree about the
one thing that is a safety claim: what blocks the task, and whether it is clear.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins

BLOCKED, NEXT = "test-epic-001", "test-epic-002"


@pytest.fixture
def twins(tmp_path, monkeypatch):
    def seed():
        # Ids are allocated by the store: the first two tasks under `test-epic`.
        bs.backlog_add_task(title="Blocked work", epic="test-epic", phase="dev")
        bs.backlog_add_task(title="Next work", epic="test-epic", phase="dev",
                            depends_on=BLOCKED)
        bs.backlog_note(action="create", text="A pinned orientation note", pinned=True)
    return make_twins(tmp_path, monkeypatch, seed)


def answer(root, twins_, **kwargs):
    with twins_.at(root):
        return json.loads(bs.backlog_context(**kwargs))


KEYS = {"store_id", "sequence", "scope", "focus", "mandatory", "selected",
        "budget", "provenance", "cursor"}


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_context_answers_one_shape_with_mandatory_blockers_and_a_byte_budget(twins, side):
    root = getattr(twins, side)
    answered = answer(root, twins, focus=NEXT, scope="task",
                      include=["dependencies", "notes"])
    assert set(answered) == KEYS
    assert answered["focus"] == NEXT and answered["scope"] == "task"
    assert ("dependency", BLOCKED) in {(b["kind"], b["id"])
                                             for b in answered["mandatory"]["blockers"]}
    assert answered["mandatory"]["clear"] is False
    assert [row["id"] for row in answered["selected"]["dependencies"]] == [BLOCKED]
    assert answered["budget"]["applies_to"] == "selected"


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_the_reported_byte_count_is_the_bytes_the_tool_returned(twins, side):
    root = getattr(twins, side)
    with twins.at(root):
        text = bs.backlog_context(focus=NEXT, include=["dependencies", "notes", "siblings"])
    assert json.loads(text)["budget"]["used_bytes"] == len(text.encode("utf-8"))


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_budget_too_small_for_the_blockers_returns_them_anyway_and_says_so(twins, side):
    """The milestone's own rule: a trimmed blocker list is indistinguishable from
    a clear one at the point of use, so the budget only ever drops selection."""
    root = getattr(twins, side)
    whole = answer(root, twins, focus=NEXT, include=["dependencies", "notes"])
    squeezed = answer(root, twins, focus=NEXT, include=["dependencies", "notes"],
                      budget_bytes=32)
    assert squeezed["budget"]["over_budget"] is True
    assert squeezed["selected"] == {} and squeezed["cursor"] == ""
    assert squeezed["mandatory"] == whole["mandatory"]
    assert squeezed["budget"]["omitted_total"] == sum(whole["budget"]["omitted"].values()) + \
        len(whole["selected"].get("dependencies", [])) + len(whole["selected"].get("notes", []))


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_task_that_does_not_exist_blocks_rather_than_reading_as_clear(twins, side):
    root = getattr(twins, side)
    absent = answer(root, twins, focus="no-such-task")
    assert absent["mandatory"]["clear"] is False
    assert [(b["kind"], b["id"]) for b in absent["mandatory"]["blockers"]
            if b["kind"] == "unknown"] == [("unknown", "task")]


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_question_with_no_task_in_focus_is_never_reported_as_clear(twins, side):
    root = getattr(twins, side)
    wide = answer(root, twins, scope="project", include=["notes"])
    assert wide["focus"] is None and wide["mandatory"]["clear"] is False
    assert [b["reason"] for b in wide["mandatory"]["blockers"]] == ["no_focus_task"]
    assert wide["selected"]["notes"]


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_session_scoped_question_finds_the_task_that_session_picked(twins, side):
    root = getattr(twins, side)
    with twins.at(root):
        bs.backlog_pick_task(task_id=BLOCKED)
        picked = json.loads(bs.backlog_context(scope="session", include=["notes"]))
    assert picked["focus"] == BLOCKED


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_bad_arguments_refuse_as_json_on_both_stores(twins, side):
    root = getattr(twins, side)
    for kwargs in ({"scope": "everything"}, {"include": ["nonsense"]}, {"budget_bytes": 0},
                   {"budget_bytes": 10 ** 9}, {"cursor": "not-a-cursor-this-store-issued"}):
        refusal = answer(root, twins, **kwargs)
        assert set(refusal) == {"error"} and refusal["error"]


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_cursor_pages_the_selection_and_loses_no_row(twins, side):
    root = getattr(twins, side)
    with twins.at(root):
        for n in range(6):
            bs.backlog_note(action="create", text=f"Orientation note {n}", pinned=True)
    whole = answer(root, twins, scope="project", include=["notes"])
    every = [row["id"] for row in whole["selected"]["notes"]]
    assert len(every) == 7 and whole["cursor"] == ""
    limit = whole["budget"]["used_bytes"] - 120
    page = answer(root, twins, scope="project", include=["notes"], budget_bytes=limit)
    seen, cursor = [row["id"] for row in page["selected"]["notes"]], page["cursor"]
    assert 0 < len(seen) < 7 and cursor
    assert page["budget"]["omitted"]["notes"] == 7 - len(seen)
    while cursor:
        nxt = answer(root, twins, scope="project", include=["notes"],
                     budget_bytes=limit, cursor=cursor)
        seen += [row["id"] for row in nxt["selected"].get("notes", [])]
        cursor = nxt["cursor"]
    assert seen == every


def test_a_context_cursor_cannot_cross_between_the_legacy_store_and_its_native_copy(twins):
    for root in (twins.legacy, twins.native):
        with twins.at(root):
            for n in range(6):
                bs.backlog_note(action="create", text=f"Orientation note {n}", pinned=True)
    whole = answer(twins.legacy, twins, scope="project", include=["notes"])
    limit = whole["budget"]["used_bytes"] - 120
    legacy_cursor = answer(twins.legacy, twins, scope="project", include=["notes"],
                           budget_bytes=limit)["cursor"]
    assert legacy_cursor
    crossed = answer(twins.native, twins, scope="project", include=["notes"],
                     budget_bytes=limit, cursor=legacy_cursor)
    assert set(crossed) == {"error"}


def test_the_two_stores_agree_about_what_blocks_a_task(twins):
    """Twin parity where it counts. The selected context is a bounded convenience
    and the stores read it differently; `mandatory` is a safety claim and must be
    the same answer on both."""
    with twins.at(twins.legacy):
        bs.backlog_bug_create(title="A blocking defect", severity="P1", found_in=NEXT)
    with twins.at(twins.native):
        bs.backlog_bug_create(title="A blocking defect", severity="P1", found_in=NEXT)
    for focus in (NEXT, BLOCKED, "no-such-task"):
        legacy = answer(twins.legacy, twins, focus=focus)["mandatory"]
        native = answer(twins.native, twins, focus=focus)["mandatory"]
        assert native == legacy, f"{focus}: mandatory context diverged"
    assert any(b["kind"] == "bug" for b in answer(twins.legacy, twins,
                                                  focus=NEXT)["mandatory"]["blockers"])
