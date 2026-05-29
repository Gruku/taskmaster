import re
import backlog_server as _bs


def _ready(lane="express"):
    tid = re.search(r"[a-z0-9-]+-\d{3}",
                    _bs.backlog_add_task("c", epic="test-epic", phase="dev", priority="medium")).group(0)
    _bs.backlog_update_task(tid, "lane", lane)
    _bs.backlog_update_task(tid, "status", "in-progress")
    return tid


def test_complete_blocked_when_gates_outstanding(tm_epic_phase):
    tid = _ready("express")
    out = _bs.backlog_complete_task(tid)            # no gates recorded
    assert "Error" in out or "Cannot" in out
    assert "impl" in out and "review-gate" in out
    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["status"] != "done"


def test_complete_allowed_when_gates_satisfied(tm_epic_phase):
    tid = _ready("express")
    _bs.backlog_record_gate(tid, "impl", status="done")
    _bs.backlog_record_gate(tid, "review-gate", verdict="pass")
    out = _bs.backlog_complete_task(tid)
    assert "Error" not in out and "Cannot" not in out
    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["status"] == "done"


def test_complete_allowed_with_skips(tm_epic_phase):
    tid = _ready("express")
    _bs.backlog_skip_gate(tid, "impl", "no code, doc-only")
    _bs.backlog_record_gate(tid, "review-gate", verdict="pass")
    assert "Error" not in _bs.backlog_complete_task(tid)


def test_laneless_task_exempt(tm_epic_phase):
    tid = re.search(r"[a-z0-9-]+-\d{3}",
                    _bs.backlog_add_task("c", epic="test-epic", phase="dev", priority="medium")).group(0)
    data = _bs._load(); t, _ = _bs._find_task(data, tid); t.pop("lane", None); t["status"] = "in-progress"; _bs._mutate_and_save(data)
    out = _bs.backlog_complete_task(tid)            # no gates, but laneless => allowed
    assert "Error" not in out and "Cannot" not in out
