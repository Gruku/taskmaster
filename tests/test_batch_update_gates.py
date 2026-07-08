"""
Batch update parity gaps: I1 (completion gate) and I2 (lane validation + gate_state recompute).

I1: `complete {id}` and `status {id} done` must apply _completion_block_reason so a
    lane'd task with outstanding review gates cannot be forced to done via batch.
I2: `update {id} lane {value}` must validate against _VALID_LANES and recompute
    gate_state when the lane is valid (the generic else-fallthrough previously did neither).
"""
import re

import pytest

from taskmaster import backlog_server as _bs


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_task(*, lane="express", status="in-progress"):
    """Create a task in test-epic/dev, optionally set lane + status, return task id."""
    out = _bs.backlog_add_task(
        "batch-gate-task", epic="test-epic", phase="dev", priority="medium"
    )
    match = re.search(r"[a-z0-9-]+-\d{3}", out)
    assert match, f"Could not parse task id from: {out!r}"
    tid = match.group(0)

    if lane:
        r = _bs.backlog_update_task(tid, "lane", lane)
        assert "Error" not in r, f"lane set failed: {r!r}"

    if status and status != "todo":
        r = _bs.backlog_update_task(tid, "status", status)
        assert "Error" not in r, f"status set failed: {r!r}"

    return tid


# ── I1: completion gate applied via `complete` op ────────────────────────────

def test_batch_complete_blocked_by_outstanding_gate(tm_epic_phase):
    """complete op on a lane'd task with no review-gate must be rejected."""
    tid = _make_task(lane="express", status="in-progress")
    # express lane requires review-gate; no gate recorded yet → should be blocked

    out = _bs.backlog_batch_update(f"complete {tid}")

    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["status"] != "done", (
        f"task was wrongly completed despite outstanding gate; status={task['status']!r}"
    )
    assert "error" in out.lower() or "cannot complete" in out.lower(), (
        f"Expected gate-block error in batch output, got: {out!r}"
    )


def test_batch_complete_allowed_after_gate(tm_epic_phase):
    """complete op succeeds once the required review-gate is satisfied."""
    tid = _make_task(lane="express", status="in-progress")
    _bs.backlog_record_gate(tid, "review-gate", verdict="pass")

    out = _bs.backlog_batch_update(f"complete {tid}")

    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["status"] == "done", (
        f"Expected done after gate satisfied, got {task['status']!r}; batch output: {out!r}"
    )
    assert "error" not in out.lower(), f"Unexpected error: {out!r}"


def test_batch_laneless_complete_unaffected(tm_epic_phase):
    """Laneless tasks are exempt from the completion gate; complete op must succeed."""
    tid = _make_task(lane="express", status="in-progress")
    # Remove the lane so the task is laneless (exempt from gate checks)
    data = _bs._load()
    task, _ = _bs._find_task(data, tid)
    task.pop("lane", None)
    task.pop("gate_state", None)
    _bs._mutate_and_save(data)

    out = _bs.backlog_batch_update(f"complete {tid}")

    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["status"] == "done", (
        f"Laneless task should complete freely, got {task['status']!r}; output: {out!r}"
    )
    assert "error" not in out.lower(), f"Unexpected error: {out!r}"


# ── I1: completion gate applied via `status {id} done` op ────────────────────

def test_batch_status_done_blocked_by_outstanding_gate(tm_epic_phase):
    """status <id> done on a lane'd task with outstanding gate must be rejected."""
    tid = _make_task(lane="express", status="in-progress")

    out = _bs.backlog_batch_update(f"status {tid} done")

    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["status"] != "done", (
        f"task was wrongly set to done despite outstanding gate; status={task['status']!r}"
    )
    assert "error" in out.lower() or "cannot complete" in out.lower(), (
        f"Expected gate-block error, got: {out!r}"
    )


def test_batch_status_done_allowed_after_gate(tm_epic_phase):
    """status <id> done succeeds once the required review-gate is satisfied."""
    tid = _make_task(lane="express", status="in-progress")
    _bs.backlog_record_gate(tid, "review-gate", verdict="pass")

    out = _bs.backlog_batch_update(f"status {tid} done")

    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["status"] == "done", (
        f"Expected done after gate satisfied, got {task['status']!r}; batch output: {out!r}"
    )
    assert "error" not in out.lower(), f"Unexpected error: {out!r}"


# ── I2: lane validation + gate_state recompute ───────────────────────────────

def test_batch_update_lane_rejects_invalid_lane(tm_epic_phase):
    """update {id} lane {bogus} must be rejected; original lane must be unchanged."""
    tid = _make_task(lane="express", status="in-progress")

    out = _bs.backlog_batch_update(f"update {tid} lane turbo")

    task, _ = _bs._find_task(_bs._load(), tid)
    assert task.get("lane") == "express", (
        f"Lane was wrongly changed to invalid value; task lane={task.get('lane')!r}"
    )
    assert "error" in out.lower() or "invalid lane" in out.lower(), (
        f"Expected invalid-lane error, got: {out!r}"
    )


def test_batch_update_lane_valid_sets_and_recomputes_gate_state(tm_epic_phase):
    """update {id} lane full must write the new lane and recompute gate_state."""
    tid = _make_task(lane="express", status="in-progress")

    out = _bs.backlog_batch_update(f"update {tid} lane full")

    task, _ = _bs._find_task(_bs._load(), tid)
    assert task.get("lane") == "full", (
        f"Expected lane=full after update, got {task.get('lane')!r}; output: {out!r}"
    )
    # full lane: first blocking gate is spec-review → gate_state = "spec-review:pending"
    assert task.get("gate_state") == "spec-review:pending", (
        f"gate_state not recomputed after lane change; got {task.get('gate_state')!r}"
    )
    assert "error" not in out.lower(), f"Unexpected error: {out!r}"
