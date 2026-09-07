"""Schema creation, compatibility, and connection-lifecycle contracts."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from taskmaster import store


EXPECTED_COLUMNS = {
    "meta": {"key", "value"},
    "entities": {
        "kind", "id", "epic", "status", "archived", "deleted", "doc",
        "body", "rev", "updated_seq",
    },
    "changes": {
        "seq", "ts", "session", "tool", "kind", "id", "op", "fields",
        "before", "after",
    },
    "projection": {
        "file", "kind", "id", "content_hash", "mtime", "size", "dirty",
        "quarantined", "exported_seq",
    },
    "projection_base": {"file", "content"},
    "sessions": {
        "session", "pid", "host", "started", "last_seen", "cwd", "current_tool",
    },
    "linear_queue": {
        "seq", "op", "target_id", "tracker_id", "payload", "state", "attempts",
        "last_error",
    },
    "entity_paths": {"kind", "id", "path", "match_kind", "source"},
    "links": {"src_kind", "src_id", "type", "dst_kind", "dst_id", "derived"},
    "related": {"a_kind", "a_id", "b_kind", "b_id", "via", "weight"},
    "handover_tasks": {"handover_id", "task_id"},
    "entity_fts": {"kind", "id", "title", "body"},
}


@pytest.fixture(autouse=True)
def _isolated_store_state(monkeypatch):
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _write_projection(
    root: Path,
    *,
    project: str = "schema-project",
    projection_schema: int | None = None,
) -> Path:
    backlog_path = root / ".taskmaster"
    backlog_path.mkdir(parents=True, exist_ok=True)
    meta = {"schema_version": 4}
    if projection_schema is not None:
        meta["projection_schema"] = projection_schema
    document = {
        "version": 4,
        "project": project,
        "meta": meta,
        "epics": [],
        "phases": [],
    }
    (backlog_path / "backlog.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return backlog_path


def _database(root: Path) -> Path:
    return store.db_path(root / ".taskmaster")


def _meta(db: Path) -> dict[str, str]:
    with sqlite3.connect(db) as connection:
        return dict(connection.execute("SELECT key, value FROM meta"))


def _active_connection(records, thread_id: int):
    for owner, connection in reversed(records):
        if owner != thread_id:
            continue
        try:
            connection.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            continue
        return connection
    raise AssertionError(f"no live SQLite connection recorded for thread {thread_id}")


def _index_columns(connection: sqlite3.Connection, table: str) -> list[tuple[str, ...]]:
    indexes = []
    for row in connection.execute(f"PRAGMA index_list('{table}')"):
        indexes.append(
            tuple(
                column[2]
                for column in connection.execute(f"PRAGMA index_info('{row[1]}')")
            )
        )
    return indexes


def test_first_open_creates_local_files_complete_schema_and_metadata(tmp_path):
    root = tmp_path / "repo"
    backlog_path = _write_projection(root)

    store.open_store(root=root, session="schema-first-open")

    db = store.db_path(backlog_path)
    assert db == backlog_path / "local" / "store.db"
    assert db.is_file()
    assert (backlog_path / "local" / ".gitignore").read_bytes() == b"*\n"

    with sqlite3.connect(db) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        assert EXPECTED_COLUMNS.keys() <= tables
        for table, expected in EXPECTED_COLUMNS.items():
            actual = {
                row[1] for row in connection.execute(f"PRAGMA table_info('{table}')")
            }
            assert expected <= actual, table

        entity_pk = {
            row[1]: row[5]
            for row in connection.execute("PRAGMA table_info('entities')")
        }
        assert entity_pk["kind"] == 1
        assert entity_pk["id"] == 2
        assert any(
            columns[:2] == ("kind", "id")
            for columns in _index_columns(connection, "entity_paths")
        )
        assert any(
            columns[:2] == ("src_kind", "src_id")
            for columns in _index_columns(connection, "links")
        )
        assert any(
            columns[:2] == ("a_kind", "a_id")
            for columns in _index_columns(connection, "related")
        )
        assert any(
            columns[:2] == ("handover_id", "task_id")
            for columns in _index_columns(connection, "handover_tasks")
        )

        session = connection.execute(
            "SELECT pid, host, cwd, current_tool FROM sessions WHERE session = ?",
            ("schema-first-open",),
        ).fetchone()

    metadata = _meta(db)
    assert metadata["schema_version"] == str(store.SCHEMA_VERSION)
    assert uuid.UUID(metadata["creation_token"]).version == 4
    assert session == (os.getpid(), socket.gethostname(), str(Path.cwd().resolve()), None)


def test_simultaneous_fresh_process_opens_serialize_schema_creation(tmp_path):
    root = tmp_path / "repo"
    backlog_path = _write_projection(root)
    gate = tmp_path / "open-gate"
    script = (
        "import sys,time; from pathlib import Path; from taskmaster import store; "
        "gate=Path(sys.argv[1]); "
        "\nwhile not gate.exists(): time.sleep(0.005)\n"
        "store.open_store(backlog_path=Path(sys.argv[2]))"
    )
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(gate), str(backlog_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(8)
    ]
    gate.write_text("go", encoding="ascii")
    failures = []
    for worker in workers:
        stdout, stderr = worker.communicate(timeout=20)
        if worker.returncode:
            failures.append((worker.returncode, stdout, stderr))

    assert failures == []
    opened = store.open_store(backlog_path=backlog_path, session="verify-fresh-open")
    assert opened.connection.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()[0] == str(store.SCHEMA_VERSION)


def test_store_connection_uses_required_pragmas_and_explicit_transactions(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    _write_projection(root)
    real_connect = store.sqlite3.connect
    records = []

    def recording_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        records.append((threading.get_ident(), connection))
        return connection

    monkeypatch.setattr(store.sqlite3, "connect", recording_connect)
    store.open_store(root=root, session="pragma-test")
    connection = _active_connection(records, threading.get_ident())

    assert connection.isolation_level is None
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000


def test_status_exposes_operator_recovery_snapshot(tmp_path):
    root = tmp_path / "repo"
    backlog_path = _write_projection(root)
    opened = store.open_store(backlog_path=backlog_path, session="status-contract")
    with opened.transaction(tool="status-change") as tx:
        backlog = tx.get("backlog", "__backlog__")
        backlog["project"] = "status-updated"
        tx.put("backlog", "__backlog__", backlog)

    result = opened.status()

    assert result.root == root.resolve()
    assert result.resolution_source == "explicit"
    assert result.schema_version == store.SCHEMA_VERSION
    assert result.db_size > 0
    assert result.wal_size >= 0
    assert len(result.recent_changes) <= 20
    assert result.recent_changes[0]["tool"] == "status-change"
    assert any(row["session"] == "status-contract" for row in result.live_sessions)
    assert result.merge_conflicts_24h == 0


def test_connection_is_reused_per_thread_and_isolated_between_threads(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    _write_projection(root)
    store.open_store(root=root, session="bootstrap")
    store.reset_for_tests()

    real_connect = store.sqlite3.connect
    records = []
    record_lock = threading.Lock()

    def recording_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        owner = threading.get_ident()
        with record_lock:
            records.append((owner, connection))
        return connection

    def cached_ids(owner):
        """The connections `owner` still holds, newest last.

        A cold open also announces itself through a short-lived handle so a
        plain read never waits on the writer mutex to register a session. That
        handle is closed before the open returns and is not the per-thread
        cached connection this test is about.
        """
        live = []
        with record_lock:
            recorded = list(records)
        for recorded_owner, connection in recorded:
            if recorded_owner != owner:
                continue
            try:
                connection.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                continue
            live.append(id(connection))
        return live

    monkeypatch.setattr(store.sqlite3, "connect", recording_connect)

    store.open_store(root=root, session="main-one")
    store.open_store(root=root, session="main-two")
    main_thread = threading.get_ident()

    barrier = threading.Barrier(3)

    def open_twice(label: str) -> tuple[int, tuple[int, ...]]:
        barrier.wait()
        store.open_store(root=root, session=f"{label}-one")
        store.open_store(root=root, session=f"{label}-two")
        owner = threading.get_ident()
        barrier.wait()
        return owner, tuple(cached_ids(owner))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(open_twice, "worker-a")
        second = pool.submit(open_twice, "worker-b")
        barrier.wait()
        barrier.wait()
        worker_results = [first.result(), second.result()]

    assert len(cached_ids(main_thread)) == 1
    assert len({owner for owner, _ids in worker_results}) == 2
    assert all(len(ids) == 1 for _owner, ids in worker_results)
    connection_ids = {cached_ids(main_thread)[0]}
    connection_ids.update(ids[0] for _owner, ids in worker_results)
    assert len(connection_ids) == 3


def test_worker_thread_connection_is_closed_when_thread_registry_retires(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    _write_projection(root)
    store.open_store(root=root, session="bootstrap")
    store.close_thread_connection()

    real_connect = store.sqlite3.connect
    worker_connections = []

    def recording_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        worker_connections.append(connection)
        return connection

    monkeypatch.setattr(store.sqlite3, "connect", recording_connect)
    thread = threading.Thread(
        target=lambda: store.open_store(root=root, session="retiring-worker")
    )
    thread.start()
    thread.join()

    assert len(worker_connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        worker_connections[0].execute("SELECT 1")


def test_close_thread_connection_closes_and_replaces_current_connection(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    _write_projection(root)
    real_connect = store.sqlite3.connect
    records = []

    def recording_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        records.append((threading.get_ident(), connection))
        return connection

    monkeypatch.setattr(store.sqlite3, "connect", recording_connect)
    store.open_store(root=root, session="before-close")
    first = _active_connection(records, threading.get_ident())

    store.close_thread_connection()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        first.execute("SELECT 1")
    store.open_store(root=root, session="after-close")
    second = _active_connection(records, threading.get_ident())
    assert second is not first


def test_reset_for_tests_clears_cached_root_and_closes_connection(
    tmp_path, monkeypatch
):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _write_projection(first_root, project="first")
    _write_projection(second_root, project="second")
    monkeypatch.setenv("TASKMASTER_ROOT", str(first_root))

    store.open_store(session="first-root")
    first_db = _database(first_root)
    second_db = _database(second_root)
    assert first_db.exists()
    monkeypatch.setenv("TASKMASTER_ROOT", str(second_root))
    store.open_store(session="still-first")
    assert not second_db.exists()

    store.reset_for_tests()

    store.open_store(session="second-root")
    assert second_db.exists()


def test_compatible_schema_reopens_in_place(tmp_path):
    root = tmp_path / "repo"
    _write_projection(root)
    store.open_store(root=root, session="compatible-one")
    db = _database(root)
    original = _meta(db)["creation_token"]
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO meta(key, value) VALUES('sentinel', 'preserved')")
    store.reset_for_tests()

    store.open_store(root=root, session="compatible-two")

    metadata = _meta(db)
    assert metadata["creation_token"] == original
    assert metadata["sentinel"] == "preserved"


def test_supported_schema_migration_runs_in_place(tmp_path):
    root = tmp_path / "repo"
    _write_projection(root)
    store.open_store(root=root, session="migration-source")
    db = _database(root)
    original = _meta(db)["creation_token"]
    store.close_thread_connection()
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
        connection.execute("INSERT INTO meta(key, value) VALUES('sentinel', 'preserved')")
    store.reset_for_tests()

    store.open_store(root=root, session="migration-target")

    metadata = _meta(db)
    assert metadata["schema_version"] == str(store.SCHEMA_VERSION)
    assert metadata["creation_token"] == original
    assert metadata["sentinel"] == "preserved"


def test_unsupported_schema_version_rebuilds_from_projection(tmp_path):
    root = tmp_path / "repo"
    _write_projection(root, project="rebuilt-project")
    store.open_store(root=root, session="unsupported-source")
    db = _database(root)
    original = _meta(db)["creation_token"]
    store.close_thread_connection()
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
        connection.execute("INSERT INTO meta(key, value) VALUES('sentinel', 'discarded')")
    store.reset_for_tests()

    store.open_store(root=root, session="unsupported-target")

    metadata = _meta(db)
    assert metadata["schema_version"] == str(store.SCHEMA_VERSION)
    assert metadata["creation_token"] != original
    assert "sentinel" not in metadata
    assert store.load_dict(root / ".taskmaster")["project"] == "rebuilt-project"


def test_newer_projection_schema_is_refused_before_database_creation(tmp_path):
    root = tmp_path / "repo"
    _write_projection(root, projection_schema=store.PROJECTION_SCHEMA + 1)

    with pytest.raises(
        (RuntimeError, ValueError), match=r"(?i)projection.schema.*newer|newer.*projection"
    ):
        store.open_store(root=root, session="too-new")

    assert not _database(root).exists()


def test_creation_token_change_invalidates_cached_dict_without_new_change_seq(tmp_path):
    root = tmp_path / "repo"
    backlog_path = _write_projection(root, project="before-token-change")
    store.open_store(root=root, session="cache-source")
    assert store.load_dict(backlog_path)["project"] == "before-token-change"
    db = _database(root)

    with sqlite3.connect(db) as connection:
        entity_id, raw_doc = connection.execute(
            "SELECT id, doc FROM entities WHERE kind = 'backlog'"
        ).fetchone()
        document = json.loads(raw_doc)
        document["project"] = "after-token-change"
        connection.execute(
            "UPDATE entities SET doc = ? WHERE kind = 'backlog' AND id = ?",
            (json.dumps(document, sort_keys=True), entity_id),
        )
        replacement_token = str(uuid.uuid4())
        connection.execute(
            "UPDATE meta SET value = ? WHERE key = 'creation_token'",
            (replacement_token,),
        )

    assert store.load_dict(backlog_path)["project"] == "after-token-change"
