# User intent: prove the read-only SQL door onto the backlog store cannot be turned into a
# write door — the guard rejects writes, PRAGMA, ATTACH and stacked statements, the authorizer
# is defense in depth, the reader never takes a write lock or outlives its call, and the tool
# renders a capped table with a schema hint on errors.
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster.query_guard import validate  # noqa: E402

FIXTURE_SRC = PLUGIN_ROOT / "tests" / "fixtures" / "index_backlog" / ".taskmaster"


@pytest.fixture()
def indexed_server(tmp_taskmaster):
    """`tmp_taskmaster` with the fixture projection adopted into `local/store.db`."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    shutil.copytree(FIXTURE_SRC, tmp_taskmaster / ".taskmaster", dirs_exist_ok=True)
    bs._load()
    return bs


@pytest.mark.parametrize("bad", ["DELETE FROM entities", "PRAGMA user_version", "ATTACH 'x' AS y",
                                 "SELECT 1; SELECT 2",
                                 "/* hi */ UPDATE entities SET status='x'",
                                 "WITH t AS (SELECT 1) DELETE FROM entities", ""])
def test_guard_rejects(bad):
    with pytest.raises(ValueError):
        validate(bad)


def test_guard_accepts_select_and_with_and_trailing_semicolon():
    assert validate("select 1;").lower().startswith("select")
    assert validate("WITH t AS (SELECT 1) SELECT * FROM t")


def test_guard_allows_semicolon_inside_string():
    assert validate("SELECT * FROM entities WHERE title='a;b'")


def test_query_returns_table_and_caps(indexed_server):
    out = indexed_server.backlog_query("SELECT id FROM entities ORDER BY id", limit=2)
    assert out.count("\n") >= 3 and "2 rows (capped)" in out


def test_query_error_includes_schema(indexed_server):
    out = indexed_server.backlog_query("SELECT nope FROM entities")
    assert out.startswith("Error:") and "entity_paths(" in out


def test_write_capable_function_is_unavailable(indexed_server):
    # a SELECT that calls a write-capable function must still fail under the authorizer
    out = indexed_server.backlog_query("SELECT writefile('x.txt','y')")
    assert out.startswith("Error:")


def test_authorizer_is_the_thing_stopping_off_store_reads(indexed_server):
    """Would fail if the authorizer were removed: both queries are valid read-only SQL."""
    allowed = indexed_server.backlog_query("SELECT count(*) AS n FROM sqlite_master")
    assert not allowed.startswith("Error:")

    denied = indexed_server.backlog_query("SELECT count(*) AS n FROM pragma_table_info('entities')")
    assert denied.startswith("Error: not authorized: READ pragma_table_info")


def test_limit_clamped(indexed_server):
    assert "rows" in indexed_server.backlog_query("SELECT 1", limit=10_000)


def test_docstring_examples_run(indexed_server):
    """The three queries advertised in the tool description must actually work."""
    bugs = indexed_server.backlog_query(
        "SELECT id,status,json_extract(doc,'$.title') AS title FROM entities "
        "WHERE kind='bug' AND deleted=0 AND status IN ('open','adopted')")
    assert "B-001" in bugs and "Usage rows are double counted" in bugs

    paths = indexed_server.backlog_query(
        "SELECT e.kind,e.id,e.status FROM entity_paths p "
        "JOIN entities e ON e.kind=p.kind AND e.id=p.id WHERE p.path LIKE '%model.py'")
    assert "B-001" in paths and not paths.startswith("Error:")

    # FTS5 needs its shadow tables and an internal `PRAGMA data_version`.
    fts = indexed_server.backlog_query(
        "SELECT id,title FROM entity_fts WHERE entity_fts MATCH 'usage' "
        "ORDER BY bm25(entity_fts) LIMIT 10")
    assert not fts.startswith("Error:") and "0 rows" not in fts


def test_authorizer_denies_unknown_table_and_write_pragma():
    import sqlite3

    from taskmaster.query_guard import Authorizer

    az = Authorizer()
    assert az(sqlite3.SQLITE_READ, "entities", "id", "main", None) == sqlite3.SQLITE_OK
    assert az(sqlite3.SQLITE_RECURSIVE, None, None, None, None) == sqlite3.SQLITE_OK
    assert az(sqlite3.SQLITE_PRAGMA, "data_version", None, "main", None) == sqlite3.SQLITE_OK
    assert az.denial is None

    for action, arg1, arg2 in ((sqlite3.SQLITE_READ, "secrets", "v"),
                               (sqlite3.SQLITE_INSERT, "entities", None),
                               (sqlite3.SQLITE_ATTACH, "other.db", None),
                               (sqlite3.SQLITE_PRAGMA, "journal_mode", "delete")):
        assert Authorizer()(action, arg1, arg2, "main", None) == sqlite3.SQLITE_DENY


def test_authorizer_records_the_first_denial_by_name():
    import sqlite3

    from taskmaster.query_guard import Authorizer

    az = Authorizer()
    az(sqlite3.SQLITE_INSERT, "entities", None, "main", None)
    az(sqlite3.SQLITE_DROP_TABLE, "entities", None, "main", None)
    assert az.denial == "INSERT entities"  # first denial wins, action named


def test_recursive_cte_is_allowed(indexed_server):
    out = indexed_server.backlog_query(
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<5) SELECT * FROM c")
    assert not out.startswith("Error:")
    assert "5 rows" in out


def test_aggregate_over_a_cte_is_allowed(indexed_server):
    """`count(*)` over a CTE issues SQLITE_READ on the CTE's own name."""
    out = indexed_server.backlog_query(
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<5) "
        "SELECT count(*) AS n FROM c")
    assert not out.startswith("Error:") and "5" in out

    linked = indexed_server.backlog_query(
        "WITH RECURSIVE reach(id) AS (SELECT 'B-001' UNION SELECT l.dst_id FROM links l "
        "JOIN reach r ON l.src_id=r.id) SELECT count(*) AS n FROM reach")
    assert not linked.startswith("Error:")


