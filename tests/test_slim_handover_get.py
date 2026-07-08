# plugins/taskmaster/tests/test_slim_handover_get.py
"""Task 12: backlog_handover_get slim default + verbose/sections/expand_links."""
from __future__ import annotations

from taskmaster.backlog_server import backlog_handover_create, backlog_handover_get


def test_slim_default_excludes_body(tmp_taskmaster):
    result = backlog_handover_create(
        tldr="Session complete.",
        next_action="Resume next morning.",
        body="## Decisions\n\nChose approach A.\n\n## Blockers\n\nNone.",
        session_kind="end-of-day",
    )
    hid = result.split("\n")[0].split(": ", 1)[1].strip()
    out = backlog_handover_get(hid)
    assert "Session complete." in out
    assert "Chose approach A" not in out


def test_verbose_includes_body(tmp_taskmaster):
    result = backlog_handover_create(
        tldr="Session tldr.",
        body="## Decisions\n\nVerbose body content here.",
        session_kind="end-of-day",
    )
    hid = result.split("\n")[0].split(": ", 1)[1].strip()
    out = backlog_handover_get(hid, verbose=True)
    assert "Verbose body content here" in out
    assert "---" in out


def test_sections_returns_only_requested(tmp_taskmaster):
    result = backlog_handover_create(
        tldr="Sectioned handover.",
        body="## Decisions\n\nChose A over B.\n\n## Blockers\n\nAwaiting review.",
        session_kind="end-of-day",
    )
    hid = result.split("\n")[0].split(": ", 1)[1].strip()
    out = backlog_handover_get(hid, sections=["decisions"])
    assert "Chose A over B" in out
    assert "Awaiting review" not in out


def test_slim_next_action_in_output(tmp_taskmaster):
    result = backlog_handover_create(
        tldr="Context handoff.",
        next_action="Start with the auth fix.",
        session_kind="context-handoff",
    )
    hid = result.split("\n")[0].split(": ", 1)[1].strip()
    out = backlog_handover_get(hid)
    assert "Start with the auth fix" in out


def test_not_found_returns_message(tmp_taskmaster):
    out = backlog_handover_get("2099-01-01-no-such-handover")
    assert "not found" in out.lower() or "Handover not found" in out


def test_verbose_with_expand_links_does_not_error(tmp_taskmaster):
    """expand_links is silently ignored in verbose mode — should not raise."""
    result = backlog_handover_create(
        tldr="Verbose expand test.",
        next_action="Check the spec.",
        body="## Decisions\n\nChose option B.",
        session_kind="end-of-day",
    )
    hid = result.split("\n")[0].split(": ", 1)[1].strip()
    # verbose=True + expand_links=True must not crash and must return the body
    out = backlog_handover_get(hid, verbose=True, expand_links=True)
    assert "Chose option B" in out
    assert "---" in out  # frontmatter fences present
