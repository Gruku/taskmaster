# plugins/taskmaster/tests/test_slim_list_tools.py
"""Tasks 17-19: slim list views for backlog_list_tasks, backlog_handover_list,
and backlog_issue_list."""
from __future__ import annotations

from taskmaster.backlog_server import (
    backlog_add_task,
    backlog_handover_create,
    backlog_handover_list,
    backlog_issue_create,
    backlog_issue_list,
    backlog_list_tasks,
)

# ── Task 17: backlog_list_tasks slim ─────────────────────────────────────────


def test_list_tasks_returns_slim_entries(tm_epic_phase):
    backlog_add_task(epic="test-epic", title="X", tldr="Tldr 1.", notes="Heavy notes content.", phase="dev", options={"task_id": "T-1"})
    backlog_add_task(epic="test-epic", title="Y", tldr="Tldr 2.", phase="dev", options={"task_id": "T-2"})
    out = backlog_list_tasks()
    assert "Tldr 1" in out and "Tldr 2" in out
    assert "Heavy notes content" not in out


def test_list_tasks_verbose_includes_heavy(tm_epic_phase):
    backlog_add_task(epic="test-epic", title="X", tldr="T.", notes="Heavy notes content.", phase="dev", options={"task_id": "T-1"})
    out = backlog_list_tasks(verbose=True)
    assert "Heavy notes content" in out


# ── Task 18: backlog_handover_list slim ──────────────────────────────────────


def test_handover_list_returns_slim_entries(tmp_taskmaster):
    backlog_handover_create(
        tldr="Tldr handover.",
        next_action="Next.",
        body="## Decisions\n\nLong body.",
        task_ids=["T-1"],
    )
    out = backlog_handover_list()
    assert "Tldr handover" in out
    assert "Long body" not in out


# ── Task 19: backlog_issue_list slim ─────────────────────────────────────────


def test_issue_list_slim(tmp_taskmaster):
    backlog_issue_create(
        title="Bug",
        severity="P1",
        tldr="Issue tldr.",
        impact="fixture evidence.",
        body="## Repro\n\nLong repro steps.",
    )
    out = backlog_issue_list()
    assert "Issue tldr" in out
    assert "Long repro steps" not in out
