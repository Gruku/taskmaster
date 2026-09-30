# User intent: the routing gate's legacy answer should cost no new SQLite connection per
# tool call, without ever answering "legacy" for a store that has become native.
"""The gate reads a legacy store's own idle connection; everything else decides as before."""
from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path

import pytest
import yaml

from taskmaster import store
from taskmaster.native_routing import gate


@pytest.fixture(autouse=True)
def _isolated_store_state(monkeypatch):
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    store.reset_for_tests()
    yield
    store.reset_for_tests()


@pytest.fixture
def legacy(tmp_path):
    """An opened legacy store with a connection retained on this thread."""
    backlog = tmp_path / "project" / ".taskmaster"
    backlog.mkdir(parents=True)
    (backlog / "backlog.yaml").write_text(yaml.safe_dump({
        "version": 4, "project": "gate", "epics": [], "phases": [],
        "meta": {"schema_version": 4, "projection_schema": store.PROJECTION_SCHEMA},
    }), encoding="utf-8")
    store.open_store(backlog_path=backlog, session="gate-retained")
    store.load_dict(backlog)
    database = gate.database_path(backlog)
    assert store.retained_connection(database) is not None
    return backlog, database


def _no_new_connections(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("the gate opened a connection of its own")
    monkeypatch.setattr(gate.sqlite3, "connect", forbidden)


def test_legacy_answer_reuses_the_idle_legacy_connection(legacy, monkeypatch):
    backlog, _database = legacy
    _no_new_connections(monkeypatch)
    assert gate.native_database(backlog) is None
    assert gate.native_database(backlog / "backlog.yaml") is None


def test_a_native_marker_committed_elsewhere_is_never_answered_legacy(legacy):
    backlog, database = legacy
    with closing(sqlite3.connect(database, isolation_level=None)) as other:
        other.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    # Not a ready native manifest, so the gate refuses; it must not route to legacy.
    with pytest.raises(gate.NativeUnavailable):
        gate.native_database(backlog)


def test_a_connection_inside_a_transaction_is_not_retained(legacy):
    _backlog, database = legacy
    connection = store.retained_connection(database)
    connection.execute("BEGIN")
    try:
        assert store.retained_connection(database) is None
    finally:
        connection.rollback()
    assert store.retained_connection(database) is connection


def test_a_connection_to_a_replaced_file_is_not_retained(legacy, monkeypatch):
    _backlog, database = legacy
    connection = store.retained_connection(database)
    monkeypatch.setitem(store._CONNECTION_IDENTITIES, id(connection), (-1, -1))
    assert store.retained_connection(database) is None


def test_no_retained_connection_on_another_thread(legacy):
    import threading
    _backlog, database = legacy
    seen = []
    worker = threading.Thread(target=lambda: seen.append(store.retained_connection(database)))
    worker.start()
    worker.join()
    assert seen == [None]


def test_repeated_resolution_of_an_absolute_path_skips_path_resolve(legacy, monkeypatch):
    backlog, database = legacy
    for spelling in (backlog, backlog / "backlog.yaml"):
        store.resolve_location(spelling)

    def forbidden(_path: Path):
        raise AssertionError("resolved the path again")
    monkeypatch.setattr(store, "_backlog_dir", forbidden)
    for spelling in (backlog, backlog / "backlog.yaml"):
        assert store.resolve_location(spelling).backlog_path == backlog.resolve()
        assert store.opened_store(spelling) is not None
