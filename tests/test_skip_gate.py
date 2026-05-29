import re
import backlog_server as _bs
import taskmaster_v3 as tv


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
