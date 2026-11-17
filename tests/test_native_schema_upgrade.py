# User intent: pin the irreversible schema 3 -> 4 migration's guarantees: stores are born at 3 and stay
# there (solo stores remain downgradable to 7.4.x), the upgrade is one transaction that changes nothing
# but the manifest, and an older build refuses a schema-4 store cleanly without touching it.
from __future__ import annotations

from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import threading

import pytest

from taskmaster import backlog_server as bs
from taskmaster.admission import UnsupportedStoreError
from taskmaster.bounded_run import run_bounded
from taskmaster.coordinator import client as client_module
from taskmaster.coordinator import service as service_module
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.ownership import OwnershipUnavailable
from taskmaster.coordinator.service import Coordinator
from taskmaster.native import commands, db, schema, upgrade
from taskmaster.native.queries import Repository
from taskmaster.native_routing import gate
from native_twins import make_twins, native_connection, native_database

REPO = Path(__file__).resolve().parents[1]
RELEASED_7_4_0 = "6ef360b"
OLD = {'version': '7.0.0', 'digest': 'a' * 32}
NEW = {'version': '7.0.1', 'digest': 'b' * 32}


@pytest.fixture
def root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, lambda: bs.backlog_add_task(title='Upgrade task', epic='test-epic',
                                                                         phase='dev'), visibility=None)
    with twins.at(twins.native):
        yield twins.native


def manifest_version(root):
    with closing(sqlite3.connect(native_database(root))) as connection:
        return connection.execute("SELECT value FROM native_manifest WHERE key='schema_version'").fetchone()[0]


def store_id(root):
    with closing(sqlite3.connect(native_database(root))) as connection:
        return connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]


def patch(root, key='one', title='Changed'):
    return {'protocol': 2, 'store_id': store_id(root), 'caller_scope': 'upgrade-tests', 'request_id': key,
            'operation': 'task.patch', 'arguments': {'id': 'test-epic-001', 'set': {'title': title}},
            'expected_revisions': []}


def dump_without_version(connection):
    return [line for line in connection.iterdump()
            if not (line.startswith('INSERT INTO "native_manifest"') and "'schema_version'" in line)]


def test_new_stores_are_born_at_schema_3(root):
    assert schema.BASE_VERSION == 3 and schema.VERSION == 5 and schema.SUPPORTED == frozenset({3, 4, 5})
    assert manifest_version(root) == '3'


def test_schema_3_store_reads_and_writes_as_today_and_is_never_upgraded_by_start(root):
    with Coordinator(root):
        client = Client(root, autostart=False)
        assert client.execute(patch(root))['receipt']['affected']
        with native_connection(root) as connection, Repository(connection).snapshot() as snapshot:
            assert snapshot.get('task', 'test-epic-001')['fields']['title'] == 'Changed'
        assert gate.native_database(root / '.taskmaster') is not None
    assert manifest_version(root) == '3'
    with Coordinator(root):
        pass
    assert manifest_version(root) == '3'


def test_upgrade_moves_3_to_4_once_and_changes_nothing_else(root):
    with native_connection(root) as connection:
        before = dump_without_version(connection)
        assert upgrade.upgrade(connection, to=4) == {'from': 3, 'to': 4}
        assert not connection.in_transaction
        assert dump_without_version(connection) == before
        assert manifest_version(root) == '4'
        whole = list(connection.iterdump())
        assert upgrade.upgrade(connection, to=4) == {'from': 4, 'to': 4}
        assert list(connection.iterdump()) == whole


def test_upgrade_requires_no_open_transaction(root):
    with native_connection(root) as connection:
        connection.execute('BEGIN')
        with pytest.raises(RuntimeError):
            upgrade.upgrade(connection)
        connection.rollback()
    assert manifest_version(root) == '3'


def test_interrupted_upgrade_leaves_schema_3(root, monkeypatch):
    def broken(connection, version):
        raise RuntimeError('interrupted upgrade step')
    monkeypatch.setattr(schema, 'upgrade_native', broken)
    with native_connection(root) as connection:
        before = list(connection.iterdump())
        with pytest.raises(RuntimeError, match='interrupted'):
            upgrade.upgrade(connection)
        assert not connection.in_transaction
        assert list(connection.iterdump()) == before
    assert manifest_version(root) == '3'


def _as_older_build(monkeypatch):
    """At 7.4.0 `native/db.py` is this same code with VERSION = 3 and only {3} accepted."""
    monkeypatch.setattr(schema, 'VERSION', 3)
    monkeypatch.setattr(schema, 'SUPPORTED', frozenset({3}))


