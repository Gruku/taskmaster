"""Explicit SQL compatibility snapshots never widen access to private tables."""
from contextlib import closing
import sqlite3

import pytest

from taskmaster.native.migrate import backfill
from taskmaster.native.queries import Repository
from test_native_migration import legacy  # noqa: F401


def test_public_sql_entity_history_fts_and_queue_contract(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            result = query.sql("SELECT kind,id,json_extract(doc,'$.title'),rev FROM entities ORDER BY kind,id")
            assert result["rows"] == connection.execute("SELECT kind,id,json_extract(doc,'$.title'),rev FROM entities ORDER BY kind,id").fetchall()
            assert result["materialized_rows"]["entities"] == 13
            assert query.sql("SELECT id FROM entity_fts WHERE entity_fts MATCH 'run'")["rows"] == [("same",)]
            assert query.sql("SELECT COUNT(*) FROM changes")["rows"] == [(13,)]
            assert query.sql("SELECT claimed_by FROM linear_queue")["rows"] == [("peer",)]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM entity_core", "SELECT * FROM command_receipts", "SELECT * FROM projection_base",
    "WITH command_receipts AS (SELECT 1) SELECT * FROM main.command_receipts",
    "WITH entities AS (SELECT * FROM entity_core) SELECT * FROM entities",
    "DELETE FROM entities", "SELECT load_extension('missing')",
])
def test_sql_compatibility_does_not_expose_native_private_tables(legacy, sql):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            with pytest.raises((ValueError, sqlite3.DatabaseError)):
                query.sql(sql)


def test_sql_row_cap_and_execution_deadline(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            assert len(query.sql("SELECT * FROM entities", limit=2)["rows"]) == 2
            with pytest.raises(sqlite3.OperationalError, match="interrupt"):
                query.sql("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT SUM(x) FROM c", timeout=0.001)
