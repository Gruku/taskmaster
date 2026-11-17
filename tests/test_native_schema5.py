# User intent: pin native schema 5 (domain_events.actor and entity_key): opt-in, one atomic upgrade step with the
# backfill and index inside it, refused cleanly by older builds, and fast enough at real-store scale.
from __future__ import annotations

import re
import sqlite3
import time

import pytest

from taskmaster import backlog_server as bs
from taskmaster.admission import UnsupportedStoreError
from taskmaster.native import db, schema, upgrade
from native_twins import commit_only, make_twins, native_connection
from test_native_schema_upgrade import _as_older_build, manifest_version


@pytest.fixture
def root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, lambda: bs.backlog_add_task(title='Upgrade task', epic='test-epic',
                                                                         phase='dev'), visibility=None)
    with twins.at(twins.native):
        yield twins.native


def columns(connection):
    return {row[1] for row in connection.execute("PRAGMA table_info(domain_events)")}


def test_schema_5_constants():
    assert schema.VERSION == 5 and schema.SUPPORTED == frozenset({3, 4, 5}) and schema.BASE_VERSION == 3
    assert schema.SINGLETON_KINDS == ("backlog", "project")
    listed = re.search(r"NOT IN \(([^)]*)\)", schema.SCHEMA_5_BACKFILL).group(1)
    assert tuple(item.strip().strip("'") for item in listed.split(",")) == schema.SINGLETON_KINDS


def test_new_stores_have_no_actor_columns(root):
    assert manifest_version(root) == '3'
    with native_connection(root) as connection:
        assert not {"actor", "entity_key"} & columns(connection)


def _seed_events(connection):
    a = commit_only(connection, 'task.create', {'epic': 'test-epic', 'phase': 'dev', 'title': 'A'})
    b = commit_only(connection, 'task.create', {'epic': 'test-epic', 'phase': 'dev', 'title': 'B'})
    ida, idb = (r['affected'][0]['id'] for r in (a, b))
    commit_only(connection, 'task.patch', {'id': ida, 'set': {'title': 'A2'}})
    commit_only(connection, 'task.archive', {'id': idb, 'reason': 'wont-fix'})
    commit_only(connection, 'handover.create', {'tldr': 'Threaded', 'thread': 'main'})
    commit_only(connection, 'thread.update', {'name': 'main', 'status': 'parked', 'reason': 'x'})
    commit_only(connection, 'project.set', {'document': {'schema_version': 1, 'meta': {'name': 'P', 'slug': 'p'}}})
    connection.execute("UPDATE entity_core SET deleted=1 WHERE kind='task' AND public_id=?", (idb,))
    for kind, ident, op in (('task', 'tasks/x.md', 'resolve'), ('session', 's-1', 'note')):
        connection.execute("INSERT INTO domain_events(ts,session,tool,kind,id,op) VALUES('t','s','tool',?,?,?)", (kind, ident, op))
    return ida, idb


def test_upgrade_3_to_5_adds_columns_backfills_and_indexes(root):
    with native_connection(root) as connection:
        ida, idb = _seed_events(connection)
        assert upgrade.upgrade(connection) == {"from": 3, "to": 5}
        assert manifest_version(root) == '5'
        keys = dict(connection.execute("SELECT public_id,entity_key FROM entity_core WHERE kind='task'"))
        rows = connection.execute("SELECT kind,id,entity_key,actor FROM domain_events").fetchall()
        assert all(actor is None for *_, actor in rows)
        seen = set()
        for kind, ident, key, _ in rows:
            if kind == 'task' and ident in (ida, idb):
                assert key == keys[ident]
                seen.add(ident)
            if kind in ('backlog', 'project', 'session') or ident == 'tasks/x.md':
                assert key is None
        assert seen == {ida, idb}
        assert any(r[0] == 'backlog' for r in rows) and any(r[0] == 'project' for r in rows)
        assert "ix_domain_events_entity_key" in {r[1] for r in connection.execute("PRAGMA index_list(domain_events)")}
        plan = " ".join(str(r) for r in connection.execute("EXPLAIN QUERY PLAN SELECT seq FROM domain_events WHERE entity_key=?", (1,)))
        assert "ix_domain_events_entity_key" in plan


def test_upgrade_4_to_5_runs_only_the_new_step(root):
    with native_connection(root) as connection:
        _seed_events(connection)
        assert upgrade.upgrade(connection, to=4) == {"from": 3, "to": 4}
        legacy = "SELECT seq,ts,session,tool,kind,id,op,fields,before,after,commit_key FROM domain_events"
        tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        counts = {t: connection.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        events = connection.execute(legacy).fetchall()
        assert upgrade.upgrade(connection) == {"from": 4, "to": 5}
        assert counts == {t: connection.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        assert connection.execute(legacy).fetchall() == events
        assert {"actor", "entity_key"} <= columns(connection)


def test_failed_schema_5_step_rolls_back_the_columns(root, monkeypatch):
    with native_connection(root) as connection:
        upgrade.upgrade(connection, to=4)
        monkeypatch.setattr(schema, 'SCHEMA_5_BACKFILL', "UPDATE domain_events SET no_such_column=1 WHERE seq>0")
        with pytest.raises(sqlite3.OperationalError):
            upgrade.upgrade(connection)
        assert manifest_version(root) == '4'
        assert not {"actor", "entity_key"} & columns(connection)
        assert not connection.in_transaction


def test_actor_column_rejects_non_json(root):
    with native_connection(root) as connection:
        upgrade.upgrade(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO domain_events(ts,session,tool,kind,id,op,actor) VALUES('t','s','t','task','x','o','not json')")


def test_schema_5_store_is_refused_by_a_schema_3_build(root, monkeypatch):
    with native_connection(root) as connection:
        upgrade.upgrade(connection)
        before = list(connection.iterdump())
        with monkeypatch.context() as older:
            _as_older_build(older)
            connection.execute('BEGIN')
            with pytest.raises(UnsupportedStoreError, match='Unsupported native staging schema or protocol'):
                db.assert_native(connection)
            connection.rollback()
        assert list(connection.iterdump()) == before


def test_schema_5_backfill_at_codemaestro_scale(root, record_property):
    with native_connection(root) as connection:
        real = connection.execute("SELECT COUNT(*) FROM domain_events e JOIN entity_core c ON c.kind=e.kind AND c.public_id=e.id "
                                  "WHERE e.kind NOT IN ('backlog','project')").fetchone()[0]
        connection.execute("BEGIN")
        connection.executemany("INSERT INTO entity_core(kind,public_id,revision,last_seq) VALUES('task',?,1,0)",
                               [(f'scale-{n:05d}',) for n in range(5000)])
        filler = "x" * 1500
        rows = [("t", "s", "tool", "task", f"scale-{n % 5000:05d}", "patch", filler) for n in range(47000)]
        rows += [("t", "s", "tool", "backlog", "__backlog__", "replace", filler) for _ in range(2000)]
        rows += [("t", "s", "tool", "task", f"tasks/p{n}.md", "resolve", filler) for n in range(1000)]
        connection.executemany("INSERT INTO domain_events(ts,session,tool,kind,id,op,after) VALUES(?,?,?,?,?,?,?)", rows)
        connection.execute("COMMIT")
        start = time.perf_counter()
        upgrade.upgrade(connection)
        elapsed = time.perf_counter() - start
        record_property("schema5_backfill_seconds", elapsed)
        assert connection.execute("SELECT COUNT(entity_key) FROM domain_events").fetchone()[0] == 47000 + real
        assert elapsed < 5.0
