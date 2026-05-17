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


# tmp_taskmaster is taken for chaining; actual writes go through monkeypatched ROOT in conftest
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


def test_update_task_rejects_mixed_styles(tmp_taskmaster):
    """Mixing kwarg style with field/value style is an error, not a silent drop."""
    _setup(tmp_taskmaster)
    backlog_add_task(
        epic="test-epic",
        task_id="T-tldr-6",
        title="Mixed",
        tldr="Mixed tldr.",
        phase="dev",
    )
    result = backlog_update_task(
        "T-tldr-6", field="title", value="Renamed", tldr="New tldr"
    )
    assert "Error" in result
    assert "not both" in result
    # Verify nothing changed
    t = _load_task(tmp_taskmaster, "T-tldr-6")
    assert t["title"] == "Mixed"
    assert t["tldr"] == "Mixed tldr."


def test_update_task_rejects_empty_tldr(tmp_taskmaster):
    """tldr cannot be cleared via the classic field/value API — it is required."""
    _setup(tmp_taskmaster)
    backlog_add_task(
        epic="test-epic",
        task_id="T-tldr-7",
        title="Empty tldr test",
        tldr="Original tldr.",
        phase="dev",
    )
    result = backlog_update_task("T-tldr-7", field="tldr", value="")
    assert "Error" in result
    assert "tldr cannot be cleared" in result
    # Verify the original tldr is intact
    t = _load_task(tmp_taskmaster, "T-tldr-7")
    assert t["tldr"] == "Original tldr."
