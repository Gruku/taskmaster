import re
import backlog_server as _bs


def _t(lane="express"):
    tid = re.search(r"[a-z0-9-]+-\d{3}",
                    _bs.backlog_add_task("p", epic="test-epic", phase="dev", priority="medium")).group(0)
    _bs.backlog_update_task(tid, "lane", lane)
    return tid


def test_pipeline_shows_lane_and_gate_marks(tm_epic_phase):
    tid = _t("express")
    _bs.backlog_record_gate(tid, "impl", status="done")
    out = _bs.backlog_task_pipeline(tid)
    assert "express" in out
    assert "impl" in out and "review-gate" in out
    assert "pending" in out.lower()           # review-gate still pending


def test_pipeline_laneless(tm_epic_phase):
    tid = re.search(r"[a-z0-9-]+-\d{3}",
                    _bs.backlog_add_task("p", epic="test-epic", phase="dev", priority="medium")).group(0)
    data = _bs._load(); t, _ = _bs._find_task(data, tid); t.pop("lane", None); _bs._mutate_and_save(data)
    out = _bs.backlog_task_pipeline(tid)
    assert "laneless" in out.lower() or "no lane" in out.lower()
