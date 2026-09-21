"""User intent: measure N09's first exit half — representative agent journeys need
fewer calls and fewer bytes on the new tools while yielding the same required
context — on both stores, and say so plainly where they do not.

A seeded project of six epics is built once through the tools, legacy and native.
Each journey then runs its old path (today's playbooks) and its new path
(`backlog_context`, `backlog_changes_since`, claims) on fresh copies of the same
starting state. The required context is fixed here, from the seed, not read back
from the tool under test. Run with `-s` to print the measured table.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import shutil

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
import agent_journeys as aj
from native_twins import activate_native, install_clock, point_server_at, scaffold

EPICS = ("test-epic", "alpha", "beta", "gamma", "delta", "omega")
PER_EPIC = 8
NOTES = ("Reproduce against the fixture corpus, then narrow the parser change to the "
         "tokenizer boundary. Keep the public signature; callers in three packages rely on it. ")

PICK, PICK_DEP_DONE, PICK_DEP_OPEN = "beta-008", "beta-001", "beta-002"
RESUME, CLOSE = "gamma-008", "delta-008"
NOISE = ("alpha-003", "alpha-004", "omega-003", "omega-004", "test-epic-003", "test-epic-004")


@bs._transactional("test_journey_shapes")
def _shape(mutate):
    data = bs._load()
    mutate(data)
    bs._mutate_and_save(data)
    return "ok"


def _seed(ids: dict) -> None:
    bs.backlog_update_epic(epic_id="test-epic", field="status", value="active")
    for epic in EPICS[1:]:
        bs.backlog_add_epic(epic_id=epic, name=f"{epic.title()} workstream",
                            done_when="its tasks are done", status="active")
    for epic in EPICS:
        for n in range(1, PER_EPIC + 1):
            bs.backlog_add_task(title=f"{epic.title()} step {n}: tighten the pipeline", epic=epic,
                                phase="dev", priority=("high", "medium", "low")[n % 3], notes=NOTES)

    def shapes(data):
        for epic in data["epics"]:
            for index, task in enumerate(epic["tasks"]):
                # Laneless unless a journey needs gates: a default lane leaves every
                # review gate pending and the whole project blocked.
                task["lane"], task["gates"] = "", {}
                task["status"] = ("done", "in-progress", "todo", "todo")[index % 4]
                if task["id"] in (PICK, RESUME, CLOSE):
                    task["status"] = "todo"
        pick, _ = bs._find_task(data, PICK)
        pick.update(lane="express", depends_on=[PICK_DEP_DONE, PICK_DEP_OPEN])
        close, _ = bs._find_task(data, CLOSE)
        close["lane"] = "express"
    assert _shape(shapes).startswith("ok")

    ids["pick_bug"] = aj.created_id(bs.backlog_bug_create(title="Crash on empty input", found_in=PICK,
                                                        severity="P1"))
    ids["close_bug"] = aj.created_id(bs.backlog_bug_create(title="Typo in a log line", found_in=CLOSE,
                                                         severity="P3"))
    bs.backlog_bug_create(title="Unrelated flake", severity="P2")
    ids["pick_handover"] = aj.created_id(bs.backlog_handover_create(
        tldr="Spiked the tokenizer change", next_action="port the spike onto the branch",
        task_ids=[PICK], thread="beta"))
    bs.backlog_handover_create(tldr="Mapped the gamma call sites", task_ids=[RESUME], thread="gamma")
    bs.backlog_handover_create(tldr="Omega plan drafted", next_action="review the omega plan",
                               task_ids=["omega-002"], thread="omega")
    ids["issue"] = aj.created_id(bs.backlog_issue_create(title="Nightly build is slow", severity="P2",
                                                         evidence="Seen for a week."))
    bs.backlog_note(action="create", text="Ask about the release date")
    bs.backlog_note(action="create", text="Parser owners are away Friday", pinned=True)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """The seeded project, legacy and native, built once for every journey."""
    mp = pytest.MonkeyPatch()
    root = tmp_path_factory.mktemp("journeys")
    ids = {"pick": PICK, "resume": RESUME, "resume_epic": "gamma", "resume_thread": "gamma",
           "close": CLOSE, "noise": NOISE}
    try:
        install_clock(mp)
        legacy = scaffold(root / "legacy")
        point_server_at(mp, legacy)
        bs.backlog_add_epic(epic_id="test-epic", name="Test Epic", done_when="all test tasks complete")
        bs.backlog_add_phase(phase_id="dev", name="Development")
        _seed(ids)
        store.reset_for_tests()
        native = root / "native"
        shutil.copytree(legacy, native)
        for leftover in native.rglob("*.tmp.*"):
            os.remove(leftover)
        activate_native(native)
        store.reset_for_tests()
        yield {"legacy": legacy, "native": native}, ids
    finally:
        store.reset_for_tests()
        mp.undo()


def _fresh(source, tmp_path, monkeypatch):
    install_clock(monkeypatch)

    @contextmanager
    def fresh(label):
        target = tmp_path / label
        shutil.copytree(source, target)
        for leftover in target.rglob("*.tmp.*"):
            os.remove(leftover)
        point_server_at(monkeypatch, target)
        try:
            yield
        finally:
            store.reset_for_tests()
    return fresh


# The seed's ground truth: what each journey must learn before it acts. A close
# needs the open bugs of every severity, because the close refuses on any of them.
def _required(journey, ids):
    if journey == "pick":
        return {("gate", "review-gate"), ("dependency", PICK_DEP_OPEN),
                ("bug", ids["pick_bug"]), ("handover", ids["pick_handover"])}
    if journey == "resume":
        return {("bug", ids["resume_bug"]), ("handover", ids["resume_handover"])}
    return {("gate", "review-gate"), ("bug", ids["close_bug"])}


ORIENT_REQUIRED = ("port the spike onto the branch", "review the omega plan",
                   "Ask about the release date", "Parser owners are away Friday")

MEASURED: dict = {}


def _measure(base, tmp_path, monkeypatch, side, journey):
    """Each journey is measured once per store; every test reads the same run."""
    key = (journey.name, side)
    if key not in MEASURED:
        roots, ids = base
        ids = {**ids, "close_open_bugs": [ids["close_bug"]]}
        MEASURED[key] = aj.measure(journey, ids, _fresh(roots[side], tmp_path / side, monkeypatch))
        print(f"\n[{side}] {MEASURED[key].row()}")
    return MEASURED[key]


SIDES = ("legacy", "native")
JOURNEYS = {journey.name: journey for journey in aj.JOURNEYS}


def _journeys(**failing):
    """Every journey, with the ones named in `failing` marked as measured failures."""
    return [pytest.param(journey, id=journey.name,
                         marks=[pytest.mark.xfail(strict=True, reason=failing[journey.name])]
                         if journey.name in failing else [])
            for journey in aj.JOURNEYS]


# Measured, not wished for: the project-scoped context selects pinned notes only
# (`note_operational.pinned`), so an unpinned desk note the old `backlog_note`
# list shows is not in the new orientation at all.
ORIENT_DROPS_NOTES = "project context selects pinned notes only; an unpinned desk note is lost"


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("journey", _journeys(orient=ORIENT_DROPS_NOTES))
def test_the_new_path_yields_every_required_fact(base, tmp_path, monkeypatch, side, journey):
    measured = _measure(base, tmp_path, monkeypatch, side, journey)
    if journey.name == "orient":
        for fact in ORIENT_REQUIRED:
            assert fact in measured.old.text(), fact
            assert fact in measured.new.text(), fact
        return
    required = _required(journey.name, measured.ids)
    assert measured.required == required, (measured.required, required)
    assert required <= measured.named_new
    assert measured.new.contexts()[0]["mandatory"]["clear"] is False
    # "Same required context": whatever the old path named, the new path names too.
    assert measured.covered_old <= measured.named_new
    if journey.name in ("pick", "resume"):   # the old path's issue list (step 5b)
        assert ("issue", measured.ids["issue"]) in measured.named_new


@pytest.mark.parametrize("side", SIDES)
def test_both_paths_end_in_the_same_place(base, tmp_path, monkeypatch, side):
    for name, tool in (("pick", "backlog_pick_task"), ("close", "backlog_complete_task")):
        measured = _measure(base, tmp_path, monkeypatch, side, JOURNEYS[name])
        old, new = ([answer for called, answer in trace.answers if called == tool][-1]
                    for trace in (measured.old, measured.new))
        assert not old.startswith("Error") and "Cannot" not in old, old
        assert new == old


def test_both_stores_give_the_same_required_context(base, tmp_path, monkeypatch):
    for journey in aj.JOURNEYS:
        if journey.focus is None:
            continue
        legacy, native = (_measure(base, tmp_path, monkeypatch, side, journey) for side in SIDES)
        assert legacy.required == native.required, journey.name
        assert legacy.new.contexts()[0]["mandatory"] == native.new.contexts()[0]["mandatory"]


# Measured, not wished for: a context answer carries a fixed JSON envelope (store
# id, provenance, budget block) of several hundred bytes. Where it replaces short
# markdown answers rather than `backlog_status`, one call fewer still costs more.
MORE_BYTES = "fewer calls, but the context envelope outweighs the short answers it replaces"


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("journey", _journeys(orient=MORE_BYTES, close=MORE_BYTES))
def test_the_new_path_needs_fewer_calls_and_fewer_bytes(base, tmp_path, monkeypatch, side, journey):
    measured = _measure(base, tmp_path, monkeypatch, side, journey)
    assert measured.new.count < measured.old.count, measured.row()
    assert measured.new.bytes < measured.old.bytes, measured.row()
