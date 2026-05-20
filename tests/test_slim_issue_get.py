# plugins/taskmaster/tests/test_slim_issue_get.py
"""Task 13: backlog_issue_get slim default + verbose/sections/expand_links."""
from __future__ import annotations

from backlog_server import backlog_issue_create, backlog_issue_get


def test_slim_default_excludes_body(tmp_taskmaster):
    backlog_issue_create(
        title="Auth fails",
        severity="P1",
        tldr="Auth middleware crashes on deploy.",
        impact="Users locked out.",
        body="## Repro\n\nStep 1: deploy on Friday.\n",
    )
    out = backlog_issue_get("ISS-001")
    assert "Auth middleware crashes" in out
    assert "Step 1: deploy on Friday" not in out


def test_verbose_includes_body(tmp_taskmaster):
    backlog_issue_create(
        title="Login broken",
        severity="P2",
        tldr="Login page errors.",
        impact="fixture evidence.",
        body="## Repro\n\nVerbose repro steps here.",
    )
    out = backlog_issue_get("ISS-001", verbose=True)
    assert "Verbose repro steps here" in out
    assert "---" in out


def test_slim_has_title_severity_status(tmp_taskmaster):
    backlog_issue_create(
        title="Database slow",
        severity="P2",
        tldr="DB queries timeout.",
        impact="fixture evidence.",
    )
    out = backlog_issue_get("ISS-001")
    assert "Database slow" in out
    assert "P2" in out


def test_sections_returns_only_requested(tmp_taskmaster):
    backlog_issue_create(
        title="Cache miss",
        severity="P3",
        tldr="Cache misses on cold start.",
        impact="fixture evidence.",
        body="## Repro\n\nRepro content.\n\n## Investigation\n\nInvestigation notes.",
    )
    out = backlog_issue_get("ISS-001", sections=["repro"])
    assert "Repro content" in out
    assert "Investigation notes" not in out


def test_not_found_returns_message(tmp_taskmaster):
    out = backlog_issue_get("ISS-999")
    assert "not found" in out.lower() or "Issue not found" in out
