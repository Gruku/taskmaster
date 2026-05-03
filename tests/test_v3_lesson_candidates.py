"""Tests for v3 lesson-candidate persistence (defer/read/clear)."""
from pathlib import Path

import pytest
import yaml

from taskmaster_v3 import (
    LESSON_CANDIDATE_KINDS,
    LESSON_CANDIDATE_SCOPES,
    lesson_candidates_clear,
    lesson_candidates_defer,
    lesson_candidates_path,
    lesson_candidates_read,
)


def _make_backlog(tmp_path: Path) -> Path:
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {"updated": "2026-05-03"}, "epics": []}))
    (tmp_path / "lessons").mkdir()
    return bp


def test_candidate_constants_match_spec():
    assert set(LESSON_CANDIDATE_KINDS) == {"pattern", "anti-pattern", "gotcha"}
    assert set(LESSON_CANDIDATE_SCOPES) == {"point", "session"}


def test_candidates_path_resolves_under_lessons_dir(tmp_path):
    bp = _make_backlog(tmp_path)
    p = lesson_candidates_path(bp)
    assert p == bp.parent / "lessons" / "_candidates.md"


def test_candidates_read_returns_empty_when_file_missing(tmp_path):
    bp = _make_backlog(tmp_path)
    assert lesson_candidates_read(bp) == []


def test_defer_creates_file_and_returns_index(tmp_path):
    bp = _make_backlog(tmp_path)
    idx = lesson_candidates_defer(
        bp,
        title="useEffect reads useLocation().state without active-tab guard",
        kind="gotcha",
        topic="multi-tab fanout",
        scope="point",
        context="session 2026-05-03; commit cb6927c0",
    )
    assert idx == 0
    p = lesson_candidates_path(bp)
    assert p.exists()
    raw = p.read_text(encoding="utf-8")
    assert "# Lesson Candidates" in raw
    assert "```yaml" in raw
    assert "useEffect reads useLocation" in raw


def test_defer_appends_subsequent_entries(tmp_path):
    bp = _make_backlog(tmp_path)
    a = lesson_candidates_defer(bp, title="first")
    b = lesson_candidates_defer(bp, title="second", kind="pattern")
    c = lesson_candidates_defer(bp, title="third", scope="session")
    assert (a, b, c) == (0, 1, 2)
    items = lesson_candidates_read(bp)
    assert [i["title"] for i in items] == ["first", "second", "third"]
    assert items[1]["kind"] == "pattern"
    assert items[2]["scope"] == "session"


def test_defer_round_trip_preserves_fields(tmp_path):
    bp = _make_backlog(tmp_path)
    lesson_candidates_defer(
        bp,
        title="round-trip me",
        kind="anti-pattern",
        topic="bare exception",
        scope="point",
        context="discovered in PR review",
    )
    items = lesson_candidates_read(bp)
    assert items[0]["title"] == "round-trip me"
    assert items[0]["kind"] == "anti-pattern"
    assert items[0]["topic"] == "bare exception"
    assert items[0]["scope"] == "point"
    assert items[0]["context"] == "discovered in PR review"
    assert "deferred_at" in items[0]


def test_defer_rejects_invalid_kind(tmp_path):
    bp = _make_backlog(tmp_path)
    with pytest.raises(ValueError, match="kind"):
        lesson_candidates_defer(bp, title="bad", kind="not-a-kind")


def test_defer_rejects_invalid_scope(tmp_path):
    bp = _make_backlog(tmp_path)
    with pytest.raises(ValueError, match="scope"):
        lesson_candidates_defer(bp, title="bad", scope="forever")


def test_defer_rejects_empty_title(tmp_path):
    bp = _make_backlog(tmp_path)
    with pytest.raises(ValueError, match="title"):
        lesson_candidates_defer(bp, title="   ")


def test_clear_removes_specified_indices(tmp_path):
    bp = _make_backlog(tmp_path)
    lesson_candidates_defer(bp, title="a")
    lesson_candidates_defer(bp, title="b")
    lesson_candidates_defer(bp, title="c")
    n = lesson_candidates_clear(bp, indices=[0, 2])
    assert n == 2
    remaining = lesson_candidates_read(bp)
    assert [i["title"] for i in remaining] == ["b"]


