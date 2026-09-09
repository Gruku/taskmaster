"""Native migration oracles: keep diagnostics independent of retained FTS state."""
from contextlib import closing
import sqlite3

import pytest

from taskmaster.integrity import check_database
from taskmaster import store
from native_oracles import canonical_rows, projection_hashes


def seed(path):
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript("""
        CREATE VIRTUAL TABLE entity_fts USING fts5(kind UNINDEXED, id UNINDEXED,
            title, body, tokenize='porter unicode61');
        CREATE TABLE related(a, b, weight);
        INSERT INTO entity_fts VALUES('task','core-001','First','original document');
    """)
    return connection


@pytest.mark.parametrize("rollback", [False, True])
def test_committed_peer_fts_write_checked_on_fresh_snapshot(tmp_path, rollback):
    path = tmp_path / "store.db"
    with closing(seed(path)) as retained:
        assert retained.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        with closing(sqlite3.connect(path, isolation_level=None)) as peer:
            peer.execute("INSERT INTO entity_fts VALUES('task','core-002','Second','new document')")
        if rollback:
            retained.execute("BEGIN IMMEDIATE")
            retained.execute("INSERT INTO related VALUES('a','b',1)")
            retained.rollback()
        result = check_database(path)
        assert result["integrity"] == ["ok"]
        assert result["foreign_keys"] == []
        assert retained.execute("SELECT id FROM entity_fts WHERE entity_fts MATCH 'new'").fetchall() == [("core-002",)]


def test_real_fts_damage_is_reported_without_repair(tmp_path):
    path = tmp_path / "store.db"
    with closing(seed(path)) as connection:
        # Deliberately invalidate index/content agreement, only in this fixture.
        connection.execute("UPDATE entity_fts_content SET c3='different tokens'")
        before = connection.execute("SELECT * FROM entity_fts_content").fetchall()
        result = check_database(path)
        assert result["integrity"] != ["ok"]
        assert connection.execute("SELECT * FROM entity_fts_content").fetchall() == before


def test_diagnostics_do_not_create_missing_database(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        check_database(path)
    assert not path.exists()


def test_foreign_key_violations_are_separate_from_integrity(tmp_path):
    path = tmp_path / "store.db"
    with closing(seed(path)) as connection:
        connection.executescript("CREATE TABLE parent(id PRIMARY KEY);"
            "CREATE TABLE child(id REFERENCES parent(id)); INSERT INTO child VALUES(1);")
    result = check_database(path)
    assert result["integrity"] == ["ok"]
    assert len(result["foreign_keys"]) == 1


def test_full_derived_rebuild_matches_committed_write_and_preserves_projection(tmp_path):
    tm = tmp_path / ".taskmaster"
    tm.mkdir()
    (tm / "backlog.yaml").write_text(
        "version: 4\nmeta: {schema_version: 4}\nepics: []\nphases: []\n", encoding="utf-8")
    opened = store.open_store(backlog_path=tm)
    with opened.transaction(tool="native-baseline") as tx:
        for ident, anchor in (("core-001", "src/*.py"), ("core-002", "src/main.py")):
            tx.create("task", {"id": ident, "title": ident, "status": "todo",
                "anchors": [anchor], "custom": {"nullable": None, "values": [1, True]},
                "_body": "Exact authored prose\n\nSecond paragraph."})
    connection = opened.connection
    before = canonical_rows(connection)
    files = projection_hashes(tm)
    connection.execute("BEGIN IMMEDIATE")
    try:
        store.Store._rebuild_related(connection)
        assert canonical_rows(connection) == before
    finally:
        connection.rollback()
    assert projection_hashes(tm) == files
    assert check_database(opened.db_path)["integrity"] == ["ok"]
