"""User intent: the continuity rail must report what the store committed.

`continuity_items` still answered from files: tasks came from `epic["tasks"]`
inside backlog.yaml, which the v4 exporter strips from every epic, so the rail
and `/api/continuity` reported *zero* task items on every v4 project. Decisions,
issues and ideas came from directory globs rather than the rows beside them.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs


@pytest.fixture()
def seeded(tm_epic_phase):
    assert "Error" not in bs.backlog_add_task(
        title="Carry this on", epic="test-epic", phase="dev", tldr="w"
    )
    assert "Error" not in bs.backlog_update_task(
        "test-epic-001", "status", "in-progress"
    )
    assert "Error" not in bs.backlog_idea_create(title="An idea worth keeping")
    assert "Error" not in bs.backlog_issue_create(
        title="A real issue",
        severity="P1",
        components=["viewer"],
        impact="Users cannot save filters.",
        evidence="Reported three times in a week; every reload clears the filter.",
        tldr="filters do not persist",
    )
    return tm_epic_phase


def _items(kind):
    payload = json.loads(bs.backlog_continuity_items())
    return [item for item in payload["items"] if item["type"] == kind]


def test_the_rail_reports_task_items_on_a_v4_project(seeded):
    """The v4 exporter strips `tasks` from every epic in backlog.yaml, so the
    loop that read them there always yielded nothing."""
    tasks = _items("task")
    assert tasks, json.loads(bs.backlog_continuity_items())["items"]
    assert {"test-epic-001"} == {item["id"] for item in tasks}
    assert tasks[0]["title"] == "Carry this on"


def test_the_rail_reports_ideas_and_issues_from_rows(seeded):
    assert [item["title"] for item in _items("idea")] == ["An idea worth keeping"]
    assert [item["title"] for item in _items("issue")] == ["A real issue"]


def test_the_rail_does_not_parse_markdown(seeded, monkeypatch):
    """Decision 1: nothing parses files to answer a query on a warm store."""
    from taskmaster import taskmaster_v3 as v3  # noqa: PLC0415

    def refuse(*args, **kwargs):
        raise AssertionError("the continuity rail parsed markdown instead of rows")

    for name in ("read_idea", "list_idea_ids", "read_issue", "list_issue_ids",
                 "read_decision", "list_decision_ids", "read_handover",
                 "list_handover_ids"):
        monkeypatch.setattr(v3, name, refuse)

    payload = json.loads(bs.backlog_continuity_items())

    assert any(item["type"] == "task" for item in payload["items"]), payload


def test_a_caller_with_no_store_still_gets_the_file_reader(tmp_path):
    """The directory scan stays as the fallback for a caller that holds no
    store — `taskmaster_v3` is a pure module and keeps working on its own."""
    from taskmaster import taskmaster_v3 as v3  # noqa: PLC0415
    import tests.entity_helpers as entity_helpers  # noqa: PLC0415

    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir()
    bp.write_text("meta:\n  schema_version: 3\nepics: []\nhandovers: []\n",
                  encoding="utf-8")
    hid, _ = entity_helpers.write_handover(bp, tldr="shipped X", next_action="carry on")

    items = v3.continuity_items(bp)

    assert any(item["id"] == hid for item in items), items
