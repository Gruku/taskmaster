# plugins/taskmaster/tests/test_foundation_smoke.py
"""End-to-end check: every _get/_list returns slim by default; verbose preserves
today's behavior. The canary that ensures Plan A delivers without regressions.
"""
from __future__ import annotations

import re


def test_slim_defaults_across_all_entities(tm_epic_phase):
    """All _get and _list tools must omit heavy body content in slim (default) mode."""
    from taskmaster.backlog_server import (
        backlog_add_task,
        backlog_get_task,
        backlog_list_tasks,
        backlog_status,
        backlog_issue_create,
        backlog_issue_get,
        backlog_issue_list,
        backlog_lesson_create,
        backlog_lesson_get,
        backlog_lesson_list,
        backlog_handover_create,
        backlog_handover_get,
        backlog_handover_list,
    )

    # ── Create one of each entity with heavy body content ──────────────────
    backlog_add_task(
        epic="test-epic", task_id="T-1", title="Auth",
        tldr="Refactor auth.",
        notes="Heavy notes content here.",
        phase="dev",
    )

    backlog_issue_create(
        title="Bug", severity="P1", tldr="Auth bug.",
        impact="fixture evidence.",
        body="## Repro\n\nLong steps.",
    )

    backlog_lesson_create(
        title="Atomic", kind="pattern", tldr="Use atomic.",
        body="## Why\n\nLong why.\n## What to do\n\nLong WTD.",
    )

    hnd_result = backlog_handover_create(
        tldr="Auth handoff.",
        next_action="Next.",
        body="## Decisions\n\nDetail.",
        task_ids=["T-1"],
    )
    # Extract handover id from "Handover written: <hid>\n..."
    hid_match = re.search(r"Handover written:\s*(\S+)", hnd_result)
    handover_id = hid_match.group(1) if hid_match else None

    # ── Slim _get hides body content ───────────────────────────────────────
    slim_task = backlog_get_task("T-1")
    assert "Heavy notes content" not in slim_task, (
        f"backlog_get_task leaked heavy content in slim mode:\n{slim_task}"
    )

    slim_issue = backlog_issue_get("ISS-001")
    assert "Long steps" not in slim_issue, (
        f"backlog_issue_get leaked heavy content in slim mode:\n{slim_issue}"
    )

    slim_lesson = backlog_lesson_get("L-001")
    assert "Long why" not in slim_lesson, (
        f"backlog_lesson_get leaked heavy content in slim mode:\n{slim_lesson}"
    )

    if handover_id:
        slim_handover = backlog_handover_get(handover_id)
        assert "Detail" not in slim_handover, (
            f"backlog_handover_get leaked heavy content in slim mode:\n{slim_handover}"
        )

    # ── Verbose _get reveals body content ──────────────────────────────────
    verbose_task = backlog_get_task("T-1", verbose=True)
    assert "Heavy notes content" in verbose_task, (
        f"backlog_get_task verbose missing notes content:\n{verbose_task}"
    )

    verbose_issue = backlog_issue_get("ISS-001", verbose=True)
    assert "Long steps" in verbose_issue, (
        f"backlog_issue_get verbose missing body content:\n{verbose_issue}"
    )

    verbose_lesson = backlog_lesson_get("L-001", verbose=True)
    assert "Long why" in verbose_lesson, (
        f"backlog_lesson_get verbose missing body content:\n{verbose_lesson}"
    )

    if handover_id:
        verbose_handover = backlog_handover_get(handover_id, verbose=True)
        assert "Detail" in verbose_handover, (
            f"backlog_handover_get verbose missing body content:\n{verbose_handover}"
        )

    # ── List tools return slim (no heavy body content) ─────────────────────
    list_tasks_out = backlog_list_tasks()
    assert "Heavy notes content" not in list_tasks_out, (
        f"backlog_list_tasks leaked heavy content:\n{list_tasks_out}"
    )

    list_issues_out = backlog_issue_list()
    assert "Long steps" not in list_issues_out, (
        f"backlog_issue_list leaked heavy content:\n{list_issues_out}"
    )

    list_lessons_out = backlog_lesson_list()
    assert "Long why" not in list_lessons_out, (
        f"backlog_lesson_list leaked heavy content:\n{list_lessons_out}"
    )

    list_handovers_out = backlog_handover_list()
    assert "Detail" not in list_handovers_out, (
        f"backlog_handover_list leaked heavy content:\n{list_handovers_out}"
    )

    # ── Dashboard slim ─────────────────────────────────────────────────────
    status_out = backlog_status()
    assert len(status_out) < 4000, (
        f"slim status too large ({len(status_out)} chars):\n{status_out[:500]}"
    )


def test_token_budget_targets_met(tm_epic_phase):
    """Confirm Plan A's slim mode produces the promised token reductions."""
    from taskmaster.backlog_server import backlog_add_task, backlog_get_task

    backlog_add_task(
        epic="test-epic", task_id="T-1", title="Auth refactor",
        tldr="Refactor middleware.",
        notes="A" * 2000,
        phase="dev",
    )

    slim = backlog_get_task("T-1")
    assert len(slim) < 800, (
        f"slim get_task too large: {len(slim)} chars\n{slim}"
    )

    verbose = backlog_get_task("T-1", verbose=True)
    assert len(verbose) > 2000, (
        f"verbose get_task missing content: {len(verbose)} chars"
    )
