"""Lifecycle guards on batch completion (B-049) and pick-task dependency warnings (B-050)."""
from pathlib import Path

import pytest

from tests.test_server_api import running_server  # noqa: F401


def _ensure_scaffold(tmp_path: Path) -> None:
    progress = tmp_path / ".taskmaster" / "PROGRESS.md"
    if not progress.exists():
        progress.write_text("## Changelog\n", encoding="utf-8")
    from taskmaster import backlog_server as _bs
    r_epic = _bs.backlog_add_epic(epic_id="test-epic", name="Test Epic", done_when="all test tasks complete")
    if "Error" in r_epic and "already" not in r_epic.lower():
        raise AssertionError(f"add_epic failed: {r_epic}")
    r_phase = _bs.backlog_add_phase(phase_id="dev", name="Development")
    if "Error" in r_phase and "already" not in r_phase.lower():
        raise AssertionError(f"add_phase failed: {r_phase}")


def _add_task(task_id: str, depends_on: str = "") -> None:
    from taskmaster import backlog_server as _bs
    r = _bs.backlog_add_task(
        title=f"Task {task_id}", epic="test-epic", phase="dev",
        priority="medium", task_id=task_id, depends_on=depends_on,
    )
    assert "Error" not in r, f"add_task {task_id} failed: {r}"


def _satisfy_gates(task_id: str) -> None:
    """Skip every required gate for a task's lane (audited skip is always allowed)."""
    from taskmaster import backlog_server as _bs
    from taskmaster.taskmaster_v3 import required_gates as _required_gates
    task, _ = _bs._find_task(_bs._load(), task_id)
    lane = task.get("lane")
    if not lane:
        return
    for gate in _required_gates(lane):
        _bs.backlog_skip_gate(task_id, gate, "test setup — isolating dependency warning")


# ── B-049: batch completion honors the lifecycle guard + backfills started ──

def test_batch_complete_rejects_todo_task(running_server, tmp_path):
    """A `complete` op on a todo task must be rejected, not silently set done."""
    _ensure_scaffold(tmp_path)
    _add_task("guard-todo-001")  # left in todo (never picked)

    from taskmaster import backlog_server as _bs
    out = _bs.backlog_batch_update("complete guard-todo-001")

    assert "error" in out.lower(), f"Expected guard error, got: {out!r}"
    assert "cannot complete from `todo`" in out, f"Expected todo guard message, got: {out!r}"

    task, _ = _bs._find_task(_bs._load(), "guard-todo-001")
    assert task.get("status") != "done", f"todo task wrongly completed: {task.get('status')!r}"


def test_batch_complete_backfills_started_for_valid_task(running_server, tmp_path):
    """A `complete` op on an in-progress task succeeds and backfills `started`."""
    _ensure_scaffold(tmp_path)
    _add_task("guard-ip-001")

    from taskmaster import backlog_server as _bs
    _bs.backlog_pick_task("guard-ip-001")  # -> in-progress, sets started
    # Satisfy required review gates so the Spec-A completion gate passes (I1).
    _satisfy_gates("guard-ip-001")

    out = _bs.backlog_batch_update("complete guard-ip-001")
    assert "error" not in out.lower(), f"Unexpected error: {out!r}"

    task, _ = _bs._find_task(_bs._load(), "guard-ip-001")
    assert task.get("status") == "done"
    assert task.get("started"), "started must be set after completion"
    assert task.get("completed"), "completed must be set"


def test_batch_status_done_rejects_todo_task(running_server, tmp_path):
    """The `status <id> done` shorthand must honor the same guard as `complete`."""
    _ensure_scaffold(tmp_path)
    _add_task("guard-todo-002")

    from taskmaster import backlog_server as _bs
    out = _bs.backlog_batch_update("status guard-todo-002 done")

    assert "cannot complete from `todo`" in out, f"Expected guard message, got: {out!r}"
    task, _ = _bs._find_task(_bs._load(), "guard-todo-002")
    assert task.get("status") != "done"


# ── B-050: pick_task warns on unmet dependencies (warn, not block) ──

def test_pick_warns_on_unmet_dependency(running_server, tmp_path):
    """Picking a task whose dependency is not done picks it (override) but warns."""
    _ensure_scaffold(tmp_path)
    _add_task("dep-001")  # stays todo
    _add_task("needs-001", depends_on="dep-001")

    from taskmaster import backlog_server as _bs
    out = _bs.backlog_pick_task("needs-001")

    assert "Unmet dependencies" in out, f"Expected dependency warning, got: {out!r}"
    assert "dep-001" in out, f"Warning should name the unmet dep, got: {out!r}"

    # Still picked — pick is an explicit override.
    task, _ = _bs._find_task(_bs._load(), "needs-001")
    assert task.get("status") == "in-progress", f"task should be picked, got {task.get('status')!r}"


def test_pick_no_warning_when_dependency_done(running_server, tmp_path):
    """No spurious warning when the dependency is already done."""
    _ensure_scaffold(tmp_path)
    _add_task("dep-002")
    _add_task("needs-002", depends_on="dep-002")

    from taskmaster import backlog_server as _bs
    _bs.backlog_pick_task("dep-002")
    # Spec A gate guard: a lane'd task can't reach `done` until its required
    # gates are satisfied. This test cares about the dependency warning, not
    # the gate pipeline, so skip every required gate for dep-002's lane.
    _satisfy_gates("dep-002")
    _bs.backlog_complete_task(task_id="dep-002")  # dep -> done

    out = _bs.backlog_pick_task("needs-002")
    assert "Unmet dependencies" not in out, f"Should not warn when dep is done, got: {out!r}"


# ── B-048: batch archive must write `archive_reason`, not `archived_reason` ──

def test_batch_archive_writes_canonical_archive_reason_key(running_server, tmp_path):
    """batch `archive <id> <reason>` must persist `archive_reason`, not `archived_reason`.

    `archived_reason` is the wrong key; every other archive path and the viewer
    use `archive_reason`.  A misspelled key means the reason chip never renders
    and epic stats miscount the entry.
    """
    _ensure_scaffold(tmp_path)
    _add_task("arch-001")

    from taskmaster import backlog_server as _bs
    out = _bs.backlog_batch_update("archive arch-001 deprecated")
    assert "error" not in out.lower(), f"Unexpected error from batch archive: {out!r}"

    task, _ = _bs._find_task(_bs._load(), "arch-001")
    assert task.get("status") == "archived", f"Expected archived status, got {task.get('status')!r}"
    assert task.get("archive_reason") == "deprecated", (
        f"Expected archive_reason='deprecated', got task={task!r}"
    )
    assert "archived_reason" not in task, (
        f"Stale key `archived_reason` must not be present, got task={task!r}"
    )
