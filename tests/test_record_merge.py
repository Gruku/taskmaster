import re
from taskmaster import backlog_server as _bs


def _task(epic="test-epic"):
    out = _bs.backlog_add_task("merge task", epic, priority="medium", phase="dev")
    return re.search(r"[a-z0-9-]+-\d{3}", out).group(0)


def test_record_merge_stamps_rung(tm_epic_phase):
    tid = _task()
    msg = _bs.backlog_record_merge(tid, "develop", "abc1234def")
    assert "Error" not in msg
    task, _ = _bs._find_task(_bs._load(), tid)
    rec = task["merge_status"]["develop"]
    assert rec["merge_commit"] == "abc1234def"
    assert "merged_at" in rec


def test_record_merge_is_idempotent_overwrite(tm_epic_phase):
    tid = _task()
    _bs.backlog_record_merge(tid, "stage", "first")
    _bs.backlog_record_merge(tid, "stage", "second")
    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["merge_status"]["stage"]["merge_commit"] == "second"
    assert len(task["merge_status"]) == 1


def test_record_merge_updates_merge_gate_state(tm_epic_phase):
    tid = _task()
    _bs.backlog_record_merge(tid, "develop", "x")
    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["merge_gate_state"] == "develop"


def test_record_merge_unknown_task(tm_epic_phase):
    assert "Error" in _bs.backlog_record_merge("nope-999", "develop", "x")


def test_record_merge_records_raw_branch_label(tm_epic_phase):
    tid = _task()
    msg = _bs.backlog_record_merge(tid, "branch:hotfix-1", "y")
    assert "Error" not in msg
    task, _ = _bs._find_task(_bs._load(), tid)
    assert "branch:hotfix-1" in task["merge_status"]


# ── team-server-008 D6: expect_commit, a compare-and-set on the rung's current merge commit ──────────────

import json as _json
import pytest as _pytest

from native_twins import make_twins


def _seed_one():
    _bs.backlog_add_task(title="Merge task", epic="test-epic", phase="dev")


@_pytest.fixture
def pair(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed_one)


def _recorded(twins, rung="stage"):
    out = {}
    for name, root in (("legacy", twins.legacy), ("native", twins.native)):
        from native_twins import committed
        doc = committed(root)[("task", "test-epic-001")][0]
        out[name] = ((doc.get("merge_status") or {}).get(rung) or {}).get("merge_commit")
    return out


def test_expect_commit_empty_records_a_first_stamp_and_refuses_a_second(pair):
    legacy, native = pair.same("backlog_record_merge", task_id="test-epic-001", rung="stage", sha="aaa1111",
                               expect_commit="")
    assert legacy.startswith("Recorded merge for rung `stage`")
    assert _recorded(pair) == {"legacy": "aaa1111", "native": "aaa1111"}
    legacy, native = pair.same("backlog_record_merge", task_id="test-epic-001", rung="stage", sha="bbb2222",
                               expect_commit="")
    assert legacy.startswith("Error: rung `stage` of `test-epic-001` records aaa1111, not nothing")
    assert _recorded(pair) == {"legacy": "aaa1111", "native": "aaa1111"}


def test_expect_commit_equal_to_the_current_commit_records(pair):
    pair.same("backlog_record_merge", task_id="test-epic-001", rung="stage", sha="aaa1111")
    legacy, _ = pair.same("backlog_record_merge", task_id="test-epic-001", rung="stage", sha="bbb2222",
                          expect_commit="aaa1111")
    assert legacy.startswith("Recorded merge")
    assert _recorded(pair) == {"legacy": "bbb2222", "native": "bbb2222"}


def test_a_different_expect_commit_refuses_with_the_named_text_and_changes_nothing(pair):
    pair.same("backlog_record_merge", task_id="test-epic-001", rung="stage", sha="aaa1111")
    legacy, _ = pair.same("backlog_record_merge", task_id="test-epic-001", rung="stage", sha="bbb2222",
                          expect_commit="ccc3333")
    assert legacy == "Error: rung `stage` of `test-epic-001` records aaa1111, not ccc3333"
    assert _recorded(pair) == {"legacy": "aaa1111", "native": "aaa1111"}


def test_none_keeps_todays_overwrite(pair):
    pair.same("backlog_record_merge", task_id="test-epic-001", rung="stage", sha="aaa1111")
    pair.same("backlog_record_merge", task_id="test-epic-001", rung="stage", sha="bbb2222")
    assert _recorded(pair) == {"legacy": "bbb2222", "native": "bbb2222"}


def test_a_batch_command_cannot_smuggle_a_mismatched_expect_commit(pair):
    pair.same("backlog_record_merge", task_id="test-epic-001", rung="stage", sha="aaa1111")
    command = {"operation": "task.merge", "arguments": {"id": "test-epic-001", "rung": "stage", "sha": "bbb2222",
                                                         "merged_at": "", "expect_commit": "nothere"}}
    answer = pair.call("backlog_batch_update", operations="", commands=[command])[1]
    assert _json.loads(answer)["ok"] is False
    assert _recorded(pair)["native"] == "aaa1111"
    good = dict(command, arguments=dict(command["arguments"], expect_commit="aaa1111"))
    assert _json.loads(pair.call("backlog_batch_update", operations="", commands=[good])[1])["ok"] is True
    assert _recorded(pair)["native"] == "bbb2222"
