import re
from taskmaster import backlog_server as _bs
from taskmaster import taskmaster_v3 as tv


def _new_task(lane="full"):
    out = _bs.backlog_add_task("skip task", epic="test-epic", phase="dev", priority="medium")
    tid = re.search(r"[a-z0-9-]+-\d{3}", out).group(0)
    _bs.backlog_update_task(tid, "lane", lane)
    return tid


def test_skip_gate_records_paper_trail(tm_epic_phase):
    tid = _new_task()
    msg = _bs.backlog_skip_gate(tid, "spec", "trivial config change")
    assert "Error" not in msg
    task, _ = _bs._find_task(_bs._load(), tid)
    rec = task["gates"]["spec"]
    assert rec["skipped"] is True
    assert rec["reason"] == "trivial config change"
    assert rec["by"]            # session-stamped (non-empty)
    assert "at" in rec
    assert tv.gate_satisfied(rec) is True


def test_skip_requires_reason(tm_epic_phase):
    tid = _new_task()
    assert "Error" in _bs.backlog_skip_gate(tid, "spec", "")


def test_skip_invalid_gate(tm_epic_phase):
    tid = _new_task()
    assert "Error" in _bs.backlog_skip_gate(tid, "nope", "because")


def test_skip_unblocks_ordering(tm_epic_phase):
    tid = _new_task()
    _bs.backlog_skip_gate(tid, "spec", "n/a")
    assert "Error" not in _bs.backlog_record_gate(tid, "spec-review", verdict="pass")


def test_clear_gate_removes_record_and_recomputes(tm_epic_phase):
    tid = _new_task("express")
    _bs.backlog_record_gate(tid, "impl", status="done")
    out = _bs.backlog_clear_gate(tid, "impl")
    assert "Error" not in out
    task, _ = _bs._find_task(_bs._load(), tid)
    assert "impl" not in (task.get("gates") or {})
    # gate_state tracks only blocking gates; for express that is review-gate
    assert task["gate_state"] == "review-gate:pending"


def test_clear_gate_absent(tm_epic_phase):
    tid = _new_task()
    assert "no" in _bs.backlog_clear_gate(tid, "tests").lower()


def test_clear_spec_review_alias(tm_epic_phase):
    tid = _new_task("full")
    _bs.backlog_record_gate(tid, "spec", status="done")
    _bs.backlog_set_spec_review(tid, "pass", "specs/x.md")
    _bs.backlog_clear_spec_review(tid)
    task, _ = _bs._find_task(_bs._load(), tid)
    assert "spec-review" not in (task.get("gates") or {})
    assert "spec_review" not in task
