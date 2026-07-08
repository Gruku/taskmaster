# plugins/taskmaster/tests/test_dead_tool_cull.py
"""tm-audit-006: dead MCP tool cull + lesson_reinforce consolidation.

13 tools with zero agent-surface references lose their @mcp.tool()
registration (functions stay for HTTP routes/tests). Tools that looked
dead but have live skill references stay registered: backlog_archive_epic
(backlog_update_epic redirects to it), recap_list (reflect-auto-improve
retro), viewer_prefs_get/set (migrate-v3 migration-steps).

backlog_lesson_reinforce absorbs the orphan lesson_reinforce's audit
trail: every reinforce now appends a reinforce_events entry.
"""
from __future__ import annotations

import pytest

from test_mcp_v3_exposure import _list_tool_names

CULLED = [
    "backlog_release_notes",
    "backlog_handover_latest",
    "recap_get",
    "recap_set",
    "snapshot_diff",
    "lesson_list_extended",
    "issue_list_extended",
    "lesson_reinforce",
]

KEPT = [
    "backlog_archive_epic",
    "recap_list",
    "viewer_prefs_get",
    "viewer_prefs_set",
    "backlog_lesson_reinforce",
]


@pytest.mark.parametrize("tool_name", CULLED)
def test_culled_tools_not_exposed(tool_name):
    assert tool_name not in _list_tool_names()


@pytest.mark.parametrize("tool_name", KEPT)
def test_live_referenced_tools_stay_exposed(tool_name):
    assert tool_name in _list_tool_names()


def test_backlog_lesson_reinforce_appends_audit_event(tmp_taskmaster):
    from taskmaster.backlog_server import backlog_lesson_create, backlog_lesson_reinforce
    from taskmaster.taskmaster_v3 import parse_frontmatter

    backlog_lesson_create(
        title="Atomic writes",
        kind="gotcha",
        tldr="Use atomic_write() everywhere.",
        body="## Why\n\nPrevents corruption.\n## What to do\n\nCall atomic_write().",
    )
    out = backlog_lesson_reinforce("L-001")
    assert "Reinforced" in out

    text = (tmp_taskmaster / ".taskmaster" / "lessons" / "L-001.md").read_text(
        encoding="utf-8"
    )
    fm, _ = parse_frontmatter(text)
    # backlog_lesson_create seeds one {note: created} event; the reinforce
    # must append a SECOND one (this was the orphan lesson_reinforce's job).
    events = fm.get("reinforce_events") or []
    assert len(events) == 2, f"expected created + reinforce events, got {events!r}"
    assert events[1]["source"] in {"user", "claude", "skill"}
    assert events[1].get("note") != "created"
    assert fm.get("reinforce_count") == 1


@pytest.mark.parametrize("tool_name", [
    "backlog_auto_start",
    "backlog_auto_status",
    "backlog_auto_advance",
    "backlog_auto_complete_task",
    "backlog_auto_finish",
    "backlog_auto_abort",
])
def test_auto_mode_tools_removed(tool_name):
    """Regression guard: no backlog_auto_* tool may be registered after auto removal."""
    assert tool_name not in _list_tool_names()
