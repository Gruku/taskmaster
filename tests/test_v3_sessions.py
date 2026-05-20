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
        "id", "start", "end", "duration", "time_resolution", "handover_ids",
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


def test_get_session_detail_bundles_handovers_recap(tmp_path, monkeypatch):
    from taskmaster_v3 import get_session_detail, save_recap
    monkeypatch.chdir(tmp_path)

    _write_handover(tmp_path, "2026-04-26-1640-foo.md", {
        "id": "2026-04-26-1640-foo",
        "date": "2026-04-26T16:40:00Z",
        "tldr": "Stitched the gate", "next_action": "Rebase",
        "task_ids": ["T-148"], "session_kind": "deep-context",
        "context_size_at_write": 0.8,
    }, body_md="Resume by running pytest -k gate.")
    save_recap(
        session_id="SES-0001",
        frontmatter={"snapshot_before": "SNAP-0000", "snapshot_after": "SNAP-0001",
                     "generator": "claude", "generated_at": "2026-04-26T16:48Z",
                     "token_cost": 1840},
        title="Stitched", what_happened="A", what_landed="B", whats_next="C",
    )

    detail = get_session_detail("SES-0001")
    assert detail["session"]["id"] == "SES-0001"
    assert len(detail["handovers"]) == 1
    h = detail["handovers"][0]
    assert h["id"] == "2026-04-26-1640-foo"
    assert h["viewer_kind"] == "mid-task"  # deep-context → mid-task
    assert h["tldr"] == "Stitched the gate"
    assert h["resume_prompt"].startswith("Resume by running")
    assert detail["recap"]["frontmatter"]["session_id"] == "SES-0001"
    assert detail["task_ids"] == ["T-148"]


def test_get_session_detail_returns_none_when_missing(tmp_path, monkeypatch):
    from taskmaster_v3 import get_session_detail
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    assert get_session_detail("SES-9999") is None
