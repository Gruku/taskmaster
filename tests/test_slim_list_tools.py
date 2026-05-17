# plugins/taskmaster/tests/test_slim_list_tools.py
"""Tasks 17-20: slim list views for backlog_list_tasks, backlog_handover_list,
backlog_issue_list, backlog_lesson_list, and backlog_lesson_match."""
from __future__ import annotations

from backlog_server import (
    backlog_add_task,
    backlog_handover_create,
    backlog_handover_list,
    backlog_issue_create,
    backlog_issue_list,
    backlog_lesson_create,
    backlog_lesson_list,
    backlog_lesson_match,
    backlog_list_tasks,
)

# ── Task 17: backlog_list_tasks slim ─────────────────────────────────────────


def test_list_tasks_returns_slim_entries(tm_epic_phase):
    backlog_add_task(
        epic="test-epic",
        task_id="T-1",
        title="X",
        tldr="Tldr 1.",
        notes="Heavy notes content.",
        phase="dev",
    )
    backlog_add_task(
        epic="test-epic",
        task_id="T-2",
        title="Y",
        tldr="Tldr 2.",
        phase="dev",
    )
    out = backlog_list_tasks()
    assert "Tldr 1" in out and "Tldr 2" in out
    assert "Heavy notes content" not in out


def test_list_tasks_verbose_includes_heavy(tm_epic_phase):
    backlog_add_task(
        epic="test-epic",
        task_id="T-1",
        title="X",
        tldr="T.",
        notes="Heavy notes content.",
        phase="dev",
    )
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
        body="## Repro\n\nLong repro steps.",
    )
    out = backlog_issue_list()
    assert "Issue tldr" in out
    assert "Long repro steps" not in out


# ── Task 19: backlog_lesson_list slim ────────────────────────────────────────


def test_lesson_list_slim(tmp_taskmaster):
    backlog_lesson_create(
        title="Atomic",
        kind="pattern",
        tldr="Lesson tldr.",
        body="## Why\n\nLong why body.\n## What to do\n\nLong WTD body.",
    )
    out = backlog_lesson_list()
    assert "Lesson tldr" in out
    assert "Long why body" not in out


# ── Task 20: backlog_lesson_match slim ──────────────────────────────────────


def test_lesson_match_slim_returns_id_tldr_pills(tmp_taskmaster):
    backlog_lesson_create(
        title="Atomic writes",
        kind="pattern",
        tldr="Use atomic_write() everywhere.",
        body="## Why\n\nPrevents corruption.\n## What to do\n\nCall atomic_write() instead of open().",
        task_titles_match=["atomic writes"],
    )
    out = backlog_lesson_match(task_title="atomic writes")
    assert "L-001" in out
    assert "Use atomic_write()" in out
    assert "Prevents corruption" not in out
    assert "reinforce_count" not in out.lower()


def test_lesson_match_verbose_returns_full_summary(tmp_taskmaster):
    backlog_lesson_create(
        title="Atomic writes",
        kind="pattern",
        tldr="Use atomic_write() everywhere.",
        body="Why text and WTD text.",
        task_titles_match=["atomic writes"],
    )
    out = backlog_lesson_match(task_title="atomic writes", verbose=True)
    assert "pattern" in out
    assert "L-001" in out
