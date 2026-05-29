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
    # first required gate for standard lane is "spec"
    assert task.get("gate_state") == "spec:pending"


def test_new_task_high_priority_bumped_to_full(tm_epic_phase):
    out = _bs.backlog_add_task(epic="test-epic", title="a scary task", priority="high", phase="dev")
    assert "Error" not in out, f"add_task failed: {out}"
    data = _bs._load()
    result = _bs._find_task(data, _extract_id(out))
    assert result is not None
    task, _ = result
    assert task["lane"] == "full"
    # first required gate for full lane is "spec"
    assert task.get("gate_state") == "spec:pending"


def test_new_task_critical_priority_bumped_to_full(tm_epic_phase):
    out = _bs.backlog_add_task(epic="test-epic", title="a critical task", priority="critical", phase="dev")
    assert "Error" not in out, f"add_task failed: {out}"
    data = _bs._load()
    result = _bs._find_task(data, _extract_id(out))
    assert result is not None
    task, _ = result
    assert task["lane"] == "full"
    assert task.get("gate_state") == "spec:pending"


def test_new_task_low_priority_is_standard(tm_epic_phase):
    out = _bs.backlog_add_task(epic="test-epic", title="a low-pri task", priority="low", phase="dev")
    assert "Error" not in out, f"add_task failed: {out}"
    data = _bs._load()
    result = _bs._find_task(data, _extract_id(out))
    assert result is not None
    task, _ = result
    assert task["lane"] == "standard"
    assert task.get("gate_state") == "spec:pending"
