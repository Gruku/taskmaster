"""Native queries are bounded by requested rows/fields and share snapshots."""
from contextlib import closing
import json
import sqlite3

import pytest

from taskmaster.native.migrate import backfill, legacy_entities
from taskmaster.native.queries import Repository, CursorInvalid
from test_native_migration import legacy  # noqa: F401


def test_targeted_details_match_every_kind_without_legacy_reads(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        expected = legacy_entities(connection)
        backfill(connection)
        statements = []
        connection.set_trace_callback(statements.append)
        with Repository(connection).snapshot() as query:
            for row in expected:
                actual = query.get(row["kind"], row["id"], include_body=True, include_deleted=True)
                assert actual["fields"] == row["doc"]
                assert actual["revision"] == row["rev"]
                assert actual["body"] == row["body"]
        assert not any("FROM entities" in sql or "FROM changes" in sql for sql in statements)


def test_bounded_list_does_not_read_documents_or_unrequested_extensions(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        statements = []
        connection.set_trace_callback(statements.append)
        with Repository(connection).snapshot() as query:
            result = query.list("task", fields=["id", "title", "status", "epic"], limit=1)
        assert result["items"][0]["fields"] == {"id": "same", "title": None, "status": "todo", "epic": "missing"}
        assert not any("entity_documents" in sql or "entity_extensions" in sql for sql in statements)
        assert any("LIMIT 2" in sql for sql in statements)


def test_cursor_scope_and_generation_invalidation(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            first = query.list(limit=3, include_archived=True, include_deleted=True)
            assert len(first["items"]) == 3 and first["cursor"]
            second = query.list(limit=3, include_archived=True, include_deleted=True, cursor=first["cursor"])
            assert not ({(i["kind"], i["id"]) for i in first["items"]} & {(i["kind"], i["id"]) for i in second["items"]})
            with pytest.raises(CursorInvalid):
                query.list("task", cursor=first["cursor"])
        connection.execute("UPDATE entities SET doc=json_set(doc,'$.custom','changed') WHERE kind='task'")
        backfill(connection)
        with Repository(connection).snapshot() as query:
            with pytest.raises(CursorInvalid):
                query.list(limit=3, include_archived=True, include_deleted=True, cursor=first["cursor"])


def test_search_matches_legacy_ranking_and_limits(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        expected = connection.execute("SELECT kind,id,bm25(entity_fts) FROM entity_fts WHERE entity_fts MATCH 'run' ORDER BY bm25(entity_fts)").fetchall()
        backfill(connection)
        with Repository(connection).snapshot() as query:
            found = query.search("run", limit=1)
        assert [(r["kind"], r["id"], r["rank"]) for r in found["items"]] == expected


def test_relations_preserve_order_duplicates_and_unresolved_refs(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            refs = query.relations("task", "same", field="depends_on", limit=10)["items"]
            assert [r["value"] for r in refs] == ["same", "missing", "same"]
            assert [r["resolved"] for r in refs] == [True, False, True]
            inverse = query.references_to("task", "same", limit=10)["items"]
            assert any(r["kind"] == "handover" for r in inverse)


def test_summaries_apply_lifecycle_and_scope_in_sql(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            assert query.summary("task", epic="missing")["total"] == 1
            assert query.summary("task", epic="other")["total"] == 0
            assert query.summary("area")["total"] == 0
            assert query.summary("bug")["total"] == 0


@pytest.mark.parametrize("bad", [-1, 0, 501, True, "1"])
def test_native_page_bounds_are_explicit(legacy, bad):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            with pytest.raises(ValueError, match="limit"):
                query.list(limit=bad)


def test_repository_cannot_escape_snapshot_lifetime(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            assert query.get("task", "same")
        with pytest.raises(RuntimeError, match="snapshot"):
            query.get("task", "same")


def test_requested_metadata_columns_do_not_load_unrequested_core_fields(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        statements = []
        connection.set_trace_callback(statements.append)
        with Repository(connection).snapshot() as query:
            query.get("task", "same", fields=["id"])
            query.list("task", fields=["id"], limit=1)
        selects = [sql for sql in statements if "FROM entity_core" in sql]
        assert not any("SELECT *" in sql or "SELECT c.*" in sql or "title_json" in sql for sql in selects)
