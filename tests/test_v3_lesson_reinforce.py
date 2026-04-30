"""Tests for lesson reinforce_events extension and _ensure_reinforce_events migration."""
import json
from pathlib import Path

import pytest


def _write_lesson(root: Path, lesson_id: str, body_extra: str = "") -> Path:
    p = root / ".taskmaster" / "lessons" / f"{lesson_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\n"
        f"id: {lesson_id}\n"
        f"title: Sample lesson\n"
        "kind: gotcha\n"
        "tier: active\n"
        "triggers:\n"
        "  files: ['**/*.css']\n"
        "  task_titles_match: []\n"
        "  task_kinds: []\n"
        "reinforce_count: 3\n"
        "last_reinforced: 2026-04-20T10:00:00Z\n"
        "created: 2026-03-18T10:00:00Z\n"
        "related_tasks: []\n"
        "related_issues: []\n"
        f"{body_extra}"
        "---\n"
        "Lesson body.\n"
    )
    return p


def test_ensure_reinforce_events_backfills_empty_array(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_lesson(tmp_path, "L-001")

    from taskmaster_v3 import load_lesson, _ensure_reinforce_events

    lesson = load_lesson("L-001")
    assert "reinforce_events" in lesson
    assert lesson["reinforce_events"] == []

    # Direct call is idempotent on already-migrated data
    populated = {"id": "L-002", "reinforce_events": [{"at": "2026-04-25T00:00:00Z", "source": "user", "note": ""}]}
    _ensure_reinforce_events(populated)
    assert len(populated["reinforce_events"]) == 1


def test_load_lesson_preserves_existing_reinforce_events(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_lesson(
        tmp_path,
        "L-003",
        body_extra=(
            "reinforce_events:\n"
            "  - at: 2026-04-22T09:00:00Z\n"
            "    source: user\n"
            "    note: 'paid attention this time'\n"
        ),
    )

    from taskmaster_v3 import load_lesson

    lesson = load_lesson("L-003")
    assert len(lesson["reinforce_events"]) == 1
    assert lesson["reinforce_events"][0]["source"] == "user"
    assert lesson["reinforce_events"][0]["note"] == "paid attention this time"


def test_lesson_reinforce_appends_event_and_bumps_counters(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_lesson(tmp_path, "L-010")

    from taskmaster_v3 import lesson_reinforce, load_lesson

    summary = lesson_reinforce("L-010", source="user", note="caught the bug")
    assert summary["id"] == "L-010"
    assert summary["reinforce_count"] == 4  # was 3
    assert summary["reinforce_events"][-1]["source"] == "user"
    assert summary["reinforce_events"][-1]["note"] == "caught the bug"
    assert summary["last_reinforced"]  # ISO string

    # Reload from disk and confirm persistence
    lesson = load_lesson("L-010")
    assert lesson["reinforce_count"] == 4
    assert lesson["reinforce_events"][-1]["source"] == "user"


def test_lesson_reinforce_rejects_bad_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_lesson(tmp_path, "L-011")

    from taskmaster_v3 import lesson_reinforce

    with pytest.raises(ValueError):
        lesson_reinforce("L-011", source="other-thing")


def test_lesson_reinforce_unknown_id_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster" / "lessons").mkdir(parents=True, exist_ok=True)

    from taskmaster_v3 import lesson_reinforce

    with pytest.raises(FileNotFoundError):
        lesson_reinforce("L-999", source="user")


def test_lesson_reinforce_mcp_tool_returns_json_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_lesson(tmp_path, "L-020")

    from backlog_server import lesson_reinforce as tool

    out = tool("L-020", source="user", note="")
    assert "L-020" in out
    payload = json.loads(out)
    assert payload["reinforce_count"] == 4


def test_compute_lesson_shelf_core_when_recent_and_volume(tmp_path):
    from datetime import datetime, timedelta, timezone
    from taskmaster_v3 import compute_lesson_shelf

    now = datetime(2026, 4, 26, tzinfo=timezone.utc)
    events = [
        {"at": (now - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "user", "note": ""}
        for d in [1, 3, 5, 10, 20, 35, 45]
    ]
    thresholds = {
        "core_count": 7, "core_window_days": 60,
        "core_recency_days": 14, "retired_after_days": 30,
    }
    assert compute_lesson_shelf({"reinforce_events": events}, thresholds, now=now) == "core"


def test_compute_lesson_shelf_active_when_recent_but_low_volume(tmp_path):
    from datetime import datetime, timedelta, timezone
    from taskmaster_v3 import compute_lesson_shelf

    now = datetime(2026, 4, 26, tzinfo=timezone.utc)
    events = [
        {"at": (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "user", "note": ""}
    ]
    thresholds = {"core_count": 7, "core_window_days": 60, "core_recency_days": 14, "retired_after_days": 30}
    assert compute_lesson_shelf({"reinforce_events": events}, thresholds, now=now) == "active"


def test_compute_lesson_shelf_retired_when_no_recent(tmp_path):
    from datetime import datetime, timedelta, timezone
    from taskmaster_v3 import compute_lesson_shelf

    now = datetime(2026, 4, 26, tzinfo=timezone.utc)
    events = [
        {"at": (now - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "user", "note": ""}
    ]
    thresholds = {"core_count": 7, "core_window_days": 60, "core_recency_days": 14, "retired_after_days": 30}
    assert compute_lesson_shelf({"reinforce_events": events}, thresholds, now=now) == "retired"


def test_compute_lesson_shelf_active_when_high_volume_but_no_recent_fire(tmp_path):
    """High volume in window but nothing in last 14d → active, not core."""
    from datetime import datetime, timedelta, timezone
    from taskmaster_v3 import compute_lesson_shelf

    now = datetime(2026, 4, 26, tzinfo=timezone.utc)
    events = [
        {"at": (now - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "user", "note": ""}
        for d in [16, 18, 20, 22, 24, 26, 28, 29]
    ]
    thresholds = {"core_count": 7, "core_window_days": 60, "core_recency_days": 14, "retired_after_days": 30}
    assert compute_lesson_shelf({"reinforce_events": events}, thresholds, now=now) == "active"
