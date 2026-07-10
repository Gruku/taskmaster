import json
from taskmaster.backlog_server import (backlog_add_epic, backlog_add_task, backlog_update_task,
                            backlog_update_epic, backlog_epic_status, backlog_archive_task,
                            _load, _find_task, _mutate_and_save)


def _set_status(task_id, status):
    """Set status via the data layer — bypasses the Spec A transition table /
    done-gate for lane'd tasks where the test only cares about the final state."""
    if status in ("in-progress", "todo"):
        backlog_update_task(task_id, "status", status)
        return
    data = _load()
    task, _ = _find_task(data, task_id)
    task["status"] = status
    _mutate_and_save(data)


def _setup(tm_epic_phase):
    backlog_update_epic("test-epic", "components",
                        json.dumps({"core": {"title": "Core"}}))
    for tid, status in [("E-1", "done"), ("E-2", "in-progress"), ("E-3", "todo")]:
        backlog_add_task(epic="test-epic", title=tid, phase="dev", options={"task_id": tid})
        backlog_update_task(tid, "component", "core")
        _set_status(tid, status)

def test_epic_status_shows_counts_and_components(tm_epic_phase):
    _setup(tm_epic_phase)
    out = backlog_epic_status("test-epic")
    assert "test-epic" in out
    assert "1/3" in out                      # done/total progress
    assert "Core" in out or "core" in out    # component line
    assert "Components" in out

def test_epic_status_unknown(tm_epic_phase):
    out = backlog_epic_status("ghost")
    assert "Error" in out and "ghost" in out

def test_epic_status_attention_list(tm_epic_phase):
    backlog_add_task(epic="test-epic", title="blocked one", phase="dev", options={"task_id": "A-1"})
    backlog_update_task("A-1", "status", "blocked")
    backlog_update_task("A-1", "blockers", "waiting on CDN creds")
    out = backlog_epic_status("test-epic")
    assert "Attention" in out
    assert "A-1" in out and "CDN creds" in out

def test_epic_status_no_attention_when_clean(tm_epic_phase):
    backlog_add_task(epic="test-epic", title="fine", phase="dev", options={"task_id": "C-1"})
    out = backlog_epic_status("test-epic")
    assert "Attention" not in out

def test_epic_status_shows_unassigned(tm_epic_phase):
    backlog_add_task(epic="test-epic", title="loose", phase="dev", options={"task_id": "U-1"})
    out = backlog_epic_status("test-epic")
    assert "unassigned" in out

def test_epic_status_counts_archived(tm_epic_phase):
    # Two tasks: one done, one archived. Archiving keeps the task in
    # epic["tasks"] with status "archived" (verified: backlog_archive_task
    # mutates status in place, does not move the task out of the list).
    backlog_add_task(epic="test-epic", title="kept", phase="dev", options={"task_id": "K-1"})
    _set_status("K-1", "done")
    backlog_add_task(epic="test-epic", title="closed", phase="dev", options={"task_id": "K-2"})
    _set_status("K-2", "done")
    backlog_archive_task("K-2", reason="done")
    out = backlog_epic_status("test-epic")
    # Breakdown surfaces archived count.
    assert "Archived: 1" in out
    # Progress numerator counts done + archived against total (2/2).
    assert "2/2" in out


def test_epic_status_shows_done_when(tm_epic_phase):
    out = backlog_epic_status("test-epic")
    assert "Done when: all test tasks complete" in out


def test_epic_status_closeable_when_all_done(tm_epic_phase):
    backlog_add_task(epic="test-epic", title="one", phase="dev", options={"task_id": "D-1"})
    _set_status("D-1", "done")
    out = backlog_epic_status("test-epic")
    assert "CLOSEABLE" in out
    assert "archive via backlog_archive_epic" in out


def test_epic_status_not_closeable_with_open_tasks(tm_epic_phase):
    backlog_add_task(epic="test-epic", title="one", phase="dev", options={"task_id": "O-1"})
    _set_status("O-1", "done")
    backlog_add_task(epic="test-epic", title="two", phase="dev", options={"task_id": "O-2"})
    # O-2 stays todo
    out = backlog_epic_status("test-epic")
    assert "CLOSEABLE" not in out


def test_epic_status_zero_tasks_not_closeable(tm_epic_phase):
    out = backlog_epic_status("test-epic")
    assert "CLOSEABLE" not in out


def test_epic_status_closeable_counts_archived_as_done(tm_epic_phase):
    # Mirrors test_epic_status_counts_archived's math: done + archived == total.
    backlog_add_task(epic="test-epic", title="kept", phase="dev", options={"task_id": "AC-1"})
    _set_status("AC-1", "done")
    backlog_add_task(epic="test-epic", title="closed", phase="dev", options={"task_id": "AC-2"})
    _set_status("AC-2", "done")
    backlog_archive_task("AC-2", reason="done")
    out = backlog_epic_status("test-epic")
    assert "CLOSEABLE" in out
