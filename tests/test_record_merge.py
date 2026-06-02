import re
import backlog_server as _bs


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
