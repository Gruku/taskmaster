import json
from taskmaster.backlog_server import (backlog_add_epic, backlog_add_task, backlog_update_task,
                            backlog_update_epic, _load)


def test_design_change_blocked_when_locked(tm_epic_phase):
    backlog_update_epic("test-epic", "design_status", "locked")
    backlog_add_task(epic="test-epic", task_id="D-1", title="redesign cache", phase="dev")
    out = backlog_update_task("D-1", "design_change", "true")
    assert "Error" in out and "locked" in out.lower()
    assert "revising" in out.lower()                      # tells user how to reopen
    t = next(t for e in _load()["epics"] for t in e.get("tasks", []) if t["id"] == "D-1")
    assert "design_change" not in t                       # not set


def test_design_change_allowed_when_revising(tm_epic_phase):
    backlog_update_epic("test-epic", "design_status", "revising")
    backlog_add_task(epic="test-epic", task_id="D-2", title="redesign", phase="dev")
    assert "Error" not in backlog_update_task("D-2", "design_change", "true")
    t = next(t for e in _load()["epics"] for t in e.get("tasks", []) if t["id"] == "D-2")
    assert t["design_change"] is True


def test_design_change_clear(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="D-3", title="x", phase="dev")
    backlog_update_task("D-3", "design_change", "true")
    backlog_update_task("D-3", "design_change", "false")
    t = next(t for e in _load()["epics"] for t in e.get("tasks", []) if t["id"] == "D-3")
    assert "design_change" not in t


def test_design_change_allowed_when_no_design_status(tm_epic_phase):
    # epic has no design_status declared -> defaults to "exploring" -> accepted
    backlog_add_task(epic="test-epic", task_id="D-4", title="x", phase="dev")
    assert "Error" not in backlog_update_task("D-4", "design_change", "true")
    t = next(t for e in _load()["epics"] for t in e.get("tasks", []) if t["id"] == "D-4")
    assert t["design_change"] is True


def test_design_change_allowed_when_proposed(tm_epic_phase):
    backlog_update_epic("test-epic", "design_status", "proposed")
    backlog_add_task(epic="test-epic", task_id="D-5", title="x", phase="dev")
    assert "Error" not in backlog_update_task("D-5", "design_change", "true")
    t = next(t for e in _load()["epics"] for t in e.get("tasks", []) if t["id"] == "D-5")
    assert t["design_change"] is True