def test_declared_names_finds_cte_names_and_ignores_strings():
    from taskmaster.query_guard import declared_names  # noqa: PLC0415

    assert declared_names("WITH a AS (SELECT 1), b(x) AS (SELECT 2) SELECT * FROM a") == {"a", "b"}
    assert declared_names("SELECT 'q AS (' AS lit") == frozenset()
    assert declared_names("SELECT 1") == frozenset()


def test_cte_named_after_a_forbidden_object_cannot_reach_it(indexed_server):
    """A CTE name shadows any schema object, so declaring one reaches nothing new."""
    out = indexed_server.backlog_query(
        "WITH pragma_table_info AS (SELECT 42 AS answer) SELECT * FROM pragma_table_info")
    assert not out.startswith("Error:") and "42" in out

    real = indexed_server.backlog_query("SELECT count(*) AS n FROM main.pragma_table_info")
    assert real.startswith("Error:")


def test_query_timeout_aborts_a_runaway_query(indexed_server, monkeypatch):
    """A recursive CTE with 50M steps must be cut off, not run to completion."""
    from taskmaster import query_guard  # noqa: PLC0415

    monkeypatch.setattr(query_guard, "QUERY_TIMEOUT_S", 0.01)
    out = indexed_server.backlog_query(
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<50000000) "
        "SELECT count(*) FROM c")
    assert out.startswith("Error: query exceeded 0.01 s")
    assert "entity_paths(" in out


def test_default_timeout_is_five_seconds():
    """Pins the production wording of the timeout error: `query exceeded 5 s`."""
    from taskmaster.query_guard import QUERY_TIMEOUT_S, Deadline

    assert QUERY_TIMEOUT_S == 5.0
    assert Deadline(QUERY_TIMEOUT_S).message == "query exceeded 5 s"


def test_deadline_aborts_only_after_it_expires():
    from taskmaster.query_guard import Deadline

    live = Deadline(60.0)
    assert live() == 0 and not live.expired

    dead = Deadline(0.0)
    assert dead() == 1 and dead.expired


@pytest.mark.parametrize("bad", ["SELECT name FROM pragma_table_info('entities')",
                                 "SELECT load_extension('x')"])
