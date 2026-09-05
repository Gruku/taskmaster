import textwrap


# `list_sessions` / `get_session_detail` take the store's committed handover
# rows now. Globbing `handovers/*.md` described the export, not the store: a
# handover whose file write failed, or a project on network storage the store
# cannot export to, vanished from the timeline while the row was right there.
def _row(body: dict, body_md: str = "..."):
    return (body["id"], body, body_md)


def test_list_sessions_synthesises_from_handovers():
    from taskmaster.taskmaster_v3 import list_sessions

    foo = _row({
        "id": "2026-04-26-1640-foo",
        "date": "2026-04-26T16:40:00Z",
        "tldr": "...", "next_action": "...",
        "task_ids": ["T-148"],
        "session_kind": "context-handoff",
        "context_size_at_write": 0.8,
    })
    bar = _row({
        "id": "2026-04-26-1648-bar",
        "date": "2026-04-26T16:48:00Z",
        "tldr": "...", "next_action": "...",
        "task_ids": ["T-148"],
        "session_kind": "end-of-day",
        "context_size_at_write": 0.9,
    })

    sessions = list_sessions([foo, bar])
    # Threadless handovers each form their own solo lane — no clustering.
    assert len(sessions) == 2
    by_id = {s["id"]: s for s in sessions}
    assert set(by_id) == {"2026-04-26-1640-foo", "2026-04-26-1648-bar"}
    s = by_id["2026-04-26-1640-foo"]
    assert set(s.keys()) >= {
        "id", "kind", "status", "start", "end", "duration", "time_resolution",
        "handover_ids", "task_ids",
    }
    assert s["kind"] == "thread"
    assert s["handover_ids"] == ["2026-04-26-1640-foo"]
    assert s["task_ids"] == ["T-148"]
    assert "parallel_with" not in s


def test_list_sessions_solo_lanes_for_overlapping_threadless_handovers():
    from taskmaster.taskmaster_v3 import list_sessions

    # Two threadless handovers, same time window, different task scopes.
    # Each forms its own lane now — overlap is a viewer-side rendering concern,
    # not something list_sessions tracks (parallel_with is gone).
    first = _row({
        "id": "2026-04-26-1408-a",
        "date": "2026-04-26T14:08:00Z",
        "tldr": "...", "next_action": "...",
        "task_ids": ["T-100"], "session_kind": "end-of-day",
        "context_size_at_write": 0.5,
    })
    second = _row({
        "id": "2026-04-26-1410-b",
        "date": "2026-04-26T14:10:00Z",
        "tldr": "...", "next_action": "...",
        "task_ids": ["T-200"], "session_kind": "end-of-day",
        "context_size_at_write": 0.5,
    })
    sessions = list_sessions([first, second])
    assert len(sessions) == 2
    by_id = {s["id"]: s for s in sessions}
    assert set(by_id) == {"2026-04-26-1408-a", "2026-04-26-1410-b"}
    assert all("parallel_with" not in s for s in sessions)


def test_get_session_detail_bundles_handovers():
    from taskmaster.taskmaster_v3 import get_session_detail

    foo = _row({
        "id": "2026-04-26-1640-foo",
        "date": "2026-04-26T16:40:00Z",
        "tldr": "Stitched the gate", "next_action": "Rebase",
        "task_ids": ["T-148"], "session_kind": "deep-context",
        "context_size_at_write": 0.8,
    }, body_md="Resume by running pytest -k gate.")

    detail = get_session_detail("2026-04-26-1640-foo", [foo])
    assert detail["session"]["id"] == "2026-04-26-1640-foo"
    assert len(detail["handovers"]) == 1
    h = detail["handovers"][0]
    assert h["id"] == "2026-04-26-1640-foo"
    assert h["viewer_kind"] == "mid-task"  # deep-context → mid-task
    assert h["tldr"] == "Stitched the gate"
    assert h["resume_prompt"].startswith("Resume by running")
    assert detail["task_ids"] == ["T-148"]


def test_get_session_detail_returns_none_when_missing():
    from taskmaster.taskmaster_v3 import get_session_detail

    assert get_session_detail("SES-9999", []) is None
