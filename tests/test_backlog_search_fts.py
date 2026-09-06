# User intent: prove backlog_search reaches every entity kind through the store's FTS5 table,
# filters by kind, survives punctuation in the query, and still answers from the substring scan
# when the store is unreadable — so search never regresses into an error.
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
    """`tmp_taskmaster` with the fixture projection adopted into the store."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    shutil.copytree(FIXTURE_SRC, tmp_taskmaster / ".taskmaster", dirs_exist_ok=True)
    bs._load()
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


def test_search_fallback_when_the_store_is_unreadable(tmp_taskmaster, monkeypatch):
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bs.backlog_add_epic("e1", "Epic one", "the widget works")
    bs.backlog_add_phase("dev", "Development")
    bs.backlog_add_task("Widget frobnicator", "e1", phase="dev")
    monkeypatch.setattr(bs, "_search_via_index", lambda query, kinds: None)
    out = bs.backlog_search("frobnicator")
    assert "— Widget frobnicator (medium, e1, todo)" in out


def test_fallback_is_used_when_the_fts_query_errors(indexed_server, monkeypatch):
    """A broken FTS read must not surface an error — the substring scan answers instead."""
    import sqlite3  # noqa: PLC0415

    real_store = indexed_server._store

    class BrokenConnection:
        # A real connection always reports its transaction state; the double has
        # to as well, now that the FTS path opens its own read snapshot.
        in_transaction = False

        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("boom")

    class BrokenStore:
        connection = BrokenConnection()

    # Only the FTS path is broken; `_load()` still runs against the real store,
    # which is what the substring fallback reads.
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        return BrokenStore() if calls["n"] > 1 else real_store()

    indexed_server._load()
    monkeypatch.setattr(indexed_server, "_store", flaky)
    out = indexed_server.backlog_search("usage")
    assert "Error" not in out
    assert "eng-001" in out
    assert "B-001" not in out  # fallback is tasks-only, by design


def test_search_sees_out_of_band_edits_without_another_tool_call(indexed_server, tmp_taskmaster):
    """`backlog_search` calls `_load()`, which adopts the edited file, so it is visible."""
    import os  # noqa: PLC0415

    bug = tmp_taskmaster / ".taskmaster" / "bugs" / "B-001.md"
    bug.write_text(
        bug.read_text(encoding="utf-8").replace(
            "title: Usage rows are double counted", "title: Quokkasaurus rows are double counted"),
        encoding="utf-8")
    stamp = os.path.getmtime(bug) + 5
    os.utime(bug, (stamp, stamp))

    # The store imports hand edits at most once every two seconds on a read path;
    # the fixture's own load just consumed that window.
    indexed_server._store().force_scan_on_next_read()
    out = indexed_server.backlog_search("Quokkasaurus")
    assert "- `B-001` — Quokkasaurus rows are double counted (bug, open)" in out


def test_archived_entities_still_appear(indexed_server):
    """Ruling: archived items stay in results — the old scan included them and status is shown."""
    indexed_server.backlog_archive_task("eng-002")  # the fixture's only `status: done` task
    out = indexed_server.backlog_search("usage")
    assert "- `eng-002` — Seed the usage table (medium, eng, archived)" in out


def test_search_finds_a_task_by_branch_via_the_store(indexed_server):
    """Branch names are in the FTS body; the header count proves this is not the fallback."""
    out = indexed_server.backlog_search("quokka-rework")
    assert "- `eng-001` — Rework model usage accounting (high, eng, in-progress)" in out
    assert out.startswith("**1 match** for `quokka-rework`:")

    docs_hit = indexed_server.backlog_search("usage-rework-spec")
    assert "eng-001" in docs_hit


def test_kinds_accepts_a_bare_string(indexed_server):
    out = indexed_server.backlog_search("usage", kinds="bug")
    assert "B-001" in out and "B-002" in out
    assert "eng-001" not in out and "ISS-001" not in out


def test_the_count_and_the_rows_come_from_one_snapshot(indexed_server):
    """`_load()` releases its snapshot before the FTS queries run, so a commit
    landing between the count and the result query answered "1 match" above an
    empty list. Both statements have to sit inside one read transaction."""
    con = indexed_server._store().connection
    statements: list[str] = []
    con.set_trace_callback(statements.append)
    try:
        out = indexed_server.backlog_search("usage")
    finally:
        con.set_trace_callback(None)
    assert "eng-001" in out

    upper = [s.strip().upper() for s in statements]
    count = next(i for i, s in enumerate(upper) if s.startswith("SELECT COUNT(*)"))
    rows = next(i for i, s in enumerate(upper) if "BM25(ENTITY_FTS)" in s)
    assert count < rows, statements
    # An open snapshot at the count: the last transaction statement before it
    # is a BEGIN, not the COMMIT that ended `_load()`'s own transaction.
    opened = [
        i for i, s in enumerate(upper[:count])
        if s.startswith(("BEGIN", "COMMIT", "ROLLBACK"))
    ]
    assert opened and upper[opened[-1]].startswith("BEGIN"), statements
    # …and it is still open at the row query.
    between = upper[count:rows]
    assert not any(s.startswith(("COMMIT", "ROLLBACK")) for s in between), statements
    assert not con.in_transaction, "the read snapshot outlived the call"
