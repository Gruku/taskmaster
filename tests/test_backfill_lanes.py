import re
import backlog_server as _bs
import taskmaster_v3 as tv


def test_backfill_sets_lane_and_grandfathers_passed_gates(tm_epic_phase):
    tid = re.search(r"[a-z0-9-]+-\d{3}",
                    _bs.backlog_add_task("legacy", epic="test-epic", phase="dev", priority="high")).group(0)
    data = _bs._load(); t, _ = _bs._find_task(data, tid)
    t.pop("lane", None); t.pop("gate_state", None); t["status"] = "in-review"
    _bs._mutate_and_save(data)

    out = _bs.backlog_backfill_lanes()
    assert "1" in out   # one task migrated
    t, _ = _bs._find_task(_bs._load(), tid)
    assert t["lane"] == "full"     # high priority => full
    for g in tv.required_gates("full"):
        assert tv.gate_satisfied(t["gates"][g])
        assert t["gates"][g].get("skipped") is True
        assert t["gates"][g]["reason"] == "grandfathered"
    assert "Error" not in _bs.backlog_complete_task(tid)


def test_backfill_skips_already_laned_tasks(tm_epic_phase):
    _bs.backlog_add_task("new", epic="test-epic", phase="dev", priority="medium")
    out = _bs.backlog_backfill_lanes()
    assert "0" in out or "already" in out.lower()
