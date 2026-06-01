"""Spec A — Task 13: lane-aware auto-mode.

Advancing the auto cursor to a stage that maps to a pipeline gate records the
matching gate on the cursor task. Pure-helper coverage (AUTO_STAGE_GATE +
auto_stages_for_lane) plus a server-level integration test driving the real
backlog_auto_start / backlog_auto_advance MCP-tool path.
"""
import re

import taskmaster_v3 as tv


def test_stage_gate_map():
    assert tv.AUTO_STAGE_GATE["SPEC_REVIEW"] == "spec-review"
    assert tv.AUTO_STAGE_GATE["WRITE_TESTS"] == "tests"
    assert tv.AUTO_STAGE_GATE["IMPLEMENT"] == "impl"
    assert tv.AUTO_STAGE_GATE["REVIEW_GATE"] == "review-gate"
    assert tv.AUTO_STAGE_GATE["PICK"] is None        # not a gate


def test_lane_stage_sequence():
    full = tv.auto_stages_for_lane("full")
    assert "PLAN" in full and "PLAN_REVIEW" in full
    standard = tv.auto_stages_for_lane("standard")
    # standard swaps the spec-review/plan/plan-review ceremony for one design review
    assert "DESIGN_REVIEW" in standard and "WRITE_TESTS" in standard
    assert "SPEC_REVIEW" not in standard and "PLAN" not in standard
    express = tv.auto_stages_for_lane("express")
    # express is the lean lane: no spec review, no tests gate — just impl + review-gate
    assert "SPEC_REVIEW" not in express and "WRITE_TESTS" not in express
    assert "IMPLEMENT" in express and "REVIEW_GATE" in express
    # unknown / None lane falls back to standard
    assert tv.auto_stages_for_lane(None) == standard


def test_advance_records_gate(tm_epic_phase):
    import backlog_server as _bs

    tid = re.search(
        r"[a-z0-9-]+-\d{3}",
        _bs.backlog_add_task("auto lane task", epic="test-epic", phase="dev",
                             priority="medium"),
    ).group(0)
    _bs.backlog_update_task(tid, "lane", "express")

    # Start a real auto run on the express-lane task; cursor begins at PICK.
    started = _bs.backlog_auto_start("task", tid)
    assert "Auto run started" in started, started

    # Walk the express lane: PICK -> IMPLEMENT -> REVIEW_GATE. Each advance to a
    # gate-mapped stage records the matching gate on the cursor task.
    _bs.backlog_auto_advance("IMPLEMENT")
    _bs.backlog_auto_advance("REVIEW_GATE")

    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["gates"]["impl"]["status"] == "done"
    assert task["gates"]["review-gate"]["verdict"] == "pass"
