# plugins/taskmaster/tests/test_slim_get_task.py
"""Task 11: backlog_get_task slim default + verbose/sections/expand_links."""
from __future__ import annotations

import yaml
from backlog_server import backlog_add_task, backlog_get_task, backlog_update_task


def test_slim_default_excludes_body(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="T-1", title="X", tldr="Short tldr.",
                     notes="Very long notes that should not appear in slim mode.",
                     phase="dev")
    out = backlog_get_task("T-1")
    assert "Short tldr." in out
    assert "Very long notes" not in out


def test_verbose_includes_body(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="T-2", title="X", tldr="Tldr.",
                     notes="Verbose-only content.", phase="dev")
    out = backlog_get_task("T-2", verbose=True)
    assert "Verbose-only content" in out


def test_slim_has_docs_available_not_docs(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="T-3", title="X", tldr="T.",
                     docs="plan:p.md;spec:s.md", phase="dev")
    out = backlog_get_task("T-3")
    assert "docs_available" in out
    assert "plan" in out and "spec" in out


def test_expand_links_swaps_ids_for_pills(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="T-A", title="A", tldr="A-tldr.",
                     phase="dev")
    backlog_add_task(epic="test-epic", task_id="T-B", title="B", tldr="B-tldr.",
                     depends_on="T-A", phase="dev")
    out = backlog_get_task("T-B", expand_links=True)
    assert "A-tldr" in out


def test_sections_returns_only_requested(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="T-S", title="X", tldr="T.",
                     notes="My notes here.", phase="dev")
    out = backlog_get_task("T-S", sections=["notes"])
    assert "My notes here" in out


def test_slim_not_found_returns_error(tm_epic_phase):
    out = backlog_get_task("T-NOTEXIST")
    assert "Error" in out
