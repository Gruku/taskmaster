# plugins/taskmaster/tests/test_lane_assignment.py
"""Task 3 of Spec A: verify default lane and gate_state are set on task creation."""
import re

import backlog_server as _bs


def _extract_id(out: str) -> str:
    # return format: "Added `{new_id}` — {title} ({priority}) under {name}"
    m = re.search(r"[a-z0-9-]+-\d{3}", out)
    assert m, f"no id in: {out}"
    return m.group(0)


def test_new_task_default_lane_standard(tm_epic_phase):
    # tm_epic_phase yields tmp_path; epic "test-epic" and phase "dev" are pre-seeded
    out = _bs.backlog_add_task(epic="test-epic", title="a normal task", priority="medium", phase="dev")
    assert "Error" not in out, f"add_task failed: {out}"
    data = _bs._load()
    result = _bs._find_task(data, _extract_id(out))
    assert result is not None
    task, _ = result
    assert task["lane"] == "standard"
    # gate_state reflects first unsatisfied BLOCKING gate; design-review is first for standard
    assert task.get("gate_state") == "design-review:pending"


def test_new_task_high_priority_bumped_to_full(tm_epic_phase):
    out = _bs.backlog_add_task(epic="test-epic", title="a scary task", priority="high", phase="dev")
    assert "Error" not in out, f"add_task failed: {out}"
    data = _bs._load()
    result = _bs._find_task(data, _extract_id(out))
    assert result is not None
    task, _ = result
    assert task["lane"] == "full"
    # gate_state reflects first unsatisfied BLOCKING gate; spec-review is first for full
    assert task.get("gate_state") == "spec-review:pending"


def test_new_task_critical_priority_bumped_to_full(tm_epic_phase):
    out = _bs.backlog_add_task(epic="test-epic", title="a critical task", priority="critical", phase="dev")
    assert "Error" not in out, f"add_task failed: {out}"
    data = _bs._load()
    result = _bs._find_task(data, _extract_id(out))
    assert result is not None
    task, _ = result
    assert task["lane"] == "full"
    assert task.get("gate_state") == "spec-review:pending"


def test_new_task_low_priority_is_standard(tm_epic_phase):
    out = _bs.backlog_add_task(epic="test-epic", title="a low-pri task", priority="low", phase="dev")
    assert "Error" not in out, f"add_task failed: {out}"
    data = _bs._load()
    result = _bs._find_task(data, _extract_id(out))
    assert result is not None
    task, _ = result
    assert task["lane"] == "standard"
    assert task.get("gate_state") == "design-review:pending"


def test_update_lane_valid_and_recomputes_gate_state(tm_epic_phase):
    out = _bs.backlog_add_task("lane override", epic="test-epic", phase="dev", priority="medium")
    tid = _extract_id(out)
    msg = _bs.backlog_update_task(tid, "lane", "express")
    assert "Error" not in msg
    data = _bs._load()
    task, _ = _bs._find_task(data, tid)
    assert task["lane"] == "express"
    assert task["gate_state"] == "review-gate:pending"   # recomputed for the new lane


def test_update_lane_invalid_rejected(tm_epic_phase):
    tid = _extract_id(_bs.backlog_add_task("bad lane", epic="test-epic", phase="dev", priority="medium"))
    msg = _bs.backlog_update_task(tid, "lane", "turbo")
    assert "Error" in msg
    assert "turbo" in msg
