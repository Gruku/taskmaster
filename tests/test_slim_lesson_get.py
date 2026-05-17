# plugins/taskmaster/tests/test_slim_lesson_get.py
"""Task 14: backlog_lesson_get slim default + verbose/sections/expand_links."""
from __future__ import annotations

from backlog_server import backlog_lesson_create, backlog_lesson_get


def test_slim_default_excludes_body(tmp_taskmaster):
    backlog_lesson_create(
        title="Use atomic writes",
        kind="pattern",
        tldr="Call atomic_write() for every file mutation.",
        body="## Why\n\nPrevents partial writes on crash.\n\n## What to do\n\nUse atomic_write().",
    )
    out = backlog_lesson_get("L-001")
    assert "atomic_write()" in out  # tldr is in slim
    assert "Prevents partial writes" not in out  # body not in slim


def test_verbose_includes_body(tmp_taskmaster):
    backlog_lesson_create(
        title="Atomic writes",
        kind="pattern",
        tldr="Use atomic_write().",
        body="## Why\n\nVerbose body content here.",
    )
    out = backlog_lesson_get("L-001", verbose=True)
    assert "Verbose body content here" in out
    assert "---" in out


def test_slim_has_title_kind_tier(tmp_taskmaster):
    backlog_lesson_create(
        title="Always read before edit",
        kind="gotcha",
        tldr="Read file before editing it.",
    )
    out = backlog_lesson_get("L-001")
    assert "Always read before edit" in out
    assert "gotcha" in out


def test_sections_what_to_do(tmp_taskmaster):
    backlog_lesson_create(
        title="Atomic writes",
        kind="pattern",
        tldr="Use atomic_write().",
        body="## Why\n\nAvoid partial writes.\n\n## What to do\n\nCall atomic_write() for mutations.",
    )
    out = backlog_lesson_get("L-001", sections=["what_to_do"])
    assert "atomic_write() for mutations" in out
    assert "Avoid partial writes" not in out


def test_not_found_returns_message(tmp_taskmaster):
    out = backlog_lesson_get("L-999")
    assert "not found" in out.lower() or "Lesson not found" in out