def test_clear_tolerates_out_of_range_indices(tmp_path):
    bp = _make_backlog(tmp_path)
    lesson_candidates_defer(bp, title="only one")
    n = lesson_candidates_clear(bp, indices=[0, 5, 99])
    assert n == 1
    assert lesson_candidates_read(bp) == []


def test_clear_on_empty_file_is_noop(tmp_path):
    bp = _make_backlog(tmp_path)
    n = lesson_candidates_clear(bp, indices=[0])
    assert n == 0
    assert lesson_candidates_read(bp) == []


import json as _json
from datetime import datetime, timedelta, timezone

from taskmaster_v3 import scan_transcripts_for_candidates


def _write_jsonl(project_dir: Path, name: str, lines: list[dict]) -> Path:
    p = project_dir / name
    p.write_text(
        "\n".join(_json.dumps(li) for li in lines) + "\n", encoding="utf-8"
    )
    return p


def test_scan_returns_empty_when_no_jsonl(tmp_path):
    out = scan_transcripts_for_candidates(tmp_path, days=7)
    assert out == []


def test_scan_finds_inline_candidate_in_jsonl(tmp_path):
    project_dir = tmp_path / "projects" / "my-project"
    project_dir.mkdir(parents=True)
    _write_jsonl(
        project_dir,
        "session-aaa.jsonl",
        [
            {"role": "assistant", "content": "Working on auth..."},
            {
                "role": "assistant",
                "content": (
                    'I noticed a recurring issue.\n'
                    '<lesson-candidate kind="gotcha" topic="auth-session">\n'
                    'auth/session.ts must be read before edit; legacy globals leak.\n'
                    '</lesson-candidate>\nMoving on.'
                ),
            },
        ],
    )
    out = scan_transcripts_for_candidates(project_dir, days=7)
    assert len(out) == 1
    item = out[0]
    assert item["kind"] == "gotcha"
    assert item["topic"] == "auth-session"
    assert "auth/session.ts must be read" in item["body"]
    assert item["source_file"].endswith("session-aaa.jsonl")
    assert item["source_line"] >= 1


def test_scan_filters_by_kind(tmp_path):
    project_dir = tmp_path / "p"
    project_dir.mkdir()
    _write_jsonl(
        project_dir,
        "s1.jsonl",
        [
            {"role": "assistant", "content": '<lesson-candidate kind="gotcha">A</lesson-candidate>'},
            {"role": "assistant", "content": '<lesson-candidate kind="pattern">B</lesson-candidate>'},
        ],
    )
    out = scan_transcripts_for_candidates(project_dir, kind_filter="pattern")
    assert len(out) == 1
    assert out[0]["body"].strip() == "B"


def test_scan_respects_days_window(tmp_path):
    project_dir = tmp_path / "p"
    project_dir.mkdir()
    old = _write_jsonl(project_dir, "old.jsonl", [
        {"role": "assistant", "content": '<lesson-candidate>old</lesson-candidate>'}
    ])
    new = _write_jsonl(project_dir, "new.jsonl", [
        {"role": "assistant", "content": '<lesson-candidate>new</lesson-candidate>'}
    ])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    import os as _os
    _os.utime(old, (cutoff, cutoff))

    out = scan_transcripts_for_candidates(project_dir, days=7)
    titles = [i["body"].strip() for i in out]
    assert "new" in titles and "old" not in titles


def test_scan_skips_files_outside_jsonl_extension(tmp_path):
    project_dir = tmp_path / "p"
    project_dir.mkdir()
    (project_dir / "notes.txt").write_text(
        '<lesson-candidate>ignored</lesson-candidate>', encoding="utf-8"
    )
    assert scan_transcripts_for_candidates(project_dir, days=7) == []


def test_scan_handles_malformed_jsonl_lines(tmp_path):
    project_dir = tmp_path / "p"
    project_dir.mkdir()
    p = project_dir / "broken.jsonl"
    p.write_text(
        "not-json-at-all\n"
        + _json.dumps({"role": "assistant",
                       "content": '<lesson-candidate>survivor</lesson-candidate>'})
        + "\n",
        encoding="utf-8",
    )
    out = scan_transcripts_for_candidates(project_dir, days=7)
    assert len(out) == 1
    assert "survivor" in out[0]["body"]
