# User intent: prove the read-only SQL door onto the derived index cannot be turned into a
# write door — the guard rejects writes, PRAGMA, ATTACH and stacked statements, the authorizer
# is defense in depth, and the tool renders a capped table with a schema hint on errors.
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
    """`tmp_taskmaster` with the index fixture copied in and the index built."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415
    from taskmaster.index import build_index  # noqa: PLC0415

    shutil.copytree(FIXTURE_SRC, tmp_taskmaster / ".taskmaster", dirs_exist_ok=True)
    build_index(bs._backlog_path())
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


def test_authorizer_blocks_sqlite_master_writes_and_functions(indexed_server):
    # a SELECT that calls a write-capable function must still fail under the authorizer
    out = indexed_server.backlog_query("SELECT writefile('x.txt','y')")
    assert out.startswith("Error:")


def test_limit_clamped(indexed_server):
    assert "rows" in indexed_server.backlog_query("SELECT 1", limit=10_000)


def test_docstring_examples_run(indexed_server):
    """The three queries advertised in the tool description must actually work."""
    bugs = indexed_server.backlog_query(
        "SELECT id,status,title FROM entities WHERE kind='bug' AND status IN ('open','adopted')")
    assert "B-001" in bugs

    paths = indexed_server.backlog_query(
        "SELECT e.id,e.kind,e.status,e.title FROM entity_paths p "
        "JOIN entities e ON e.id=p.entity_id WHERE p.path LIKE '%model.py'")
    assert "B-001" in paths and not paths.startswith("Error:")

    # FTS5 needs its shadow tables and an internal `PRAGMA data_version`.
    fts = indexed_server.backlog_query(
        "SELECT id,title FROM entity_fts WHERE entity_fts MATCH 'usage' "
        "ORDER BY bm25(entity_fts) LIMIT 10")
    assert not fts.startswith("Error:") and "0 rows" not in fts


def test_authorizer_denies_unknown_table_and_write_pragma():
    import sqlite3

    from taskmaster.query_guard import authorizer

    assert authorizer(sqlite3.SQLITE_READ, "entities", "id", "main", None) == sqlite3.SQLITE_OK
    assert authorizer(sqlite3.SQLITE_READ, "secrets", "v", "main", None) == sqlite3.SQLITE_DENY
    assert authorizer(sqlite3.SQLITE_INSERT, "entities", None, "main", None) == sqlite3.SQLITE_DENY
    assert authorizer(sqlite3.SQLITE_ATTACH, "other.db", None, None, None) == sqlite3.SQLITE_DENY
    assert authorizer(sqlite3.SQLITE_PRAGMA, "journal_mode", "delete", "main", None) == sqlite3.SQLITE_DENY
    assert authorizer(sqlite3.SQLITE_PRAGMA, "data_version", None, "main", None) == sqlite3.SQLITE_OK


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


def test_query_builds_the_index_when_missing(indexed_server):
    from taskmaster import index  # noqa: PLC0415

    db = index.db_path(indexed_server._backlog_path())
    db.unlink()
    assert not db.exists()
    out = indexed_server.backlog_query("SELECT id FROM entities ORDER BY id", limit=5)
    assert "B-001" in out and db.exists()


def test_long_cells_are_truncated(indexed_server):
    out = indexed_server.backlog_query("SELECT hex(zeroblob(200)) AS wide")
    cell = out.splitlines()[1]
    assert len(cell) == 80
