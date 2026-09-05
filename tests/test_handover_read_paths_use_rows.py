"""User intent: the store rows are the authority for handovers, so the read
tools must answer from them. A handover whose markdown export failed, or a
project on network storage the store cannot export to at all, still has correct
rows — and a reader that parses files instead reports the work as missing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster import taskmaster_v3 as v3


@pytest.fixture()
def no_markdown_reads(monkeypatch):
    """Make any attempt to parse a handover file a loud failure."""
    def refuse(*args, **kwargs):
        raise AssertionError("a handover read path parsed markdown instead of rows")

    monkeypatch.setattr(v3, "read_handover", refuse)
    monkeypatch.setattr(v3, "list_handover_ids", refuse)
    return monkeypatch


@pytest.fixture()
def project(tm_epic_phase):
    added = bs.backlog_add_task(title="Work", epic="test-epic", phase="dev", tldr="w")
    assert "Error" not in added, added
    created = bs.backlog_handover_create(
        tldr="what happened",
        next_action="carry on",
        task_ids=["test-epic-001"],
        session_kind="end-of-day",
        body="## Narrative\n\nthe long version\n",
    )
    assert "Error" not in created, created
    with store.transaction(backlog_path=tm_epic_phase / ".taskmaster" / "backlog.yaml",
                           tool="test-read") as tx:
        ids = [hid for hid, _doc, _body in tx.list("handover")]
    assert len(ids) == 1, ids
    return tm_epic_phase, ids[0]


def test_thread_resume_reads_the_row(project, no_markdown_reads):
    _root, handover_id = project
    output = bs.backlog_thread_resume(ref=handover_id)
    assert "what happened" in output, output
    assert "the long version" in output, output


def test_get_task_reports_open_handovers_from_rows(project, no_markdown_reads):
    _root, handover_id = project
    output = bs.backlog_get_task(task_id="test-epic-001")
    assert handover_id in output, output


def test_continuity_items_read_rows(project, no_markdown_reads):
    output = bs.backlog_continuity_items()
    assert "what happened" in output, output
