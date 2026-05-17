# plugins/taskmaster/tests/test_slim_idea_get.py
"""Task 15: backlog_idea_get slim default + verbose."""
from __future__ import annotations

from backlog_server import backlog_idea_create, backlog_idea_get


def test_slim_default_excludes_body(tmp_taskmaster):
    backlog_idea_create(
        title="Add dark mode",
        tldr="Toggle dark mode in viewer settings.",
        body="## Details\n\nVery detailed body content that should be hidden.",
    )
    out = backlog_idea_get("IDEA-001")
    assert "Toggle dark mode" in out
    assert "Very detailed body content" not in out


def test_verbose_includes_body(tmp_taskmaster):
    backlog_idea_create(
        title="Add dark mode",
        tldr="Toggle dark mode.",
        body="## Details\n\nVerbose idea body content here.",
    )
    out = backlog_idea_get("IDEA-001", verbose=True)
    assert "Verbose idea body content here" in out
    assert "---" in out


def test_slim_has_title_and_tldr(tmp_taskmaster):
    backlog_idea_create(
        title="Project switcher",
        tldr="Single viewer for all projects.",
    )
    out = backlog_idea_get("IDEA-001")
    assert "Project switcher" in out
    assert "Single viewer for all projects" in out


def test_sections_returns_error(tmp_taskmaster):
    backlog_idea_create(
        title="Some idea",
        tldr="Quick tldr.",
    )
    out = backlog_idea_get("IDEA-001", sections=["why"])
    assert out.startswith("Error:")
    assert "canonical" in out.lower() or "sections" in out.lower()


def test_not_found_returns_message(tmp_taskmaster):
    out = backlog_idea_get("IDEA-999")
    assert "not found" in out.lower() or "Idea not found" in out
