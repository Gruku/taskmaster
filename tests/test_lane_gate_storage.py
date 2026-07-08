import yaml
from taskmaster import taskmaster_v3 as tv


def test_lane_and_gate_state_are_slim():
    assert "lane" in tv.SLIM_FIELDS["task"]
    assert "gate_state" in tv.SLIM_FIELDS["task"]


def test_gates_is_heavy():
    assert "gates" in tv.HEAVY_FIELDS


def test_gates_split_to_per_task_file(tmp_path):
    task = {
        "id": "e1-001", "title": "X", "status": "in-progress",
        "lane": "express", "gate_state": "impl:pending",
        "gates": {"impl": {"status": "done", "at": "2026-05-29T00:00"}},
    }
    slim, heavy, body = tv._split_task_for_v3(task)
    assert slim["lane"] == "express"
    assert slim["gate_state"] == "impl:pending"
    assert "gates" not in slim                 # heavy, not in backlog.yaml
    assert heavy["gates"]["impl"]["status"] == "done"
