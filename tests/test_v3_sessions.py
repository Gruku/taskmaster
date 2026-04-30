import textwrap


def _write_handover(tmp_path, name: str, body: dict, body_md: str = "..."):
    import yaml
    p = tmp_path / ".taskmaster" / "handovers" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(body, sort_keys=False).rstrip()
    p.write_text(f"---\n{fm}\n---\n\n{body_md}\n", encoding="utf-8")
    return p


def test_list_sessions_synthesises_from_handovers(tmp_path, monkeypatch):
    from taskmaster_v3 import list_sessions
    monkeypatch.chdir(tmp_path)

    _write_handover(tmp_path, "2026-04-26-1640-foo.md", {
        "id": "2026-04-26-1640-foo",
        "date": "2026-04-26T16:40:00Z",
        "tldr": "...", "next_action": "...",
        "task_ids": ["T-148"],
        "session_kind": "context-handoff",
        "context_size_at_write": 0.8,
    })
    _write_handover(tmp_path, "2026-04-26-1648-bar.md", {
        "id": "2026-04-26-1648-bar",
        "date": "2026-04-26T16:48:00Z",
        "tldr": "...", "next_action": "...",
        "task_ids": ["T-148"],
        "session_kind": "end-of-day",
        "context_size_at_write": 0.9,
    })

    sessions = list_sessions()
    assert len(sessions) >= 1
    s = sessions[0]
    assert set(s.keys()) >= {
        "id", "start", "end", "duration", "handover_ids",
        "recap_id", "task_ids", "parallel_with",
    }
    assert s["id"].startswith("SES-")
    # Both handovers reference the same task within ~10 min — clustered into one session.
    assert sorted(s["handover_ids"]) == [
        "2026-04-26-1640-foo", "2026-04-26-1648-bar",
    ]
    assert s["task_ids"] == ["T-148"]


def test_list_sessions_marks_parallel_when_overlapping(tmp_path, monkeypatch):
    from taskmaster_v3 import list_sessions
    monkeypatch.chdir(tmp_path)
    # Two non-overlapping task scopes; same time window → parallel.
    _write_handover(tmp_path, "2026-04-26-1408-a.md", {
        "id": "2026-04-26-1408-a",
        "date": "2026-04-26T14:08:00Z",
        "tldr": "...", "next_action": "...",
        "task_ids": ["T-100"], "session_kind": "end-of-day",
        "context_size_at_write": 0.5,
    })
    _write_handover(tmp_path, "2026-04-26-1410-b.md", {
        "id": "2026-04-26-1410-b",
        "date": "2026-04-26T14:10:00Z",
        "tldr": "...", "next_action": "...",
        "task_ids": ["T-200"], "session_kind": "end-of-day",
        "context_size_at_write": 0.5,
    })
    sessions = list_sessions()
    by_id = {s["id"]: s for s in sessions}
    assert len(sessions) == 2
    a, b = sessions
    assert a["id"] in b["parallel_with"]
    assert b["id"] in a["parallel_with"]