def test_the_schema_3_check_accepts_3_and_refuses_4(root, monkeypatch):
    with native_connection(root) as connection:
        commands.execute(connection, patch(root, 'written-by-this-build'))
    with monkeypatch.context() as older:
        _as_older_build(older)
        with native_connection(root) as connection:
            connection.execute('BEGIN')
            db.assert_native(connection)
            connection.rollback()
            with db.verified_snapshot(connection):
                pass
            assert commands.execute(connection, patch(root, 'older-build', 'Older'))['affected']
        assert gate.native_database(root / '.taskmaster') is not None
    with native_connection(root) as connection:
        upgrade.upgrade(connection)
    refusal = 'Unsupported native staging schema or protocol'
    with monkeypatch.context() as older:
        _as_older_build(older)
        with native_connection(root) as connection:
            before = list(connection.iterdump())
            connection.execute('BEGIN')
            with pytest.raises(UnsupportedStoreError, match=refusal):
                db.assert_native(connection)
            connection.rollback()
            with pytest.raises(UnsupportedStoreError, match=refusal):
                with db.verified_snapshot(connection):
                    pass
            with pytest.raises(UnsupportedStoreError, match=refusal):
                commands.execute(connection, patch(root, 'refused', 'Refused'))
            assert list(connection.iterdump()) == before
        with pytest.raises(gate.NativeUnavailable, match=refusal):
            gate.native_database(root / '.taskmaster')
        with native_connection(root) as connection:
            assert list(connection.iterdump()) == before
    # This build still reads and writes it.
    with native_connection(root) as connection:
        assert commands.execute(connection, patch(root, 'this-build-at-4', 'Four'))['affected']


_OLD_BUILD_PROBE = r"""
import json, sqlite3, sys
old, database = sys.argv[1], sys.argv[2]
sys.path.insert(0, old)
import taskmaster
assert taskmaster.__file__.startswith(old), taskmaster.__file__
from taskmaster.native import db
from taskmaster.native.queries import Repository
connection = sqlite3.connect(database, isolation_level=None, timeout=30)
try:
    connection.execute("BEGIN")
    try:
        db.assert_native(connection)
    finally:
        connection.rollback()
    with Repository(connection).snapshot() as snapshot:
        items = snapshot.list("task", fields=["id", "title"], limit=500)["items"]
    print(json.dumps(sorted([item["id"], item["fields"].get("title")] for item in items)))
except Exception as exc:
    print(f"{type(exc).__name__}: {exc}")
finally:
    connection.close()
"""


def _released_build(tmp_path):
    archive = run_bounded(['git', 'archive', RELEASED_7_4_0, 'taskmaster'], cwd=REPO, timeout=120)
    assert archive.returncode == 0, archive.stderr.decode('utf-8', 'replace')
    target = tmp_path / 'old'
    target.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
        bundle.extractall(target, filter='data')
    return target


def _run_old(old, database):
    completed = run_bounded([sys.executable, '-I', '-c', _OLD_BUILD_PROBE, str(old), str(database)], timeout=120)
    assert completed.returncode == 0, completed.stderr.decode('utf-8', 'replace')
    return completed.stdout.decode('utf-8').strip()


def _settled_digest(database):
    with closing(sqlite3.connect(database, isolation_level=None)) as connection:
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    return hashlib.sha256(Path(database).read_bytes()).hexdigest()


@pytest.mark.slow
def test_released_7_4_0_build_opens_schema_3_and_refuses_schema_5(root, tmp_path):
    old = _released_build(tmp_path)
    database = native_database(root)
    with Coordinator(root):
        Client(root, autostart=False).execute(patch(root, 'before-old-build', 'Patched by this build'))
    assert manifest_version(root) == '3'
    assert json.loads(_run_old(old, database)) == [['test-epic-001', 'Patched by this build']]
    with native_connection(root) as connection:
        upgrade.upgrade(connection)
    before = _settled_digest(database)
    assert _run_old(old, database) == 'UnsupportedStoreError: Unsupported native staging schema or protocol'
    assert _settled_digest(database) == before
    assert manifest_version(root) == '5'


def test_newer_build_retires_a_coordinator_of_the_previous_schema(root, monkeypatch):
    """A 7.4.0 owner reports schema 3 (its build's VERSION) in discovery and its handshake."""
    real_identify = service_module.identify_connection
    previous_schema = {'on': True}

    def identify_as_previous(path, connection):
        identity = real_identify(path, connection)
        return dict(identity, schema=3) if previous_schema['on'] else identity
    monkeypatch.setattr(service_module, 'identify_connection', identify_as_previous)
    started = []
    old = Coordinator(root)
    old.build = OLD
    old.identity = dict(old.identity, schema=3)
    old.start()
    started.append(old)
    threading.Thread(target=lambda: (old.stopping.wait(), old.close()), daemon=True).start()

    def launch(path):
        previous_schema['on'] = False   # the old owner has released the lock: it is gone
        owner = Coordinator(path)
        owner.build = NEW
        try:
            owner.start()
        except OwnershipUnavailable:
            return type('Child', (), {'poll': lambda self: 0})()
        started.append(owner)
        return type('Child', (), {'poll': lambda self: None})()
    monkeypatch.setattr(client_module, '_launch', launch)
    try:
        client = Client(root)
        client.build = NEW
        assert client.identity['schema'] == 5
        receipt = client.execute(patch(root, 'after-retire', 'Served by the new build'))['receipt']
        assert receipt['affected']
        assert old.stopping.is_set()
        assert len(started) == 2 and client.status()['nonce'] == started[1].nonce
    finally:
        for owner in started:
            owner.stop()
        for owner in started:
            owner.close()
