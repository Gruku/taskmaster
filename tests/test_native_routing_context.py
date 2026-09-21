"""User intent: `backlog_context` is the one call an agent makes to learn what it
needs before touching a task, so it has to work on the stores that exist today —
every real project is still legacy (D8) — and both stores have to agree about the
one thing that is a safety claim: what blocks the task, and whether it is clear.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import hand_set_holder, make_twins

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


# The compact answer: `store_id` and the echoed `scope` are gone, and `cursor`,
# `provenance` and `focus` appear only when they carry something.
KEYS = {"sequence", "focus", "mandatory", "selected", "budget", "provenance", "cursor"}


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_context_answers_one_shape_with_mandatory_blockers_and_a_byte_budget(twins, side):
    root = getattr(twins, side)
    answered = answer(root, twins, focus=NEXT, scope="task",
                      include=["dependencies", "notes"])
    assert set(answered) <= KEYS and {"sequence", "mandatory", "selected", "budget"} <= set(answered)
    assert answered["focus"] == NEXT
    assert ("dependency", BLOCKED) in {(b["kind"], b["id"])
                                             for b in answered["mandatory"]["blockers"]}
    assert answered["mandatory"]["clear"] is False
    assert [row["id"] for row in answered["selected"]["dependencies"]] == [BLOCKED]


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
    assert squeezed["selected"] == {} and squeezed.get("cursor", "") == ""
    assert squeezed["mandatory"] == whole["mandatory"]
    assert squeezed["budget"].get("omitted_total", 0) == sum(whole["budget"].get("omitted", {}).values()) + \
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
    assert wide.get("focus") is None and wide["mandatory"]["clear"] is False
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
    assert len(every) == 7 and whole.get("cursor", "") == ""
    limit = whole["budget"]["used_bytes"] - 120
    page = answer(root, twins, scope="project", include=["notes"], budget_bytes=limit)
    seen, cursor = [row["id"] for row in page["selected"]["notes"]], page.get("cursor", "")
    assert 0 < len(seen) < 7 and cursor
    assert page["budget"].get("omitted", {}).get("notes", 0) == 7 - len(seen)
    while cursor:
        nxt = answer(root, twins, scope="project", include=["notes"],
                     budget_bytes=limit, cursor=cursor)
        seen += [row["id"] for row in nxt["selected"].get("notes", [])]
        cursor = nxt.get("cursor", "")
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


@pytest.mark.parametrize("scope", ["task", "project"])
@pytest.mark.parametrize("side", ["legacy", "native"])
def test_every_section_answers_on_both_stores_with_an_exact_byte_count(twins, side, scope):
    """Each section is a separate read on each store, so each one is a separate
    chance for a count, a page or an encoding to be wrong. Ask for all of them."""
    from taskmaster.native import context as context_shape
    root = getattr(twins, side)
    with twins.at(root):
        bs.backlog_issue_create(title="An open issue", severity="P2",
                                evidence="It happened twice")
        bs.backlog_bug_create(title="A project defect", severity="P2")
        text = bs.backlog_context(focus=NEXT if scope == "task" else "", scope=scope,
                                  include=list(context_shape.SECTIONS), budget_bytes=200000)
    answered = json.loads(text)
    assert set(answered) <= KEYS
    assert answered["budget"]["used_bytes"] == len(text.encode("utf-8"))
    assert "omitted" not in answered["budget"]   # every section delivered whole
    assert set(answered.get("provenance", {})) <= set(context_shape.SECTIONS)
    assert answered.get("cursor", "") == ""
    if scope == "task":
        assert [row["id"] for row in answered["selected"]["dependencies"]] == [BLOCKED]
        assert [row["id"] for row in answered["selected"]["siblings"]] == [BLOCKED]
        # `found_in` names no task, so the project bug is not this task's blocker.
        assert answered["selected"].get("bugs") is None
    else:
        assert answered["provenance"]["dependencies"]["query"] == "no_focus"
        assert answered["budget"].get("omitted", {}).get("siblings", 0) == 0
        assert [row["id"] for row in answered["selected"]["bugs"]] == ["B-001"]
        assert [row["id"] for row in answered["selected"]["issues"]] == ["ISS-001"]
        assert answered["selected"]["recent"]


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_document_section_says_where_its_text_came_from_or_that_it_has_none(twins, side):
    """D7 leaves the legacy store reading the file and the native store reading an
    import, so `spec` has to say which — an absent section and an unimported one
    are different facts and only one of them means the text does not exist."""
    root = getattr(twins, side)
    with twins.at(root):
        (root / "docs").mkdir(exist_ok=True)
        (root / "docs" / "spec.md").write_text("The specification prose", encoding="utf-8")
        bs.backlog_update_task(task_id=NEXT, field="docs", value="spec:docs/spec.md")
        answered = json.loads(bs.backlog_context(focus=NEXT, include=["spec", "plan"]))
    assert answered["budget"].get("omitted", {}).get("plan", 0) == 0  # never declared, so nothing is missing
    spec = answered["provenance"]["spec"]
    if side == "legacy":
        assert spec == {"source": "filesystem", "imported": False, "path": "docs/spec.md"}
        assert answered["selected"]["spec"] == [{"section": "spec",
                                                 "text": "The specification prose"}]
    else:
        # Nothing has imported it, so the native store has the path and not the text.
        assert spec["imported"] is False and spec["unresolved"] == ["not_imported"]
        assert answered["selected"].get("spec") is None
        assert answered["budget"].get("omitted", {}).get("spec", 0) == 1


def _peer(pid):
    """A session id another agent on this machine would record in `locked_by`."""
    from taskmaster import store
    return f"{store._local_host()}-{pid}-0badc0de"


def _dead_pid():
    from taskmaster import store
    return next(pid for pid in range(4_000_000, 4_100_000) if not store._local_pid_alive(pid))


@pytest.fixture
def claimed(tmp_path, monkeypatch):
    """One task held by a live peer on this host, one by a peer whose process is
    gone. Neither holder has a `sessions` row — on a real project none does,
    because `locked_by` and the `sessions` key are minted independently."""
    import os

    def seed():
        bs.backlog_add_task(title="Held by a live peer", epic="test-epic", phase="dev")
        bs.backlog_add_task(title="Held by a dead peer", epic="test-epic", phase="dev")
        hand_set_holder("test-epic-001", _peer(os.getpid()))
        hand_set_holder("test-epic-002", _peer(_dead_pid()))
    return make_twins(tmp_path, monkeypatch, seed)


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_task_a_live_peer_holds_is_blocked_by_its_claim(claimed, side):
    """The integration defect: context judged a holder by looking it up in
    `sessions`, which never holds a `locked_by` id, so a live peer's claim read
    as dead and the task read as clear."""
    answered = answer(getattr(claimed, side), claimed, focus="test-epic-001")
    assert ("claim", "test-epic-001") in {(b["kind"], b["id"])
                                          for b in answered["mandatory"]["blockers"]}
    assert answered["mandatory"]["clear"] is False


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_claim_whose_holder_process_is_gone_does_not_block(claimed, side):
    answered = answer(getattr(claimed, side), claimed, focus="test-epic-002")
    assert "claim" not in {b["kind"] for b in answered["mandatory"]["blockers"]}


def test_both_stores_agree_on_claim_blockers(claimed):
    for focus in ("test-epic-001", "test-epic-002"):
        assert answer(claimed.legacy, claimed, focus=focus)["mandatory"] == \
            answer(claimed.native, claimed, focus=focus)["mandatory"]


@bs._transactional("test_raw_edit")
def _hand_edit_task(task_id, **fields):
    """A hand edit of the YAML: a shape the tools would never write."""
    data = bs._load()
    bs._find_task(data, task_id)[0].update(fields)
    bs._mutate_and_save(data)
    return "ok"


@pytest.fixture
def hand_edited(tmp_path, monkeypatch):
    def seed():
        bs.backlog_add_task(title="Hand edited", epic="test-epic", phase="dev")
        _hand_edit_task("test-epic-001", human_action=True)
    return make_twins(tmp_path, monkeypatch, seed)


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_a_human_action_of_the_wrong_shape_answers_unknown_instead_of_failing_the_tool(hand_edited, side):
    answered = answer(getattr(hand_edited, side), hand_edited, focus="test-epic-001", include=[])
    assert answered["mandatory"]["clear"] is False
    assert ("unknown", "human_action") in {(b["kind"], b["id"])
                                           for b in answered["mandatory"]["blockers"]}


def test_the_legacy_recent_section_reads_the_same_snapshot_as_the_rest_of_the_answer(twins, monkeypatch):
    """A peer's commit landing after the backlog was loaded must not reach one
    section of the answer and not the others: `recent` named an entity no other
    section knew, with no title, under a `sequence` the rest was not read at."""
    from contextlib import closing
    import sqlite3

    with twins.at(twins.legacy):
        before = json.loads(bs.backlog_context(scope="project", include=["recent"]))
        loaded = bs._load

        def load_then_a_peer_commits():
            data = loaded()
            with closing(sqlite3.connect(bs.ROOT / ".taskmaster" / "local" / "store.db",
                                         isolation_level=None)) as peer:
                seq = peer.execute("SELECT COALESCE(MAX(seq),0)+1 FROM changes").fetchone()[0]
                peer.execute("INSERT INTO changes(seq,ts,session,tool,kind,id,op) "
                             "VALUES(?,'2026-09-21T00:00:00Z','peer','peer','bug','B-999','create')",
                             (seq,))
                peer.execute("INSERT INTO entities(kind,id,status,doc,rev,updated_seq) "
                             "VALUES('bug','B-999','open',?,1,?)",
                             (json.dumps({"id": "B-999", "title": "After the snapshot",
                                          "status": "open"}), seq))
            return data

        monkeypatch.setattr(bs, "_load", load_then_a_peer_commits)
        during = json.loads(bs.backlog_context(scope="project", include=["recent"]))
    assert during["sequence"] == before["sequence"]
    assert during["selected"]["recent"] == before["selected"]["recent"]