def test_tool_rejects_authorizer_bypasses(indexed_server, bad):
    """Statements the text guard cannot see through are still stopped by the authorizer."""
    assert indexed_server.backlog_query(bad).startswith("Error:")


def test_guard_masks_block_comment_and_line_comment_smuggling():
    for smuggled in ("select 1 -- x\n; DROP TABLE entities",
                     "SELECT 1 /* ok */ UNION /* */ DELETE FROM entities",
                     "SELECT/*x*/1;SELECT 2"):
        with pytest.raises(ValueError):
            validate(smuggled)


def test_guard_keeps_the_query_it_returns():
    assert validate("  SELECT 1  ;  ") == "SELECT 1"


def test_query_answers_from_a_cold_store(tmp_taskmaster):
    """No warm-up call: the query itself opens the store and adopts the projection."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    shutil.copytree(FIXTURE_SRC, tmp_taskmaster / ".taskmaster", dirs_exist_ok=True)
    out = bs.backlog_query("SELECT id FROM entities ORDER BY id", limit=5)
    assert "B-001" in out and not out.startswith("Error:")
    assert (tmp_taskmaster / ".taskmaster" / "local" / "store.db").exists()


def test_allowlist_matches_the_live_store_schema(indexed_server):
    """Every readable table must exist, and the store's private ones must stay out."""
    from taskmaster.query_guard import TABLES  # noqa: PLC0415

    live = {row[0] for row in indexed_server._store().connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    assert set(TABLES) <= live, set(TABLES) - live
    assert live - set(TABLES) >= {"meta", "projection_base"}
    for table in ("meta", "projection_base"):
        assert indexed_server.backlog_query(
            f"SELECT count(*) FROM {table}").startswith("Error: not authorized:")


def test_reader_leaves_no_transaction_and_takes_no_write_lock(indexed_server):
    """Spec §3.1: a read holds no cursor and no snapshot across tool calls."""
    import sqlite3  # noqa: PLC0415

    con = indexed_server._store().connection
    out = indexed_server.backlog_query("SELECT id FROM entities ORDER BY id")
    assert not out.startswith("Error:")
    assert not con.in_transaction

    # A concurrent writer must be able to take the write lock during a query, so
    # the query's own BEGIN has to be deferred rather than immediate.
    writer = sqlite3.connect(indexed_server._store().db_path, timeout=1.0)
    try:
        writer.execute("BEGIN IMMEDIATE")
        assert not indexed_server.backlog_query(
            "SELECT count(*) AS n FROM entities").startswith("Error:")
    finally:
        writer.rollback()
        writer.close()


def test_a_failed_query_still_clears_the_authorizer(indexed_server):
    """A denial must not leave the shared connection unable to write afterwards."""
    assert indexed_server.backlog_query("SELECT count(*) FROM meta").startswith("Error:")
    con = indexed_server._store().connection
    assert not con.in_transaction
    indexed_server.backlog_add_phase("later", "Later")  # writes through the same connection
    assert "later" in indexed_server.backlog_query(
        "SELECT id FROM entities WHERE kind='phase'")


def test_long_cells_are_truncated(indexed_server):
    out = indexed_server.backlog_query("SELECT hex(zeroblob(200)) AS wide")
    cell = out.splitlines()[1]
    assert len(cell) == 80


def test_query_sees_a_hand_edited_file(indexed_server, tmp_taskmaster):
    """Spec §3.4: a read tool adopts hand edits. `build_index` used to do this on every query.

    The store throttles read-path imports to once every two seconds and the
    fixture's own load consumed that window, so the throttle is defeated
    explicitly rather than by sleeping.
    """
    bug = tmp_taskmaster / ".taskmaster" / "bugs" / "B-001.md"
    bug.write_text(
        bug.read_text(encoding="utf-8").replace(
            "title: Usage rows are double counted", "title: Quokkasaurus rows are counted"),
        encoding="utf-8")
    indexed_server._store().force_scan_on_next_read()

    out = indexed_server.backlog_query(
        "SELECT json_extract(doc,'$.title') AS title FROM entities WHERE id='B-001'")
    assert "Quokkasaurus" in out, out
