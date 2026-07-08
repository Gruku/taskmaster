"""Regression guards for v3 MCP tool exposure.

These tests verify that the v3 narrative-continuity surface is registered
on the MCP server as @mcp.tool() decorated functions and is callable from
ToolSearch / list_tools — independent of whether the running session has
already loaded an older plugin install.

A previous incident (tracked by v3-release-001): backlog_migrate_v3 had the
@mcp.tool() decorator in source but the running server (v1.11.1) did not
expose it. Without it, users could not opt into v3 from a v2 backlog. These
tests prevent that regression.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# Cache the tool list across tests in this module — list_tools is invariant
# for a given source tree, and one subprocess spawn beats 32.
_CACHED_TOOL_NAMES: list[str] | None = None


def _list_tool_names() -> list[str]:
    """Enumerate registered MCP tools by spawning a fresh Python process.

    Subprocess isolation matters: other test modules in this suite mock
    `FastMCP` (e.g. via unittest.mock.patch), and those mocks survive in the
    parent interpreter even after the patch context exits. Re-importing
    backlog_server in-process picks up the leaked mock and `list_tools()`
    returns a MagicMock instead of a coroutine.

    The subprocess runs with a clean module cache, gets the real FastMCP,
    and returns the tool names as JSON on stdout.
    """
    global _CACHED_TOOL_NAMES
    if _CACHED_TOOL_NAMES is not None:
        return _CACHED_TOOL_NAMES

    script = (
        "import asyncio, json, sys, importlib.util\n"
        f"sys.path.insert(0, r'{_PLUGIN_ROOT}')\n"
        f"spec = importlib.util.spec_from_file_location('bs', r'{_PLUGIN_ROOT / 'backlog_server.py'}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "tools = asyncio.run(mod.mcp.list_tools())\n"
        "print(json.dumps(sorted(t.name for t in tools)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tool-enumeration subprocess failed: {result.stderr}")
    # The script may emit FastMCP startup warnings on stderr; tool list is the LAST stdout line.
    last_line = result.stdout.strip().splitlines()[-1]
    _CACHED_TOOL_NAMES = json.loads(last_line)
    return _CACHED_TOOL_NAMES


# v3-release-001 — the original blocker
def test_backlog_migrate_v3_is_exposed():
    names = _list_tool_names()
    assert "backlog_migrate_v3" in names, (
        "backlog_migrate_v3 missing from MCP surface — v2 users cannot opt into v3."
    )


# Handover surface (v3-skills-002, v3-skills-015)
@pytest.mark.parametrize(
    "tool_name",
    [
        "backlog_handover_create",
        "backlog_handover_get",
        "backlog_handover_list",
        "backlog_handover_resync",
        "backlog_handover_supersede",
    ],
)
def test_handover_tools_exposed(tool_name):
    assert tool_name in _list_tool_names()


# Issue surface (v3-skills-004)
@pytest.mark.parametrize(
    "tool_name",
    [
        "backlog_issue_create",
        "backlog_issue_get",
        "backlog_issue_list",
        "backlog_issue_update",
        "backlog_issue_resync",
    ],
)
def test_issue_tools_exposed(tool_name):
    assert tool_name in _list_tool_names()


# Idea surface (ideas feature)
@pytest.mark.parametrize(
    "tool_name",
    [
        "backlog_idea_create",
        "backlog_idea_get",
        "backlog_idea_list",
        "backlog_idea_update",
    ],
)
def test_idea_tools_exposed(tool_name):
    assert tool_name in _list_tool_names()


# Area surface (epic B task 1)
@pytest.mark.parametrize(
    "tool_name",
    [
        "backlog_area_create",
        "backlog_area_get",
        "backlog_area_list",
        "backlog_area_update",
    ],
)
def test_area_tools_exposed(tool_name):
    assert tool_name in _list_tool_names()


# Recap + snapshot (v3-skills-006)
@pytest.mark.parametrize(
    "tool_name",
    [
        "backlog_recap",
        "backlog_snapshot",
    ],
)
def test_recap_snapshot_tools_exposed(tool_name):
    assert tool_name in _list_tool_names()


# Project structure visibility (project-structure-visibility-003)
@pytest.mark.parametrize(
    "tool_name",
    [
        "backlog_project_structure",
    ],
)
def test_project_structure_tools_exposed(tool_name):
    assert tool_name in _list_tool_names()


# Project manifest (project-manifest-001)
@pytest.mark.parametrize(
    "tool_name",
    [
        "backlog_project_get",
        "backlog_project_get_field",
        "backlog_project_set",
        "backlog_project_init",
        "backlog_project_ship_order",
        "backlog_project_error_trace_ladder",
    ],
)
def test_project_manifest_tools_exposed(tool_name):
    assert tool_name in _list_tool_names()


def test_full_v3_surface_count():
    """Sanity check the v3 surface size hasn't accidentally shrunk.

    If a tool is intentionally removed, lower this floor. If it grew, raise
    the floor in a follow-up commit. The point is to catch silent loss.
    """
    names = _list_tool_names()
    v3_tools = [
        n for n in names
        if any(k in n for k in ("migrate", "handover", "issue", "idea", "recap", "snapshot", "area"))
    ]
    # Floor lowered 38 -> 36 by tm-audit-006: intentional cull of 13 dead
    # registrations (10 of them match the v3 keyword filter).
    # Floor lowered 36 -> 30 by remove-auto-mode-001: 6 backlog_auto_* tools removed
    # (auto_ keyword no longer in filter; actual live count = 30).
    # Floor lowered 30 -> 19 by lessons-removal epic C task 1: 11 backlog_lesson_*
    # tools removed (lesson keyword dropped from filter too).
    # Floor raised 19 -> 23 by epic B task 1: 4 backlog_area_* tools added
    # ("area" keyword added to filter too).
    assert len(v3_tools) >= 23, f"v3 surface shrank to {len(v3_tools)} tools (was 23 after epic B task 1)"
