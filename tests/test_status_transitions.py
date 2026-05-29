# plugins/taskmaster/tests/test_status_transitions.py
"""Spec A Task 11: forward-transition table + done-gate on backlog_update_task status."""
import re
import backlog_server as _bs


def _t(lane="express"):
    tid = re.search(r"[a-z0-9-]+-\d{3}",
                    _bs.backlog_add_task("s", epic="test-epic", phase="dev", priority="medium")).group(0)
    _bs.backlog_update_task(tid, "lane", lane)
    return tid


def test_todo_to_done_rejected_via_update(tm_epic_phase):
    tid = _t()
    out = _bs.backlog_update_task(tid, "status", "done")
    assert "Error" in out
    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["status"] == "todo"


def test_forward_to_in_progress_ok(tm_epic_phase):
    tid = _t()
    assert "Error" not in _bs.backlog_update_task(tid, "status", "in-progress")


def test_update_to_done_applies_gate_check(tm_epic_phase):
    tid = _t()
    _bs.backlog_update_task(tid, "status", "in-progress")
    blocked = _bs.backlog_update_task(tid, "status", "done")   # gates outstanding
    assert "Error" in blocked or "Cannot" in blocked
    _bs.backlog_record_gate(tid, "impl", status="done")
    _bs.backlog_record_gate(tid, "review-gate", verdict="pass")
    assert "Error" not in _bs.backlog_update_task(tid, "status", "done")


def test_backward_jump_rejected(tm_epic_phase):
    tid = _t()
    _bs.backlog_update_task(tid, "status", "in-progress")
    _bs.backlog_update_task(tid, "status", "in-review")
    assert "Error" in _bs.backlog_update_task(tid, "status", "todo")   # in-review->todo illegal


def test_in_review_allowed_with_outstanding_gates(tm_epic_phase):
    tid = _t()
    _bs.backlog_update_task(tid, "status", "in-progress")
    assert "Error" not in _bs.backlog_update_task(tid, "status", "in-review")


def test_laneless_skips_transition_table(tm_epic_phase):
    tid = re.search(r"[a-z0-9-]+-\d{3}",
                    _bs.backlog_add_task("s", epic="test-epic", phase="dev", priority="medium")).group(0)
    data = _bs._load(); t, _ = _bs._find_task(data, tid); t.pop("lane", None); _bs._mutate_and_save(data)
    assert "Error" not in _bs.backlog_update_task(tid, "status", "done")   # laneless = old permissive behavior
