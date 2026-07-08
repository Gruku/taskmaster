import pytest
from datetime import datetime, timezone


def test_handover_time_prefers_created_over_date():
    from taskmaster.taskmaster_v3 import _handover_time

    h = {
        "id": "2026-05-19-foo",
        "date": "2026-05-19",
        "created": "2026-05-19T14:23:45.123456+00:00",
    }
    t = _handover_time(h)
    assert t == datetime(2026, 5, 19, 14, 23, 45, 123456, tzinfo=timezone.utc)


def test_handover_time_falls_back_to_date_when_created_missing():
    from taskmaster.taskmaster_v3 import _handover_time

    h = {"id": "2026-04-26-legacy", "date": "2026-04-26T16:40:00Z"}
    t = _handover_time(h)
    assert t == datetime(2026, 4, 26, 16, 40, 0, tzinfo=timezone.utc)


def test_handover_time_falls_back_to_date_only_string():
    from taskmaster.taskmaster_v3 import _handover_time

    h = {"id": "2026-05-13-legacy", "date": "2026-05-13"}
    t = _handover_time(h)
    # Date-only parses as midnight UTC; that's the legacy behaviour we tag as such.
    assert t == datetime(2026, 5, 13, 0, 0, 0, tzinfo=timezone.utc)


def test_handover_time_raises_when_no_date_or_created():
    from taskmaster.taskmaster_v3 import _handover_time

    with pytest.raises(ValueError, match="neither 'created' nor 'date'"):
        _handover_time({"id": "2026-05-19-broken"})


def _write_handover(tmp_path, name, body):
    import yaml
    p = tmp_path / ".taskmaster" / "handovers" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(body, sort_keys=False).rstrip()
    p.write_text(f"---\n{fm}\n---\n\nbody\n", encoding="utf-8")
    return p


def test_session_start_end_use_handover_created(tmp_path, monkeypatch):
    from taskmaster.taskmaster_v3 import list_sessions

    monkeypatch.chdir(tmp_path)

    # Two handovers in the same session, real wall-clock times 20 minutes apart
    # (well within the 30-minute SESSION_GAP_MINUTES threshold so they group together).
    # `date` is date-only (the production shape); `created` carries the precise time.
    _write_handover(tmp_path, "2026-05-19-first.md", {
        "id": "2026-05-19-first",
        "date": "2026-05-19",
        "created": "2026-05-19T14:23:00+00:00",
        "tldr": "...",
        "next_action": "...",
        "task_ids": ["T-1"],
        "session_kind": "context-handoff",
        "context_size_at_write": 0.5,
    })
    _write_handover(tmp_path, "2026-05-19-second.md", {
        "id": "2026-05-19-second",
        "date": "2026-05-19",
        "created": "2026-05-19T14:43:00+00:00",
        "tldr": "...",
        "next_action": "...",
        "task_ids": ["T-1"],
        "session_kind": "end-of-day",
        "context_size_at_write": 0.6,
    })

    sessions = list_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert s["start"] == "2026-05-19T14:23:00+00:00"
    assert s["end"] == "2026-05-19T14:43:00+00:00"
    assert s["duration"] == 20 * 60  # 1200 seconds


def test_session_with_only_date_field_marks_time_resolution(tmp_path, monkeypatch):
    """A session built from a legacy handover (no `created`) is tagged as
    date-only so the viewer can render the date without inventing a time."""
    from taskmaster.taskmaster_v3 import list_sessions

    monkeypatch.chdir(tmp_path)
    _write_handover(tmp_path, "2026-05-13-legacy.md", {
        "id": "2026-05-13-legacy",
        "date": "2026-05-13",   # date-only, no `created`
        "tldr": "...",
        "next_action": "...",
        "task_ids": ["T-9"],
        "session_kind": "end-of-day",
        "context_size_at_write": 0.5,
    })

    sessions = list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["time_resolution"] == "date-only"


def test_session_with_created_marks_time_resolution_full(tmp_path, monkeypatch):
    from taskmaster.taskmaster_v3 import list_sessions

    monkeypatch.chdir(tmp_path)
    _write_handover(tmp_path, "2026-05-19-modern.md", {
        "id": "2026-05-19-modern",
        "date": "2026-05-19",
        "created": "2026-05-19T14:23:00+00:00",
        "tldr": "...",
        "next_action": "...",
        "task_ids": ["T-9"],
        "session_kind": "end-of-day",
        "context_size_at_write": 0.5,
    })

    sessions = list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["time_resolution"] == "full"
