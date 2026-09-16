"""Backfill is lossless, repeatable, fenced, and atomic on failure."""
from contextlib import closing
import json
import sqlite3

import pytest

from taskmaster import store
from taskmaster.admission import UnsupportedStoreError, assert_compatible
from taskmaster.native import migrate
from taskmaster.native.db import verified_snapshot


@pytest.fixture
def legacy(tmp_path):
    path = tmp_path / "store.db"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(store.SCHEMA_SQL)
        connection.executemany("INSERT INTO meta VALUES(?,?)", [("schema_version", "1"), ("creation_token", "fixture-store")])
        docs = {
            "task": {"id": "same", "title": None, "status": "todo", "priority": 1, "epic": "missing", "phase": None,
                     "anchors": "src/*.py", "location": ["src/a.py", "src/a.py"], "depends_on": ["same", "missing", "same"],
                     "links": [{"type": "relates_to", "target": "missing", "note": "exact"}], "bundle": [], "area": None,
                     "order": 1.5, "human_action": False, "body": "authored body key", "rev": "authored rev", "custom": {"x": [None, True, 3.5]}},
            "epic": {"id": "same", "name": "Epic", "title": "Distinct title", "phase": "P-1", "components": ["A", "A"]},
            "phase": {"id": "P-1", "name": "Phase", "start_date": "2026-09-12", "target_date": None},
            "handover": {"id": "H-1", "task_ids": ["same", "missing"], "kind": "session", "date": "2026-09-12", "thread": None},
            "issue": {"id": "I-1", "severity": None, "related_tasks": "same", "fixed_in_task": None},
            "bug": {"id": "B-1", "severity": "P1", "adopted_into": ["same"], "archived": None},
            "decision": {"id": "D-1", "task_id": "same", "resolved_in": None, "resolved_with": False},
            "idea": {"id": "IDEA-1", "related_issues": ["I-1"], "tags": ["one", None, {"unknown": True}]},
            "note": {"id": "N-1", "author": "author", "pinned": True},
            "area": {"id": "A-1", "custom": {"nested": "value"}},
            "tracker": {"id": "TR-1", "external_system": "linear", "instance_alias": "primary", "push_hash": None},
            "backlog": {"meta": {"schema_version": 4}, "threads": {"t": {"custom": 1}}},
            "project": {"conventions": {"arbitrary": [True, None]}}
        }
        for seq, (kind, doc) in enumerate(docs.items(), 1):
            ident = doc.get("id", kind)
            connection.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?)",
                (kind, ident, doc.get("epic"), doc.get("status"), int(kind == "bug"), int(kind == "area"),
                 json.dumps(doc), "Exact prose\r\n\n雪\n" if kind == "task" else None, seq + 3, seq))
            connection.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?,?,?,?)",
                (seq, "2026-09-12", "session", "fixture", kind, ident, "create", "[]", "{}", json.dumps(doc)))
        connection.execute("INSERT INTO changes(seq,ts,session,tool,kind,id,op) VALUES(100,'date','s','t','task','gone','delete')")
        connection.execute("DELETE FROM changes WHERE seq=100")  # AUTOINCREMENT must not recycle this.
        connection.execute("INSERT INTO projection(file,kind,id,content_hash,dirty,quarantined,exported_seq) VALUES('tasks/same.md','task','same','hash',1,1,4)")
        connection.execute("INSERT INTO projection_base VALUES('tasks/same.md',?)", (b"exact\r\nbase",))
        connection.execute("INSERT INTO linear_queue(seq,op,state,attempts,claimed_by) VALUES(9,'push','claimed',2,'peer')")
        connection.execute("INSERT INTO sessions(session,pid) VALUES('peer',123)")
        connection.execute("INSERT INTO entity_fts VALUES('task','same','Running task','Exact searchable prose')")
    (tmp_path / "id-reservations.json").write_bytes(b'{"task":["never-reuse"]}\n')
    (tmp_path / "export-intent.peer.json").write_bytes(b'{"entries":{}}\n')
    return path


def legacy_state(connection):
    return {name: [tuple(r) for r in connection.execute(f'SELECT * FROM "{name}"')]
            for name in ("entities", "changes", "meta", "projection", "projection_base", "linear_queue", "sessions")}


def test_backfill_preserves_all_fields_history_and_local_authority(legacy):
    files = {p.name: p.read_bytes() for p in legacy.parent.glob("*.json")}
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        before = legacy_state(connection)
        report = migrate.backfill(connection)
        assert report["entities"] == 13
        assert report["event_high_water"] == 100
        assert report["state"] == "verified"
        assert legacy_state(connection) == before
        assert_compatible(connection)  # This is staging, not native activation.
        with verified_snapshot(connection):
            assert migrate.reconstruct_entities(connection) == migrate.legacy_entities(connection)
        assert connection.execute("SELECT seq FROM sqlite_sequence WHERE name='domain_events'").fetchone()[0] == 100
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM dependencies WHERE target_key IS NULL").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM path_claims").fetchone()[0] == 3
    assert {p.name: p.read_bytes() for p in legacy.parent.glob("*.json")} == files


@pytest.mark.parametrize("stage", migrate.STAGES)
def test_every_interruption_rolls_back_schema_and_data(legacy, stage):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        before = legacy_state(connection)
        def fail(current):
            if current == stage:
                raise RuntimeError("injected interruption")
        with pytest.raises(RuntimeError, match="injected"):
            migrate.backfill(connection, checkpoint=fail)
        assert not connection.in_transaction
        assert legacy_state(connection) == before
        assert not connection.execute("SELECT name FROM sqlite_schema WHERE name='entity_core'").fetchall()
        assert migrate.backfill(connection)["state"] == "verified"


