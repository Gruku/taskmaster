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


def test_continuity_open_decision_routes_to_decide(backlog_with_handover):
    bp, _ = backlog_with_handover
    tm.write_decision(bp, title="pick a path",
                      options=["a", "b"], recommendation=1)
    items = tm.continuity_items(bp)
    decs = [i for i in items if i["type"] == "decision"]
    assert decs and decs[0]["action_class"] == "decide"
    assert decs[0]["title"] == "pick a path"


def test_continuity_resolved_decision_routes_to_ambient(backlog_with_handover):
    bp, _ = backlog_with_handover
    tm.write_decision(bp, title="x", options=["a", "b"])
    tm.resolve_decision(bp, "DEC-001", resolved_with=1)
    items = tm.continuity_items(bp)
    assert all(i["action_class"] != "decide" for i in items if i["type"] == "decision")


def test_continuity_in_review_task_routes_to_review(tmp_path):
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir()
    bp.write_text(
        "meta:\n  schema_version: 3\n"
        "epics:\n  - id: e1\n    title: E\n"
        "    tasks:\n"
        "      - id: t1\n        title: in-review task\n"
        "        status: in-review\n        priority: high\n",
        encoding="utf-8",
    )
    items = tm.continuity_items(bp)
    tasks = [i for i in items if i["type"] == "task"]
    assert tasks and tasks[0]["action_class"] == "review"


def test_route_action_class_for_task_uses_status_and_age():
    today = tm.datetime.now(tm.timezone.utc)
    # In-progress + recent → resume.
    t = {"status": "in-progress", "last_referenced": today.isoformat()}
    assert tm._task_to_item(t, "t1")["action_class"] == "resume"
    # In-progress + 10d idle → clean-up.
    old = (today - tm.timedelta(days=10)).isoformat()
    t2 = {"status": "in-progress", "last_referenced": old}
    assert tm._task_to_item(t2, "t2")["action_class"] == "clean-up"


def test_route_action_class_for_issue_uses_severity_and_age():
    today = tm.datetime.now(tm.timezone.utc).isoformat()
    p1 = {"severity": "P1", "status": "open", "discovered": today, "id": "ISS-1", "title": "t"}
    assert tm._issue_to_item(p1)["action_class"] == "review"
    old = (tm.datetime.now(tm.timezone.utc) - tm.timedelta(days=20)).isoformat()
    p3 = {"severity": "P3", "status": "open", "discovered": old, "id": "ISS-2", "title": "t"}
    assert tm._issue_to_item(p3)["action_class"] == "clean-up"


def test_age_days_accepts_datetime_and_date():
    """PyYAML auto-parses ISO timestamps into datetime/date objects when reading
    frontmatter. _age_days must accept them, not just strings."""
    now = tm.datetime(2026, 5, 16, 12, 0, 0, tzinfo=tm.timezone.utc)
    # datetime input
    ts_dt = tm.datetime(2026, 5, 14, 12, 0, 0, tzinfo=tm.timezone.utc)
    assert tm._age_days(ts_dt, now=now) == pytest.approx(2.0)
    # naive datetime input (assume UTC)
    ts_naive = tm.datetime(2026, 5, 14, 12, 0, 0)
    assert tm._age_days(ts_naive, now=now) == pytest.approx(2.0)
    # date input
    from datetime import date as _date
    assert tm._age_days(_date(2026, 5, 14), now=now) == pytest.approx(2.5)
    # str input still works
    assert tm._age_days("2026-05-14T12:00:00+00:00", now=now) == pytest.approx(2.0)
    # None / empty
    assert tm._age_days(None) == 0.0
    assert tm._age_days("") == 0.0


def test_continuity_items_survives_yaml_parsed_decision_timestamp(tmp_path):
    """Regression: a decision file written by the standard writer and read back
    via yaml.safe_load produces a datetime object for created_at. The projector
    must not crash."""
    import yaml
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir()
    bp.write_text("meta:\n  schema_version: 3\n", encoding="utf-8")
    decisions_dir = bp.parent / "decisions"
    decisions_dir.mkdir()
    fm = {
        "id": "DEC-001",
        "title": "smoke",
        "status": "open",
        "options": ["a", "b"],
        "recommendation": 1,
        "task_id": None,
        "related_issues": [],
        "branch": None,
        "resolved_with": None,
        "resolved_rationale": None,
        "dropped_reason": None,
        "created_at": tm.datetime.now(tm.timezone.utc).isoformat(timespec="microseconds"),
        "resolved_at": None,
        "raised_in": None,
        "referenced_in": [],
        "resolved_in": None,
    }
    # Write the way write_task_file does (bare yaml.dump, no string-forcing).
    fm_text = yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True)
    (decisions_dir / "DEC-001.md").write_text(f"---\n{fm_text}---\n\nbody\n", encoding="utf-8")
    # Must not raise.
    items = tm.continuity_items(bp)
    decs = [i for i in items if i["type"] == "decision"]
    assert decs and decs[0]["id"] == "DEC-001"
    assert isinstance(decs[0]["age_days"], (int, float))
