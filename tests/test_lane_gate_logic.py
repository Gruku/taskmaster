# plugins/taskmaster/tests/test_lane_gate_logic.py
import taskmaster_v3 as tv


def test_default_lane_bumps_high_stakes():
    assert tv.default_lane("critical") == "full"
    assert tv.default_lane("high") == "full"
    assert tv.default_lane("medium") == "standard"
    assert tv.default_lane("low") == "standard"
    assert tv.default_lane("") == "standard"


def test_required_gates_per_lane():
    assert tv.required_gates("express") == ("impl", "review-gate")
    assert tv.required_gates("standard")[0] == "spec"
    assert tv.required_gates("standard")[-1] == "review-gate"
    assert "plan-review" in tv.required_gates("full")
    assert tv.required_gates(None) == ()        # laneless => no pipeline
    assert tv.required_gates("bogus") == ()


def test_gate_satisfied_rules():
    assert tv.gate_satisfied({"status": "done"}) is True
    assert tv.gate_satisfied({"verdict": "pass"}) is True
    assert tv.gate_satisfied({"skipped": True, "reason": "x"}) is True
    assert tv.gate_satisfied({"verdict": "warn"}) is False   # warn does NOT satisfy
    assert tv.gate_satisfied({"verdict": "fail"}) is False
    assert tv.gate_satisfied({"status": "pending"}) is False
    assert tv.gate_satisfied(None) is False
    assert tv.gate_satisfied({}) is False


def test_outstanding_and_gate_state():
    task = {"lane": "express", "gates": {}}
    assert tv.outstanding_required_gates(task) == ["impl", "review-gate"]
    assert tv.compute_gate_state(task) == "impl:pending"

    task["gates"]["impl"] = {"status": "done"}
    assert tv.outstanding_required_gates(task) == ["review-gate"]
    assert tv.compute_gate_state(task) == "review-gate:pending"

    task["gates"]["review-gate"] = {"verdict": "pass"}
    assert tv.outstanding_required_gates(task) == []
    assert tv.compute_gate_state(task) == "review-gate:pass"


def test_gate_state_blocked_on_fail():
    task = {"lane": "express", "gates": {"impl": {"status": "done"},
                                         "review-gate": {"verdict": "fail"}}}
    assert tv.compute_gate_state(task) == "blocked@review-gate"


def test_gate_state_empty_for_laneless():
    assert tv.compute_gate_state({"gates": {}}) == ""
    assert tv.compute_gate_state({"lane": None, "gates": {}}) == ""
