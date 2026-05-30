"""Spec A — C2: auto-mode walks each task's LANE-specific stage sequence.

Before C2 the auto-task recipe walked a fixed legacy sequence
(PICK→SPEC_REVIEW→WRITE_TESTS→IMPLEMENT→TEST→REVIEW_GATE→…). That only ever
recorded the right blocking gates for the EXPRESS lane; standard tasks never
recorded `design-review` and full tasks never recorded `plan-review`, so
`backlog_complete_task` stayed blocked.

C2 makes `backlog_auto_start` seed the cursor with the lane's `planned_stages`
and lets a no-arg `backlog_auto_advance()` step through them, recording each
stage's gate in order. These tests prove:

  - the planned sequence is lane-correct at start,
  - a no-arg walk of a STANDARD task (the previously-broken default) records
    design-review + review-gate so completion succeeds end-to-end,
  - a no-arg walk of a FULL task records spec-review + plan-review + review-gate,
  - explicit-stage advance still works (back-compat),
  - the walk reports "pipeline complete" past the last planned stage.
"""
import re

import taskmaster_v3 as tv


def _add_task(bs, title, lane):
    tid = re.search(
        r"[a-z0-9-]+-\d{3}",
        bs.backlog_add_task(title, epic="test-epic", phase="dev", priority="medium"),
    ).group(0)
    bs.backlog_update_task(tid, "lane", lane)
    return tid


# ── planned-sequence seeding ────────────────────────────────────────────────

def test_start_plans_standard_sequence(tm_epic_phase):
    import backlog_server as bs

    tid = _add_task(bs, "standard task", "standard")
    bs.backlog_auto_start("task", tid)
    state = bs._read_auto_state(bs._backlog_path())
    planned = state["cursor"]["planned_stages"]
    assert "DESIGN_REVIEW" in planned
    assert "SPEC_REVIEW" not in planned
    assert "PLAN_REVIEW" not in planned
    assert state["cursor"]["lane"] == "standard"


def test_start_plans_full_sequence(tm_epic_phase):
    import backlog_server as bs

    tid = _add_task(bs, "full task", "full")
    bs.backlog_auto_start("task", tid)
    state = bs._read_auto_state(bs._backlog_path())
    planned = state["cursor"]["planned_stages"]
    assert "SPEC_REVIEW" in planned
    assert "PLAN_REVIEW" in planned
    assert "DESIGN_REVIEW" not in planned


def test_start_plans_express_sequence(tm_epic_phase):
    import backlog_server as bs

    tid = _add_task(bs, "express task", "express")
    bs.backlog_auto_start("task", tid)
    state = bs._read_auto_state(bs._backlog_path())
    planned = state["cursor"]["planned_stages"]
    assert "SPEC_REVIEW" not in planned
    assert "DESIGN_REVIEW" not in planned
    assert "PLAN_REVIEW" not in planned
    assert "IMPLEMENT" in planned and "REVIEW_GATE" in planned


def test_laneless_task_falls_back_to_standard(tm_epic_phase):
    import backlog_server as bs

    # Task with no lane set at all.
    tid = re.search(
        r"[a-z0-9-]+-\d{3}",
        bs.backlog_add_task("laneless", epic="test-epic", phase="dev", priority="medium"),
    ).group(0)
    bs.backlog_auto_start("task", tid)
    state = bs._read_auto_state(bs._backlog_path())
    assert state["cursor"]["planned_stages"] == list(tv.auto_stages_for_lane("standard"))


# ── end-to-end completion via no-arg walk ───────────────────────────────────

def _walk_to_completion(bs):
    """Drive backlog_auto_advance() with no stage until the pipeline reports
    complete. Returns the ordered list of stages stepped through."""
    stepped = []
    for _ in range(50):  # generous guard against an infinite loop
        res = bs.backlog_auto_advance()
        if "Pipeline complete" in res:
            break
        m = re.search(r"Stage → (\w+)", res)
        assert m, f"unexpected advance result: {res}"
        stepped.append(m.group(1))
    else:  # pragma: no cover - loop guard
        raise AssertionError("walk did not terminate")
    return stepped


def test_standard_lane_end_to_end_completion(tm_epic_phase):
    """THE C2 regression test: a STANDARD-lane task (the previously-broken
    default) walks its planned sequence via no-arg advance, records
    design-review + review-gate, and completes."""
    import backlog_server as bs

    tid = _add_task(bs, "standard e2e", "standard")
    bs.backlog_auto_start("task", tid)
    # Task must be in-progress for completion; PICK stage doesn't move status,
    # so pick it explicitly the way the real recipe does.
    bs.backlog_pick_task(tid)

    stepped = _walk_to_completion(bs)
    # The standard pipeline (minus the terminal COMPLETE which has no gate
    # walking past it) was stepped through.
    assert "DESIGN_REVIEW" in stepped
    assert "SPEC_REVIEW" not in stepped

    task, _ = bs._find_task(bs._load(), tid)
    # Blocking gates recorded.
    assert task["gates"]["design-review"]["verdict"] == "pass"
    assert task["gates"]["review-gate"]["verdict"] == "pass"
    # Outstanding required gates is now empty → completion unblocked.
    assert tv.outstanding_required_gates(task) == []

    out = bs.backlog_complete_task(tid, target_status="done")
    assert "Error" not in out and "Cannot complete" not in out, out
    task, _ = bs._find_task(bs._load(), tid)
    assert task["status"] == "done"


def test_full_lane_end_to_end_completion(tm_epic_phase):
    import backlog_server as bs

    tid = _add_task(bs, "full e2e", "full")
    bs.backlog_auto_start("task", tid)
    bs.backlog_pick_task(tid)

    stepped = _walk_to_completion(bs)
    assert "SPEC_REVIEW" in stepped
    assert "PLAN_REVIEW" in stepped

    task, _ = bs._find_task(bs._load(), tid)
    assert task["gates"]["spec-review"]["verdict"] == "pass"
    assert task["gates"]["plan-review"]["verdict"] == "pass"
    assert task["gates"]["review-gate"]["verdict"] == "pass"
    assert tv.outstanding_required_gates(task) == []

    out = bs.backlog_complete_task(tid, target_status="done")
    assert "Error" not in out and "Cannot complete" not in out, out


# ── back-compat: explicit-stage advance still works ─────────────────────────

def test_explicit_stage_advance_back_compat(tm_epic_phase):
    import backlog_server as bs

    tid = _add_task(bs, "express explicit", "express")
    bs.backlog_auto_start("task", tid)
    bs.backlog_auto_advance("IMPLEMENT")
    bs.backlog_auto_advance("REVIEW_GATE")
    task, _ = bs._find_task(bs._load(), tid)
    assert task["gates"]["impl"]["status"] == "done"
    assert task["gates"]["review-gate"]["verdict"] == "pass"


def test_advance_past_end_reports_pipeline_complete(tm_epic_phase):
    import backlog_server as bs

    tid = _add_task(bs, "express complete msg", "express")
    bs.backlog_auto_start("task", tid)
    _walk_to_completion(bs)  # consumes through COMPLETE
    res = bs.backlog_auto_advance()
    assert "Pipeline complete" in res


# ── status surfaces the lane pipeline ───────────────────────────────────────

def test_status_surfaces_planned_pipeline(tm_epic_phase):
    import backlog_server as bs

    tid = _add_task(bs, "status surface", "standard")
    bs.backlog_auto_start("task", tid)
    out = bs.backlog_auto_status()
    assert "Pipeline:" in out
    assert "DESIGN_REVIEW" in out
    assert "Next:" in out
