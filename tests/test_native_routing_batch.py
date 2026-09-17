"""User intent: `backlog_batch_update` on a native store must keep the tool's
per-line contract — each bad line reports its own error while every good line
still commits, in order, in one transaction — even though the native structured
batch is all-or-nothing (N08 constraint), and `backlog_batch_preview` must say
what the legacy preview says.
"""
from __future__ import annotations

import pytest

from taskmaster import backlog_server as bs
from native_twins import committed, make_twins


def _seed():
    bs.backlog_add_phase(phase_id="later", name="Later Work")
    bs.backlog_add_epic(epic_id="other", name="Other Epic", done_when="never")
    for title, epic in (("One", "test-epic"), ("Two", "test-epic"), ("Three", "test-epic"),
                        ("Four", "other"), ("Five", "other")):
        bs.backlog_add_task(title=title, epic=epic, phase="dev")
    bs.backlog_update_task(task_id="test-epic-002", field="lane", value="express")
    bs.backlog_bug_create(title="Open bug", found_in="test-epic-003")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _check(twins):
    twins.assert_state_matches()
    twins.assert_files_match()


def test_mixed_lines_apply_in_order_with_per_line_errors(twins):
    operations = "\n".join([
        "update test-epic-001 title Renamed once",
        "update test-epic-001 title Renamed twice",
        "update test-epic-001 not_a_field x",
        "update ghost-001 title Nope",
        "update test-epic-001 priority P0",
        "update test-epic-001 priority urgent",
        "update test-epic-001 docs plan:docs/p.md",
        "update test-epic-001 docs nocolon",
        "update test-epic-001 docs wrong:x",
        "update test-epic-001 depends_on test-epic-002, ghost-9",
        "update test-epic-001 depends_on test-epic-002",
        "update test-epic-001 stage seven",
        "update test-epic-001 stage 7",
        "update test-epic-001 locked_by none",
        "update test-epic-001 phase Later Work",
        "update test-epic-001 phase ghost",
        "update test-epic-001 lane warp",
        "update test-epic-001 lane standard",
        "update test-epic-001 area nowhere",
        "update test-epic-001 status flying",
        "update test-epic-001 status in-review",
        "update test-epic-001 status done",
        "update test-epic-001 notes Mentions test-epic-002",
        "",
        "lonely",
        "frobnicate test-epic-001",
        "update test-epic-001 title",
    ])
    twins.same("backlog_batch_update", operations=operations)
    _check(twins)


def test_status_complete_pick_and_archive_lines_see_earlier_lines(twins):
    operations = "\n".join([
        "complete test-epic-001",
        "pick test-epic-001",
        "status test-epic-001 in-progress",
        "status test-epic-001 done",
        "complete test-epic-003",
        "pick test-epic-003",
        "complete test-epic-003",
        "status test-epic-002 in-progress",
        "complete test-epic-002",
        "status test-epic-002 done",
        "status ghost-1 done",
        "status test-epic-004 bogus",
        "archive other-001 wont-fix",
        "archive other-001",
        "pick other-001",
        "archive ghost-2",
        "status other-002 archived",
        "status other-002 todo",
    ])
    twins.same("backlog_batch_update", operations=operations)
    _check(twins)


def test_epic_lines_including_the_archive_cascade(twins):
    operations = "\n".join([
        "update_epic other name Renamed Epic",
        "update_epic other bogus x",
        "update_epic ghost name x",
        "update_epic other status sideways",
        "update_epic other status active",
        "update_epic test-epic docs raw-string-kept",
        "pick other-002",
        "update_epic other status archived",
        "update_epic other status archived",
        "update other-002 title After the cascade",
    ])
    twins.same("backlog_batch_update", operations=operations)
    _check(twins)


def test_a_batch_with_only_errors_commits_nothing(twins):
    twins.same("backlog_batch_update", operations="update ghost-1 title x\nstatus ghost-2 done")
    _check(twins)


def test_a_batch_of_no_op_lines_matches(twins):
    twins.same("backlog_batch_update", operations="update test-epic-001 title One")
    _check(twins)


def test_preview_matches(twins):
    twins.same("backlog_update_task", task_id="test-epic-002", field="depends_on", value="test-epic-001")
    twins.same("backlog_batch_preview", operations="\n".join([
        "complete test-epic-001", "archive test-epic-001", "archive test-epic-001 superseded",
        "pick test-epic-002", "pick ghost-1", "status test-epic-001", "status test-epic-001 flying",
        "status test-epic-002 done", "status test-epic-003 in-progress", "frob test-epic-001", "x",
    ]))


def test_more_accepted_lines_than_one_native_batch_holds_is_refused_whole(twins):
    """Recorded N08 difference: the native structured batch holds at most 100
    commands and must stay one transaction, so a larger accepted set is refused
    before anything commits rather than split into several commits."""
    before = committed(twins.native)
    operations = "\n".join(f"update test-epic-001 title Title {n}" for n in range(101))
    with twins.at(twins.native):
        answer = bs.backlog_batch_update(operations=operations)
    assert answer.startswith("Error:") and "100" in answer, answer
    assert committed(twins.native) == before