def test_repeat_backfill_keeps_keys_and_rejects_stale_snapshots(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        migrate.backfill(connection)
        keys = connection.execute("SELECT kind,public_id,entity_key FROM entity_core ORDER BY entity_key").fetchall()
        migrate.backfill(connection)
        assert connection.execute("SELECT kind,public_id,entity_key FROM entity_core ORDER BY entity_key").fetchall() == keys
        with closing(sqlite3.connect(legacy, isolation_level=None)) as peer:
            with verified_snapshot(connection):
                peer.execute("UPDATE entities SET doc=json_set(doc,'$.title','peer') WHERE kind='task'")
                assert migrate.reconstruct_entities(connection) == migrate.legacy_entities(connection)
        with pytest.raises(UnsupportedStoreError, match="stale"):
            with verified_snapshot(connection):
                pass
        migrate.backfill(connection)
        with verified_snapshot(connection):
            assert migrate.reconstruct_entities(connection) == migrate.legacy_entities(connection)


@pytest.mark.parametrize("marker,value", [("schema_version", "99"), ("minimum_client_protocol", "99"), ("migration_state", "migrating")])
def test_backfill_refuses_incompatible_authority(legacy, marker, value):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        connection.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (marker, value))
        before = legacy_state(connection)
        with pytest.raises(UnsupportedStoreError):
            migrate.backfill(connection)
        assert legacy_state(connection) == before
        assert not connection.execute("SELECT name FROM sqlite_schema WHERE name='entity_core'").fetchall()


def test_nested_transaction_is_rejected_without_rolling_back_caller(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        connection.execute("BEGIN")
        with pytest.raises(RuntimeError, match="transaction"):
            migrate.backfill(connection)
        assert connection.in_transaction


def test_failed_refresh_retains_previous_verified_snapshot(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        migrate.backfill(connection)
        before = migrate.reconstruct_entities(connection)
        def fail(stage):
            if stage == "history":
                raise KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            migrate.backfill(connection, checkpoint=fail)
        with verified_snapshot(connection):
            assert migrate.reconstruct_entities(connection) == before


def test_equivalence_failure_never_publishes_verified_state(legacy, monkeypatch):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        monkeypatch.setattr(migrate, "reconstruct_entities", lambda connection: [])
        with pytest.raises(ValueError, match="equivalence"):
            migrate.backfill(connection)
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='native_manifest'").fetchone()


def test_unknown_staging_schema_is_never_rebuilt(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        migrate.backfill(connection)
        connection.execute("UPDATE native_manifest SET value='99' WHERE key='schema_version'")
        before = list(connection.iterdump())
        with pytest.raises(UnsupportedStoreError, match="staging schema"):
            migrate.backfill(connection)
        assert list(connection.iterdump()) == before


def test_unresolved_memberships_retain_their_declared_target_kind(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        connection.execute("UPDATE entities SET doc=json_set(doc,'$.promoted_to','ISS-missing') WHERE kind='bug'")
        connection.execute("UPDATE entities SET doc=json_set(doc,'$.duplicate_of','ISS-missing') WHERE kind='issue'")
        migrate.backfill(connection)
        assert connection.execute("SELECT target_kind,target_id,target_key FROM memberships WHERE field IN ('promoted_to','duplicate_of')").fetchall() == [
            ("issue", "ISS-missing", None), ("issue", "ISS-missing", None)]


def test_prior_staging_schema_upgrades_additively_without_changing_keys(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill = migrate.backfill
        backfill(connection)
        keys = connection.execute("SELECT kind,public_id,entity_key FROM entity_core ORDER BY entity_key").fetchall()
        for table in ("id_reservations", "id_counters", "external_documents"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("UPDATE native_manifest SET value='1' WHERE key='schema_version'")
        def interrupted(stage):
            if stage == "schema":
                raise RuntimeError("interrupted upgrade")
        with pytest.raises(RuntimeError, match="interrupted upgrade"):
            backfill(connection, checkpoint=interrupted)
        assert connection.execute("SELECT value FROM native_manifest WHERE key='schema_version'").fetchone()[0] == "1"
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='id_counters'").fetchone()
        assert backfill(connection)["state"] == "verified"
        assert connection.execute("SELECT kind,public_id,entity_key FROM entity_core ORDER BY entity_key").fetchall() == keys
        assert connection.execute("SELECT COUNT(*) FROM id_counters").fetchone()[0] == 0


@pytest.mark.parametrize("stage", migrate.STAGES)
def test_process_death_rolls_back_uncommitted_backfill(legacy, stage):
    import os
    from pathlib import Path
    import subprocess
    import sys
    script = """
import os,sqlite3,sys
from taskmaster.native.migrate import backfill
connection=sqlite3.connect(sys.argv[1],isolation_level=None)
def checkpoint(stage):
    if stage==sys.argv[2]:
        os._exit(37)
backfill(connection,checkpoint=checkpoint)
"""
    result = subprocess.run([sys.executable, "-c", script, str(legacy), stage],
                            env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])), timeout=30)
    assert result.returncode == 37
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='native_manifest'").fetchone()
        assert connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 13
        assert migrate.backfill(connection)["state"] == "verified"
