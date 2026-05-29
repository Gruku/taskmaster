import re
import backlog_server as _bs


def _new_task(lane="full"):
    out = _bs.backlog_add_task("gate task", epic="test-epic", phase="dev", priority="medium")
    tid = re.search(r"[a-z0-9-]+-\d{3}", out).group(0)
    _bs.backlog_update_task(tid, "lane", lane)
    return tid


def test_record_status_gate(tm_epic_phase):
    tid = _new_task()
    msg = _bs.backlog_record_gate(tid, "spec", status="done")
    assert "Error" not in msg
    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["gates"]["spec"]["status"] == "done"
    assert "at" in task["gates"]["spec"]
    assert task["gate_state"] == "spec-review:pending"   # spec satisfied; next is spec-review


def test_record_verdict_gate_with_meta(tm_epic_phase):
    tid = _new_task()
    _bs.backlog_record_gate(tid, "spec", status="done")
    msg = _bs.backlog_record_gate(tid, "spec-review", verdict="pass",
                                  critical_count=0, important_count=2)
    assert "Error" not in msg
    task, _ = _bs._find_task(_bs._load(), tid)
    rec = task["gates"]["spec-review"]
    assert rec["verdict"] == "pass"
    assert rec["important_count"] == 2


def test_out_of_order_recording_rejected(tm_epic_phase):
    tid = _new_task()
    msg = _bs.backlog_record_gate(tid, "review-gate", verdict="pass")
    assert "Error" in msg
    assert "spec" in msg  # names the first unsatisfied earlier gate


def test_invalid_gate_and_verdict_rejected(tm_epic_phase):
    tid = _new_task()
    assert "Error" in _bs.backlog_record_gate(tid, "frobnicate", status="done")
    assert "Error" in _bs.backlog_record_gate(tid, "spec-review", verdict="great")


def test_record_gate_laneless_task_allowed_no_order_check(tm_epic_phase):
    out = _bs.backlog_add_task("laneless", epic="test-epic", phase="dev", priority="medium")
    tid = re.search(r"[a-z0-9-]+-\d{3}", out).group(0)
    data = _bs._load(); t, _ = _bs._find_task(data, tid); t.pop("lane", None); _bs._mutate_and_save(data)
    msg = _bs.backlog_record_gate(tid, "review-gate", verdict="pass")
    assert "Error" not in msg


def test_set_spec_review_writes_gate_record(tm_epic_phase):
    tid = _new_task("full")
    _bs.backlog_record_gate(tid, "spec", status="done")
    out = _bs.backlog_set_spec_review(tid, "pass", "specs/x.md", critical_count=0)
    assert "Error" not in out
    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["gates"]["spec-review"]["verdict"] == "pass"
    # legacy mirror preserved for back-compat consumers
    assert task["spec_review"]["verdict"] == "pass"
