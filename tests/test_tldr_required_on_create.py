# plugins/taskmaster/tests/test_tldr_required_on_create.py
"""Task 3: tldr required on backlog_add_task / backlog_update_task, with autogen fallback.

Tests read task state by loading the backlog YAML directly (path-i deviation)
because backlog_get_task does not have a verbose=True parameter yet (Task 11).
"""
from __future__ import annotations

import yaml
import pytest
from backlog_server import (
    backlog_add_task,
    backlog_add_epic,
    backlog_add_phase,
    backlog_update_task,
)


def _setup(tmp_taskmaster):
    """Create a minimal epic + phase so backlog_add_task can proceed."""
    backlog_add_epic(epic_id="test-epic", name="Test Epic")
    backlog_add_phase(phase_id="dev", name="Development")
    return tmp_taskmaster


def _load_task(tmp_taskmaster, task_id: str) -> dict:
    """Read the task dict directly from backlog.yaml."""
    bp = tmp_taskmaster / ".taskmaster" / "backlog.yaml"
    data = yaml.safe_load(bp.read_text(encoding="utf-8"))
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t["id"] == task_id:
                return t
    raise KeyError(f"Task {task_id!r} not found in backlog.yaml")


def test_add_task_with_tldr_succeeds(tmp_taskmaster):
    _setup(tmp_taskmaster)
    result = backlog_add_task(
        epic="test-epic",
        task_id="T-tldr-1",
        title="Test task",
        tldr="One-line essence of the task.",
        phase="dev",
    )
    assert "T-tldr-1" in result
    t = _load_task(tmp_taskmaster, "T-tldr-1")
    assert t["tldr"] == "One-line essence of the task."
    assert "tldr_autogen" not in t


def test_add_task_without_tldr_autogenerates_from_body(tmp_taskmaster):
    _setup(tmp_taskmaster)
    result = backlog_add_task(
        epic="test-epic",
        task_id="T-tldr-2",
        title="Test task",
        notes="Fix the auth middleware. It breaks on Friday deploys.",
        phase="dev",
    )
    assert "T-tldr-2" in result
    t = _load_task(tmp_taskmaster, "T-tldr-2")
    assert "Fix the auth middleware" in t["tldr"]
    assert t.get("tldr_autogen") is True


def test_add_task_without_tldr_or_body_uses_title(tmp_taskmaster):
    _setup(tmp_taskmaster)
    backlog_add_task(epic="test-epic", task_id="T-tldr-3", title="Refactor auth", phase="dev")
    t = _load_task(tmp_taskmaster, "T-tldr-3")
    assert "Refactor auth" in t["tldr"]
    assert t.get("tldr_autogen") is True


def test_add_task_with_next_step_persists(tmp_taskmaster):
    _setup(tmp_taskmaster)
    backlog_add_task(
        epic="test-epic",
        task_id="T-tldr-4",
        title="Auth refactor",
        tldr="Refactor auth.",
        next_step="Write failing test first.",
        phase="dev",
    )
    t = _load_task(tmp_taskmaster, "T-tldr-4")
    assert "Write failing test first" in t["next_step"]


def test_update_task_next_step(tmp_taskmaster):
    _setup(tmp_taskmaster)
    backlog_add_task(
        epic="test-epic",
        task_id="T-tldr-5",
        title="Auth",
        tldr="Auth tldr.",
        phase="dev",
    )
    backlog_update_task("T-tldr-5", next_step="Now do Y.")
    t = _load_task(tmp_taskmaster, "T-tldr-5")
    assert "Now do Y" in t["next_step"]
