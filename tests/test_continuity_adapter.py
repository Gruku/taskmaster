from pathlib import Path
import pytest

from plugins.taskmaster import taskmaster_v3 as tm


@pytest.fixture
def backlog_with_handover(tmp_path):
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir()
    bp.write_text(
        "meta:\n  schema_version: 3\nepics: []\nhandovers: []\n",
        encoding="utf-8",
    )
    hid, _ = tm.write_handover(
        bp,
        tldr="shipped X",
        next_action="resume Y on feature/foo",
        task_ids=["t-001"],
        branch="feature/foo",
        session_kind="end-of-day",
    )
    return bp, hid


def test_continuity_item_from_handover_populates_required_fields(backlog_with_handover):
    bp, hid = backlog_with_handover
    items = tm.continuity_items(bp)
    han = [i for i in items if i["type"] == "handover"]
    assert han, "handover not projected"
    it = han[0]
    assert it["id"] == hid
    assert it["title"]  # tldr
    assert it["next"] == "resume Y on feature/foo"
    assert it["action_class"] in ("resume", "ambient")
    assert it["task_id"] == "t-001"
    assert it["branch"] == "feature/foo"
    assert it["timestamp"]
    assert isinstance(it["age_days"], (int, float))


def test_continuity_item_filters_auto_stage_by_default(backlog_with_handover):
    bp, _ = backlog_with_handover
    tm.write_handover(bp, tldr="auto-stage stub", session_kind="auto-stage")
    items_default = tm.continuity_items(bp)
    assert not any(i["type"] == "handover" and i["title"] == "auto-stage stub"
                   for i in items_default)
    items_all = tm.continuity_items(bp, include_auto_stage=True)
    assert any(i["title"] == "auto-stage stub" for i in items_all)


def test_continuity_handover_routes_resume_when_recent_and_todo(backlog_with_handover):
    bp, _ = backlog_with_handover
    items = tm.continuity_items(bp)
    h = [i for i in items if i["type"] == "handover"][0]
    # Fresh handover with default status (todo) → Resume rail.
    assert h["action_class"] == "resume"
