# plugins/taskmaster/tests/test_dead_tool_cull.py
"""tm-audit-006: dead MCP tool cull.

13 tools with zero agent-surface references lose their @mcp.tool()
registration (functions stay for HTTP routes/tests). Tools that looked
dead but have live skill references stay registered: backlog_archive_epic
(backlog_update_epic redirects to it), viewer_prefs_get/set (migrate-v3
migration-steps).
"""
from __future__ import annotations

import pytest

from test_mcp_v3_exposure import _list_tool_names

CULLED = [
    "backlog_release_notes",
    "backlog_handover_latest",
    "issue_list_extended",
]

KEPT = [
    "backlog_archive_epic",
    "viewer_prefs_get",
    "viewer_prefs_set",
]


@pytest.mark.parametrize("tool_name", CULLED)
def test_culled_tools_not_exposed(tool_name):
    assert tool_name not in _list_tool_names()


@pytest.mark.parametrize("tool_name", KEPT)
def test_live_referenced_tools_stay_exposed(tool_name):
    assert tool_name in _list_tool_names()


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
