# plugins/taskmaster/tests/test_tldr_required_on_create.py
"""Tasks 3-5: tldr required on create functions, with autogen fallback.

Tests read task state by loading the backlog YAML directly (path-i deviation)
because backlog_get_task does not have a verbose=True parameter yet (Task 11).
Issues and ideas are read from their respective .md files using
taskmaster_v3.parse_frontmatter (the project's own frontmatter parser, no
external python-frontmatter dependency).
"""
from __future__ import annotations

import yaml
import pytest
from pathlib import Path
from taskmaster.backlog_server import (
    backlog_add_task,
    backlog_add_epic,
    backlog_add_phase,
    backlog_update_task,
)


def _load_task(tmp_taskmaster, task_id: str) -> dict:
    """Read the task dict directly from backlog.yaml."""
    bp = tmp_taskmaster / ".taskmaster" / "backlog.yaml"
    data = yaml.safe_load(bp.read_text(encoding="utf-8"))
    for epic in data["epics"]:
        for t in epic.get("tasks", []):
            if t["id"] == task_id:
                return t
    raise KeyError(f"Task {task_id!r} not found in backlog.yaml")


def test_add_task_with_tldr_succeeds(tm_epic_phase):
    result = backlog_add_task(
        epic="test-epic",
        task_id="T-tldr-1",
        title="Test task",
        tldr="One-line essence of the task.",
        phase="dev",
    )
    assert "T-tldr-1" in result
    t = _load_task(tm_epic_phase, "T-tldr-1")
    assert t["tldr"] == "One-line essence of the task."
    assert "tldr_autogen" not in t


def test_add_task_without_tldr_autogenerates_from_body(tm_epic_phase):
    result = backlog_add_task(
        epic="test-epic",
        task_id="T-tldr-2",
        title="Test task",
        notes="Fix the auth middleware. It breaks on Friday deploys.",
        phase="dev",
    )
    assert "T-tldr-2" in result
    t = _load_task(tm_epic_phase, "T-tldr-2")
    assert "Fix the auth middleware" in t["tldr"]
    assert t.get("tldr_autogen") is True


def test_add_task_without_tldr_or_body_uses_title(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="T-tldr-3", title="Refactor auth", phase="dev")
    t = _load_task(tm_epic_phase, "T-tldr-3")
    assert "Refactor auth" in t["tldr"]
    assert t.get("tldr_autogen") is True


def test_add_task_with_next_step_persists(tm_epic_phase):
    backlog_add_task(
        epic="test-epic",
        task_id="T-tldr-4",
        title="Auth refactor",
        tldr="Refactor auth.",
        next_step="Write failing test first.",
        phase="dev",
    )
    t = _load_task(tm_epic_phase, "T-tldr-4")
    assert "Write failing test first" in t["next_step"]


def test_update_task_next_step(tm_epic_phase):
    backlog_add_task(
        epic="test-epic",
        task_id="T-tldr-5",
        title="Auth",
        tldr="Auth tldr.",
        phase="dev",
    )
    backlog_update_task("T-tldr-5", next_step="Now do Y.")
    t = _load_task(tm_epic_phase, "T-tldr-5")
    assert "Now do Y" in t["next_step"]


def test_update_task_rejects_mixed_styles(tm_epic_phase):
    """Mixing kwarg style with field/value style is an error, not a silent drop."""
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
    t = _load_task(tm_epic_phase, "T-tldr-6")
    assert t["title"] == "Mixed"
    assert t["tldr"] == "Mixed tldr."


def test_update_task_rejects_empty_tldr(tm_epic_phase):
    """tldr cannot be cleared via the classic field/value API — it is required."""
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
    t = _load_task(tm_epic_phase, "T-tldr-7")
    assert t["tldr"] == "Original tldr."


# ── Task 4: backlog_issue_create ──────────────────────────────────────────────


def _load_issue(tmp_taskmaster, issue_id):
    """Read an issue's frontmatter from .taskmaster/issues/<id>.md."""
    from taskmaster.taskmaster_v3 import parse_frontmatter
    issue_path = Path(tmp_taskmaster) / ".taskmaster" / "issues" / f"{issue_id}.md"
    if not issue_path.exists():
        raise AssertionError(f"Issue file not found: {issue_path}")
    fm, _ = parse_frontmatter(issue_path.read_text(encoding="utf-8"))
    return fm


from taskmaster.backlog_server import backlog_issue_create


def test_issue_create_with_tldr(tmp_taskmaster):
    backlog_issue_create(
        title="Auth fails on Friday",
        severity="P1",
        tldr="Auth middleware crashes during Friday deploys.",
        impact="3 customers blocked",
    )
    issue = _load_issue(tmp_taskmaster, "ISS-001")
    assert "Auth middleware crashes" in issue["tldr"]


def test_issue_create_autogen_tldr_from_impact(tmp_taskmaster):
    backlog_issue_create(
        title="Auth fails",
        severity="P1",
        impact="Auth middleware crashes during Friday deploys.",
    )
    issue = _load_issue(tmp_taskmaster, "ISS-001")
    assert "Auth middleware crashes" in issue["tldr"]
    assert issue.get("tldr_autogen") is True


# ── Task 5: backlog_idea_create ───────────────────────────────────────────────


from taskmaster.backlog_server import backlog_idea_create


def test_idea_create_with_tldr(tmp_taskmaster):
    result = backlog_idea_create(
        title="Add dark mode",
        tldr="Toggle dark mode in viewer settings.",
    )
    assert "IDEA-" in result
