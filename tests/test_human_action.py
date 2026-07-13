# tests/test_human_action.py
"""tm 4.5.0 human-gate: human_action field + in-review enforcement."""
import re
from taskmaster import backlog_server as _bs


def _t(lane="express"):
    tid = re.search(r"[a-z0-9-]+-\d{3}",
                    _bs.backlog_add_task("s", epic="test-epic", phase="dev", priority="medium")).group(0)
    _bs.backlog_update_task(tid, "lane", lane)
    return tid


def test_human_action_field_updatable_and_persisted(tm_epic_phase):
    tid = _t()
    out = _bs.backlog_update_task(tid, "human_action", "add OPENAI_API_KEY to .env")
    assert "Error" not in out
    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["human_action"] == "add OPENAI_API_KEY to .env"


def test_human_action_visible_in_get_task(tm_epic_phase):
    tid = _t()
    _bs.backlog_update_task(tid, "human_action", "rotate the deploy token")
    out = _bs.backlog_get_task(tid)
    assert "rotate the deploy token" in out


def test_human_action_visible_in_list_tasks(tm_epic_phase):
    tid = _t()
    _bs.backlog_update_task(tid, "human_action", "install the CUDA driver")
    out = _bs.backlog_list_tasks()
    assert "install the CUDA driver" in out


def test_complete_to_in_review_requires_human_action(tm_epic_phase):
    tid = _t()
    _bs.backlog_update_task(tid, "status", "in-progress")
    out = _bs.backlog_complete_task(tid, target_status="in-review")
    assert "Error" in out and "human_action" in out
    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["status"] == "in-progress"


def test_complete_to_in_review_with_human_action(tm_epic_phase):
    tid = _t()
    _bs.backlog_update_task(tid, "status", "in-progress")
    out = _bs.backlog_complete_task(tid, target_status="in-review",
                                    human_action="add OPENAI_API_KEY to .env")
    assert "Error" not in out
    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["status"] == "in-review"
    assert task["human_action"] == "add OPENAI_API_KEY to .env"


def test_complete_to_in_review_accepts_preexisting_human_action(tm_epic_phase):
    tid = _t()
    _bs.backlog_update_task(tid, "status", "in-progress")
    _bs.backlog_update_task(tid, "human_action", "grant repo access")
    assert "Error" not in _bs.backlog_complete_task(tid, target_status="in-review")


def test_complete_to_done_clears_human_action(tm_epic_phase):
    tid = _t()
    _bs.backlog_update_task(tid, "status", "in-progress")
    _bs.backlog_record_gate(tid, "impl", status="done")
    _bs.backlog_record_gate(tid, "review-gate", verdict="pass")
    _bs.backlog_complete_task(tid, target_status="in-review", human_action="add key")
    out = _bs.backlog_complete_task(tid, target_status="done")
    assert "Error" not in out
    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["status"] == "done"
    assert "human_action" not in task


def test_no_skip_review_nag_on_direct_done(tm_epic_phase):
    tid = _t()
    _bs.backlog_update_task(tid, "status", "in-progress")
    _bs.backlog_record_gate(tid, "impl", status="done")
    _bs.backlog_record_gate(tid, "review-gate", verdict="pass")
    out = _bs.backlog_complete_task(tid)
    assert "skipping the in-review stage" not in out
