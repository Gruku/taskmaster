# User intent: prove backlog_search reaches every entity kind through the FTS5 index, filters by
# kind, survives punctuation in the query, and still answers from the substring scan when the
# derived index is unavailable — so search never regresses on a machine without an index.db.
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

FIXTURE_SRC = PLUGIN_ROOT / "tests" / "fixtures" / "index_backlog" / ".taskmaster"


@pytest.fixture()
def indexed_server(tmp_taskmaster):
    """`tmp_taskmaster` with the index fixture copied in and the index built."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415
    from taskmaster.index import build_index  # noqa: PLC0415

    shutil.copytree(FIXTURE_SRC, tmp_taskmaster / ".taskmaster", dirs_exist_ok=True)
    build_index(bs._backlog_path())
    return bs


def test_search_reaches_every_entity_kind(indexed_server):
    """`usage` appears in a task, bug, issue, handover, decision and idea in the fixture."""
    out = indexed_server.backlog_search("usage")
    for eid in ("eng-001", "B-001", "ISS-001", "DEC-001", "IDEA-001",
                "2026-09-01-fixture-handover"):
        assert eid in out, f"{eid} missing from:\n{out}"


def test_search_matches_task_body_not_just_title(indexed_server):
    """`ModelUsageService` only occurs in eng-001's notes, which live in the FTS body."""
    out = indexed_server.backlog_search("ModelUsageService")
    assert "eng-001" in out


def test_search_kinds_filter(indexed_server):
    out = indexed_server.backlog_search("usage", kinds=["bug"])
    assert "B-001" in out and "B-002" in out
    assert "eng-001" not in out and "ISS-001" not in out


def test_search_unknown_kinds_are_ignored_not_an_error(indexed_server):
    """An unknown kind is dropped; a list of only unknown kinds searches everything."""
    mixed = indexed_server.backlog_search("usage", kinds=["bug", "nonsense"])
    assert "B-001" in mixed and "eng-001" not in mixed
    junk = indexed_server.backlog_search("usage", kinds=["nonsense"])
    assert "eng-001" in junk and "B-001" in junk


def test_task_and_non_task_lines_keep_their_shapes(indexed_server):
    out = indexed_server.backlog_search("usage")
    assert "- `eng-001` — Rework model usage accounting (high, eng, in-progress)" in out
    assert "- `B-001` — Usage rows are double counted (bug, open)" in out


def test_header_counts_total_matches(indexed_server):
    out = indexed_server.backlog_search("usage")
    assert out.startswith("**9 matches** for `usage`:")


def test_search_punctuation_does_not_error(indexed_server):
    assert "Error" not in indexed_server.backlog_search("abuse-path-001: thing")
    assert "Error" not in indexed_server.backlog_search('a "quoted" NOT thing')
    assert "Error" not in indexed_server.backlog_search("   ")


def test_no_match_message(indexed_server):
    """The FTS path covers every kind, so its empty answer must not say "tasks"."""
    assert indexed_server.backlog_search("zzzznotathing") == "No matches for `zzzznotathing`"


def test_result_list_is_capped_at_fifteen(indexed_server, monkeypatch):
    """Header reports the true total; the rendered list never exceeds 15 lines."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    monkeypatch.setattr(bs, "_SEARCH_LIMIT", 2)
    out = indexed_server.backlog_search("usage")
    assert out.startswith("**9 matches**")
    assert len([ln for ln in out.splitlines() if ln.startswith("- ")]) == 2


def test_search_fallback_without_index(tmp_taskmaster, monkeypatch):
    from taskmaster import backlog_server as bs  # noqa: PLC0415
    from taskmaster import index  # noqa: PLC0415

    monkeypatch.setattr(
        index, "open_ro", lambda bp: (_ for _ in ()).throw(FileNotFoundError()))
    bs.backlog_add_epic("e1", "Epic one", "the widget works")
    bs.backlog_add_phase("dev", "Development")
    bs.backlog_add_task("Widget frobnicator", "e1", phase="dev")
    out = bs.backlog_search("frobnicator")
    assert "— Widget frobnicator (medium, e1, todo)" in out


def test_fallback_is_used_when_the_index_errors(indexed_server, monkeypatch):
    """A broken index must not surface an error — the substring scan answers instead."""
    import sqlite3  # noqa: PLC0415

    from taskmaster import index  # noqa: PLC0415

    monkeypatch.setattr(
        index, "open_ro", lambda bp: (_ for _ in ()).throw(sqlite3.OperationalError("boom")))
    out = indexed_server.backlog_search("usage")
    assert "Error" not in out
    assert "eng-001" in out
    assert "B-001" not in out  # fallback is tasks-only, by design


def test_search_sees_out_of_band_edits_without_another_tool_call(indexed_server, tmp_taskmaster):
    """`backlog_search` calls `_load()`, which refreshes the index, so a file edit is visible."""
    import os  # noqa: PLC0415

    bug = tmp_taskmaster / ".taskmaster" / "bugs" / "B-001.md"
    bug.write_text(
        bug.read_text(encoding="utf-8").replace(
            "title: Usage rows are double counted", "title: Quokkasaurus rows are double counted"),
        encoding="utf-8")
    stamp = os.path.getmtime(bug) + 5
    os.utime(bug, (stamp, stamp))

    out = indexed_server.backlog_search("Quokkasaurus")
    assert "- `B-001` — Quokkasaurus rows are double counted (bug, open)" in out


def test_archived_entities_still_appear(indexed_server, tmp_taskmaster):
    """Ruling: archived items stay in results — the old scan included them and status is shown."""
    import os  # noqa: PLC0415

    backlog = tmp_taskmaster / ".taskmaster" / "backlog.yaml"  # eng-002 is the only `status: done`
    backlog.write_text(
        backlog.read_text(encoding="utf-8").replace("status: done", "status: archived"),
        encoding="utf-8")
    stamp = os.path.getmtime(backlog) + 5
    os.utime(backlog, (stamp, stamp))

    out = indexed_server.backlog_search("usage")
    assert "- `eng-002` — Seed the usage table (medium, eng, archived)" in out


def test_search_finds_a_task_by_branch_via_the_index(indexed_server):
    """Branch names are in the FTS body now; a non-task result proves this is not the fallback."""
    out = indexed_server.backlog_search("quokka-rework")
    assert "- `eng-001` — Rework model usage accounting (high, eng, in-progress)" in out
    assert out.startswith("**1 match** for `quokka-rework`:")

    docs_hit = indexed_server.backlog_search("usage-rework-spec")
    assert "eng-001" in docs_hit


def test_kinds_accepts_a_bare_string(indexed_server):
    out = indexed_server.backlog_search("usage", kinds="bug")
    assert "B-001" in out and "B-002" in out
    assert "eng-001" not in out and "ISS-001" not in out
