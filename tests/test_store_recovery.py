"""Corruption recovery, busy diagnostics, and checkpoint contracts."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from taskmaster import store
from taskmaster.taskmaster_v3 import render_frontmatter


_TEST_BUSY_TIMEOUT_MS = 75


@pytest.fixture(autouse=True)
def _isolated_store_state(monkeypatch):
    """Keep recovery state isolated and make real lock tests finish quickly."""
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    monkeypatch.setattr(store, "BUSY_TIMEOUT_MS", _TEST_BUSY_TIMEOUT_MS)
    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _write_projection(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    backlog_path = root / ".taskmaster"
    task_dir = backlog_path / "tasks"
    task_dir.mkdir(parents=True)
    (backlog_path / "backlog.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 4,
                "project": "recovery-tests",
                "meta": {
                    "schema_version": 4,
                    "projection_schema": store.PROJECTION_SCHEMA,
                },
                "epics": [
                    {
                        "id": "core",
                        "name": "Store core",
                        "status": "in-progress",
                    }
                ],
                "phases": [],
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    task_path = task_dir / "core-001.md"
    task_path.write_text(
        render_frontmatter(
            {
                "id": "core-001",
                "title": "Projected title",
                "epic": "core",
                "status": "todo",
                "order": 1.0,
            },
            "## Notes\n\nProjection survives recovery.",
        ),
        encoding="utf-8",
    )
    return backlog_path, task_path


def _task(data: dict, task_id: str = "core-001") -> dict:
    return next(
        task
        for epic in data["epics"]
        for task in epic.get("tasks", [])
        if task["id"] == task_id
    )


def _backup_family(backups: list[Path]) -> dict[str, dict[str, Path]]:
    pattern = re.compile(
        r"^store\.db\.corrupt-(?P<stamp>\d{8}T\d{12}Z)(?P<suffix>-wal|-shm)?$"
    )
    families: dict[str, dict[str, Path]] = {}
    for backup in backups:
        match = pattern.fullmatch(backup.name)
        assert match is not None, f"unexpected corruption-backup name: {backup.name}"
        families.setdefault(match.group("stamp"), {})[
            match.group("suffix") or ""
        ] = backup
    return families


@pytest.mark.parametrize(
    "message",
    [
        "database disk image is malformed",
        "file is not a database",
        "file is encrypted or is not a database",
    ],
)
def test_plain_database_errors_with_corruption_signatures_are_rebuildable(message):
    assert store._is_corruption(sqlite3.DatabaseError(message))


@pytest.mark.parametrize(
    "message",
    [
        "database is locked",
        "database table is locked",
        "unable to open database file",
        "attempt to write a readonly database",
        "disk I/O error",
        # Classification is type-first: an OperationalError is transient even
        # if a platform or wrapper happens to reuse corruption-like wording.
        "file is not a database",
    ],
)
def test_operational_errors_are_never_classified_as_corruption(message):
    assert not store._is_corruption(sqlite3.OperationalError(message))


def test_corrupt_database_family_is_renamed_together_then_projection_rebuilds(
    tmp_path,
):
    backlog_path, _ = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="before-corruption")
    database = opened.db_path
    assert _task(opened.load_dict())["title"] == "Projected title"
    store.reset_for_tests()

    corrupt_bytes = b"this is not a SQLite database\x00recovery sentinel"
    wal_bytes = b"wal recovery sentinel"
    shm_bytes = b"shm recovery sentinel"
    database.write_bytes(corrupt_bytes)
    Path(f"{database}-wal").write_bytes(wal_bytes)
    Path(f"{database}-shm").write_bytes(shm_bytes)

    rebuilt = store.open_store(backlog_path=backlog_path, session="after-corruption")

    assert _task(rebuilt.load_dict())["title"] == "Projected title"
    backups = sorted(database.parent.glob("store.db.corrupt-*"))
    families = _backup_family(backups)
    complete = [family for family in families.values() if set(family) == {"", "-wal", "-shm"}]
    assert len(complete) == 1
    family = complete[0]
    assert family[""].read_bytes() == corrupt_bytes
    assert family["-wal"].read_bytes() == wal_bytes
    assert family["-shm"].read_bytes() == shm_bytes
    assert set(rebuilt.status().corrupt_files) >= {
        path.name for path in family.values()
    }


def test_corrupt_header_rename_failure_preserves_backup_and_fails_safely(
    tmp_path, monkeypatch
):
    backlog_path, _ = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="corrupt-held-base")
    database = opened.db_path
    store.reset_for_tests()
    corrupt = b"not a sqlite database held elsewhere"
    database.write_bytes(corrupt)

    def held_rename(_self, _label):
        raise PermissionError(13, "held by peer", str(database))

    monkeypatch.setattr(store.Store, "_rename_database_family", held_rename)
    with pytest.raises(RuntimeError, match="close Taskmaster processes and retry"):
        store.open_store(backlog_path=backlog_path, session="corrupt-held-open")

    assert database.read_bytes() == corrupt
    backups = [
        path
        for path in database.parent.glob("store.db.corrupt-*")
        if not path.name.endswith(("-wal", "-shm"))
    ]
    assert len(backups) == 1
    assert backups[0].read_bytes() == corrupt


def test_warm_store_reference_recovers_corrupt_database_header(tmp_path):
    backlog_path, _ = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="warm-corruption")
    database = opened.db_path
    store.close_thread_connection()
    database.write_bytes(b"not a SQLite database")

    reopened = store.open_store(backlog_path=backlog_path, session="warm-reopen")

    assert _task(reopened.load_dict())["title"] == "Projected title"
    assert list(database.parent.glob("store.db.corrupt-*"))


def test_warm_store_reference_reimports_projection_when_database_disappears(tmp_path):
    backlog_path, _ = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="warm-missing")
    database = opened.db_path
    store.checkpoint_all()
    store.close_thread_connection()
    for path in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
        path.unlink(missing_ok=True)

    assert _task(opened.load_dict())["title"] == "Projected title"


def test_failed_quick_check_is_treated_as_corruption(tmp_path):
    backlog_path, _ = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="quick-check-test")

    class FailedQuickCheck:
        @staticmethod
        def execute(statement, _params=()):
            assert statement == "PRAGMA quick_check"
            return SimpleNamespace(fetchone=lambda: ("*** corruption on page 2",))

    with pytest.raises(sqlite3.DatabaseError, match="quick_check"):
        opened._prepare_schema(FailedQuickCheck())


def test_failed_quick_check_rebuild_preserves_dirty_commit_in_backup(
    tmp_path, monkeypatch
):
    backlog_path, _ = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="dirty-writer")
    database = opened.db_path
    store.reset_for_tests()

    # Model a committed authoritative row whose projection export was pending.
    # Recovery may rebuild from the older projection, but the renamed database
    # must retain the dirty row for manual recovery.
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT doc FROM entities WHERE kind='task' AND id='core-001'"
        ).fetchone()
        dirty_doc = json.loads(row[0])
        dirty_doc["title"] = "Committed only in SQLite"
        cursor = connection.execute(
            "INSERT INTO changes(ts,session,tool,kind,id,op,fields,before,after) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                "dirty-writer",
                "simulated-export-failure",
                "task",
                "core-001",
                "update",
                '["title"]',
                '{"title":"Projected title"}',
                '{"title":"Committed only in SQLite"}',
            ),
        )
        connection.execute(
            "UPDATE entities SET doc=?,rev=rev+1,updated_seq=? "
            "WHERE kind='task' AND id='core-001'",
            (
                json.dumps(
                    dirty_doc,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                cursor.lastrowid,
            ),
        )
        connection.execute(
            "UPDATE projection SET dirty=1 WHERE file='tasks/core-001.md'"
        )

    real_prepare = store.Store._prepare_schema
    calls = 0

    def fail_first_quick_check(self, connection):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.DatabaseError(
                "malformed database: quick_check=*** corruption on page 2"
            )
        return real_prepare(self, connection)

    monkeypatch.setattr(store.Store, "_prepare_schema", fail_first_quick_check)
    rebuilt = store.open_store(backlog_path=backlog_path, session="rebuild-reader")

    assert _task(rebuilt.load_dict())["title"] == "Projected title"
    backup_databases = [
        path
        for path in database.parent.glob("store.db.corrupt-*")
        if not path.name.endswith(("-wal", "-shm"))
    ]
    assert len(backup_databases) == 1
    with sqlite3.connect(backup_databases[0]) as connection:
        saved_doc = json.loads(
            connection.execute(
                "SELECT doc FROM entities WHERE kind='task' AND id='core-001'"
            ).fetchone()[0]
        )
        dirty = connection.execute(
            "SELECT dirty FROM projection WHERE file='tasks/core-001.md'"
        ).fetchone()[0]
    assert saved_doc["title"] == "Committed only in SQLite"
    assert dirty == 1


def test_old_corrupt_backups_are_pruned_but_recent_family_remains_in_status(
    tmp_path,
):
    backlog_path, _ = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="prune-test")
    database = opened.db_path
    store.reset_for_tests()

    old_names = {
        f"store.db.corrupt-20260801T010203000000Z{suffix}"
        for suffix in ("", "-wal", "-shm")
    }
    recent_names = {
        f"store.db.corrupt-20260903T010203000000Z{suffix}"
        for suffix in ("", "-wal", "-shm")
    }
    old_time = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
    recent_time = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
    for name, modified in [
        *((name, old_time) for name in old_names),
        *((name, recent_time) for name in recent_names),
    ]:
        path = database.parent / name
        path.write_bytes(name.encode("ascii"))
        os.utime(path, (modified, modified))

    reopened = store.open_store(backlog_path=backlog_path, session="prune-reader")
    present = {path.name for path in database.parent.glob("store.db.corrupt-*")}

    assert old_names.isdisjoint(present)
    assert recent_names <= present
    assert recent_names <= set(reopened.status().corrupt_files)


@pytest.mark.parametrize(
    "message",
    [
        "database is locked",
        "unable to open database file",
        "attempt to write a readonly database",
        "disk I/O error",
    ],
)
def test_open_operational_error_never_invokes_recovery(
    tmp_path, monkeypatch, message
):
    backlog_path, _ = _write_projection(tmp_path)
    database = store.open_store(
        backlog_path=backlog_path, session="transient-baseline"
    ).db_path
    store.reset_for_tests()

    def fail_prepare(_self, _connection):
        raise sqlite3.OperationalError(message)

    def forbidden_recovery(_self):
        raise AssertionError("OperationalError must not enter corruption recovery")

    monkeypatch.setattr(store.Store, "_prepare_schema", fail_prepare)
    monkeypatch.setattr(store.Store, "_recover_corrupt_database", forbidden_recovery)

    with pytest.raises(sqlite3.OperationalError, match=re.escape(message)):
        store.open_store(backlog_path=backlog_path, session="transient-open")

    assert database.is_file()
    assert not list(database.parent.glob("store.db.corrupt-*"))


def test_busy_timeout_reports_only_live_probable_holders_and_last_writer(
    tmp_path,
):
    backlog_path, _ = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="last-writer-session")
    with opened.transaction(tool="seed-writer") as tx:
        task = tx.get("task", "core-001")
        task["status"] = "in-progress"
        tx.put("task", "core-001", task)

    before_seq = opened.status().max_seq
    holder = sqlite3.connect(opened.db_path, timeout=0, isolation_level=None)
    now = datetime.now(timezone.utc)
    session_sql = (
        "INSERT INTO sessions(session,pid,host,started,last_seen,cwd,current_tool) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET "
        "last_seen=excluded.last_seen,current_tool=excluded.current_tool"
    )
    holder.execute(
        session_sql,
        (
            "holder-session",
            101,
            "test-host",
            now.isoformat(),
            now.isoformat(),
            str(tmp_path),
            "backlog_update_task",
        ),
    )
    stale = now - timedelta(minutes=2)
    holder.execute(
        session_sql,
        (
            "stale-session",
            202,
            "test-host",
            stale.isoformat(),
            stale.isoformat(),
            str(tmp_path),
            "stale-tool",
        ),
    )
    holder.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError) as raised:
            with opened.transaction(tool="blocked-writer"):
                raise AssertionError("transaction body must not run while BEGIN is blocked")
    finally:
        elapsed = time.monotonic() - started
        holder.rollback()
        holder.close()

    message = str(raised.value)
    assert elapsed < 1.0, f"injected busy timeout took {elapsed:.3f}s"
    assert message.startswith("store busy for 0.075s;")
    assert "probable holders:" in message
    assert "holder-session (backlog_update_task)" in message
    assert "stale-session" not in message
    assert "last committed writer: last-writer-session (seed-writer, seq " in message
    assert opened.status().max_seq == before_seq
    assert not list(opened.db_path.parent.glob("store.db.corrupt-*"))


def test_real_holder_tool_is_visible_and_busy_path_is_bounded(tmp_path):
    backlog_path, _ = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="actual-session")
    entered = threading.Event()
    release = threading.Event()

    def hold_writer():
        with opened.transaction(tool="REAL-HOLDER-TOOL"):
            entered.set()
            assert release.wait(timeout=5)

    thread = threading.Thread(target=hold_writer)
    thread.start()
    assert entered.wait(timeout=5)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError) as caught:
            with opened.transaction(tool="CONTENDER"):
                pass
    finally:
        release.set()
        thread.join(timeout=5)

    assert "REAL-HOLDER-TOOL" in str(caught.value)
    assert time.monotonic() - started < 1.0


def test_busy_diagnostic_uses_a_separate_bounded_connection(tmp_path, monkeypatch):
    backlog_path, _ = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="diagnostic-test")
    primary = opened.connection
    real_connect = sqlite3.connect
    calls = []

    def tracking_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        calls.append((connection, args, kwargs))
        return connection

    monkeypatch.setattr(store.sqlite3, "connect", tracking_connect)
    message = opened._busy_diagnostic()

    assert message.startswith("store busy for 0.075s;")
    assert len(calls) == 1
    diagnostic, args, kwargs = calls[0]
    assert diagnostic is not primary
    assert args[0] == f"file:{opened.db_path.as_posix()}?mode=rw"
    assert kwargs["timeout"] == 2.0
    assert kwargs["uri"] is True
    with pytest.raises(sqlite3.ProgrammingError):
        diagnostic.execute("SELECT 1")


def test_read_side_projection_scan_does_not_wait_for_writer_mutex(tmp_path):
    backlog_path, task_path = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="read-nonblocking")
    frontmatter, body = store.parse_frontmatter(task_path.read_text(encoding="utf-8"))
    frontmatter["title"] = "External while busy"
    task_path.write_text(store.render_frontmatter(frontmatter, body), encoding="utf-8")
    entered = threading.Event()
    release = threading.Event()
    # The assertion is "the read did not queue behind the writer", not "the read
    # was fast": an absolute millisecond budget flakes under full-suite load.
    # Hold the mutex for a long, explicit interval and require the read to
    # return in a small fraction of it.
    hold_seconds = 2.0

    def hold_mutex():
        with opened._writer_mutex():
            entered.set()
            assert release.wait(timeout=hold_seconds + 5)

    thread = threading.Thread(target=hold_mutex)
    thread.start()
    assert entered.wait(timeout=5)
    started = time.monotonic()
    try:
        stale = opened.load_dict()
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, hold_seconds - elapsed))
    finally:
        release.set()
        thread.join(timeout=10)

    assert elapsed < hold_seconds / 4, (
        f"the read waited {elapsed:.3f}s while the writer mutex was held for "
        f"{hold_seconds}s — it queued behind the writer"
    )
    assert _task(stale)["title"] == "Projected title"


def test_busy_diagnostic_has_exact_fallback_when_diagnostic_open_fails(
    tmp_path, monkeypatch
):
    backlog_path, _ = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="fallback-test")

    def fail_diagnostic(*_args, **_kwargs):
        raise sqlite3.OperationalError("diagnostic unavailable")

    monkeypatch.setattr(store.sqlite3, "connect", fail_diagnostic)

    assert opened._busy_diagnostic() == (
        "store busy for 0.075s; could not read session table; retry"
    )


def test_passive_checkpoint_failure_is_best_effort_after_commit(
    tmp_path, monkeypatch
):
    backlog_path, _ = _write_projection(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="passive-test")
    attempted = []
    recoveries = []

    class FailingCheckpointConnection:
        @staticmethod
        def execute(statement):
            attempted.append(statement)
            raise sqlite3.OperationalError("checkpoint is busy")

    def injected_checkpoint(_connection):
        store.Store._checkpoint_passive(FailingCheckpointConnection())

    monkeypatch.setattr(opened, "_checkpoint_passive", injected_checkpoint)
    monkeypatch.setattr(
        opened,
        "_rename_database_family",
        lambda label: recoveries.append(label),
    )

    with opened.transaction(tool="commit-before-passive-failure") as tx:
        task = tx.get("task", "core-001")
        task["status"] = "done"
        tx.put("task", "core-001", task)

    assert attempted == ["PRAGMA wal_checkpoint(PASSIVE)"]
    assert _task(opened.load_dict())["status"] == "done"
    assert recoveries == []
    assert not list(opened.db_path.parent.glob("store.db.corrupt-*"))


def test_truncate_checkpoint_failure_is_best_effort(tmp_path, monkeypatch):
    attempted = []

    class FailingCheckpointConnection:
        def execute(self, statement):
            attempted.append(statement)
            if statement == "PRAGMA wal_checkpoint(TRUNCATE)":
                raise sqlite3.OperationalError("checkpoint is busy")
            return self

        def close(self):
            attempted.append("close")

    database = tmp_path / "fake-store.db"
    database.write_bytes(b"fixture")
    fake_store = SimpleNamespace(db_path=database)
    monkeypatch.setattr(store, "_STORES", {database: fake_store})
    monkeypatch.setattr(
        store.sqlite3, "connect", lambda *_args, **_kwargs: FailingCheckpointConnection()
    )

    store.checkpoint_all()

    assert attempted == [
        "PRAGMA busy_timeout=0",
        "PRAGMA wal_checkpoint(TRUNCATE)",
        "close",
    ]
