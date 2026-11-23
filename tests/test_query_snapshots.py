# User intent: prove the shared backlog_query snapshot (team-server-034) is correct and safe: equivalent answers, fresh
# after every write, built once per revision, no partial or host-private state for a member, bounded files on disk,
# and clean recovery on Windows where an open file cannot be deleted.
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from taskmaster import query_guard
from taskmaster.coordinator import client, ownership
from taskmaster.native import compatibility, query_snapshots, query_worker
from taskmaster.native.migrate import backfill
from taskmaster.native.queries import Repository
from test_native_migration import legacy  # noqa: F401

WIN = sys.platform == "win32"


class World:
    """A staged store file, and helpers that open fresh read connections the way each MCP call does."""

    def __init__(self, path):
        self.path = Path(path)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            backfill(connection)
        self.writer = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.revision = 0
        self.write_lock = threading.RLock()

    def bump(self):
        """A commit that moves the store revision (a graph repair; the key reads it like any event)."""
        with self.write_lock:
            self.revision += 1
            self.writer.execute("INSERT OR REPLACE INTO native_manifest(key,value) VALUES('graph_repairs',?)", (str(self.revision),))

    def sql(self, statement, **kwargs):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            with Repository(connection).snapshot() as query:
                return query.sql(statement, **kwargs)

    def source(self):
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.execute("BEGIN")
        return connection

    @property
    def cache(self):
        with closing(self.source()) as connection:
            return query_snapshots.for_source(connection)

    @property
    def folder(self):
        return self.cache._folder

    def files(self):
        return sorted(p.name for p in self.folder.iterdir())


@pytest.fixture
def world(legacy, monkeypatch):  # noqa: F811
    monkeypatch.setattr(query_snapshots, "BUILD_MIN_INTERVAL_LOCAL", 0.0)
    monkeypatch.setattr(query_snapshots, "DISK_FLOOR", 1)
    built = World(legacy)
    yield built
    built.writer.close()
    query_snapshots.close_all()


def _rows(path, table):
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as connection:
        return sorted(map(repr, connection.execute(f'SELECT * FROM "{table}"')))


# -- T1 equivalence ---------------------------------------------------------------------------------------------------


def test_every_table_answers_as_a_fresh_build_and_the_host_tables_as_the_store(world, tmp_path):
    with closing(world.source()) as source:
        fresh = tmp_path / "fresh.db"
        compatibility.build(source, fresh, query_guard.Deadline(30))
        for table in query_guard.TABLES:
            got = sorted(map(repr, world.sql(f'SELECT * FROM "{table}"', limit=500)["rows"]))
            if table in compatibility.HOST_TABLES:
                expected = sorted(map(repr, source.execute(f'SELECT * FROM "{table}"')))
            else:
                expected = _rows(fresh, table)
            assert got == expected, table
    assert world.sql("SELECT claimed_by FROM linear_queue")["rows"] == [("peer",)]


# -- T2 read-your-writes and the overlay ------------------------------------------------------------------------------


def test_a_write_is_seen_by_the_next_query_and_a_heartbeat_needs_no_new_build(world):
    assert world.sql("SELECT count(*) FROM changes")["rows"] == [(13,)]
    columns = [r[1] for r in world.writer.execute("PRAGMA table_info(domain_events)") if r[1] != "seq"]
    names = ",".join(columns)
    world.writer.execute(f"INSERT INTO domain_events({names}) SELECT {names} FROM domain_events ORDER BY seq DESC LIMIT 1")
    world.bump()
    assert world.sql("SELECT count(*) FROM changes")["rows"] == [(14,)]
    builds = world.cache.builds
    world.writer.execute("UPDATE linear_queue SET claimed_by='someone-else'")
    # linear_queue changed without any revision change: the overlay carries the live rows.
    assert world.sql("SELECT claimed_by FROM linear_queue")["rows"] == [("someone-else",)]
    assert world.cache.builds == builds


# -- T3 the key covers every table the base copies ----------------------------------------------------------------------


def test_the_key_changes_with_an_event_a_repair_and_each_derived_table(world):
    def key():
        with closing(world.source()) as connection:
            return query_snapshots.read_key(connection)

    seen = {key()}
    columns = [r[1] for r in world.writer.execute("PRAGMA table_info(domain_events)") if r[1] != "seq"]
    names = ",".join(columns)
    world.writer.execute(f"INSERT INTO domain_events({names}) SELECT {names} FROM domain_events ORDER BY seq DESC LIMIT 1")
    seen.add(key())
    world.bump()
    seen.add(key())
    assert len(seen) == 3
    for table in ("entity_paths", "links", "related", "handover_tasks"):
        before = world.writer.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        columns = [r[1] for r in world.writer.execute(f'PRAGMA table_info("{table}")')]
        world.writer.execute(f'INSERT INTO "{table}"({",".join(columns)}) VALUES({",".join("?" for _ in columns)})',
                             ["x"] * len(columns))
        assert world.writer.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == before + 1
        now = key()
        assert now not in seen, table
        seen.add(now)


# -- T4 single flight ---------------------------------------------------------------------------------------------------


def test_sixteen_concurrent_calls_cause_one_build_and_a_write_during_the_run_one_more(world):
    def run():
        with ThreadPoolExecutor(16) as pool:
            return [f.result(60) for f in [pool.submit(world.sql, "SELECT count(*) FROM entities") for _ in range(16)]]

    assert {tuple(a["rows"][0]) for a in run()} == {(13,)}
    assert world.cache.builds == 1
    world.bump()
    run()
    assert world.cache.builds == 2


def test_builds_are_spaced_apart(world):
    world.cache.min_interval = 0.5
    world.sql("SELECT 1")
    first = world.cache._last_end
    world.bump()
    world.sql("SELECT 1")
    assert world.cache.builds == 2
    assert world.cache._last_end - first >= 0.5


# -- T5 no partial state ------------------------------------------------------------------------------------------------


def test_a_failed_build_publishes_nothing_removes_its_partial_and_the_next_call_retries(world, monkeypatch):
    real = compatibility.build

    def failing(source, path, deadline):
        real(source, path, deadline)
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(compatibility, "build", failing)
    with pytest.raises(query_snapshots.SnapshotUnavailable, match="could not be built: disk exploded"):
        world.sql("SELECT 1")
    assert world.files() == []
    monkeypatch.setattr(compatibility, "build", real)
    assert world.sql("SELECT 1")["rows"] == [(1,)]
    assert [n for n in world.files() if n.endswith(".partial")] == []


def test_the_worker_refuses_a_partial_file_and_any_path_outside_the_published_pattern(world, tmp_path):
    world.sql("SELECT 1")
    published = next(world.folder.glob("*.db"))
    partial = world.folder / "r1-0123456789abcdef.partial"
    partial.write_bytes(published.read_bytes())
    outside = tmp_path / "r1-0123456789abcdef.db"
    outside.write_bytes(published.read_bytes())
    for path in (partial, outside, world.path):
        answer = query_worker.POOL.run(path, "SELECT 1", limit=5, readable=query_guard.READABLE_TABLES,
                                       declared=frozenset(), seconds=5)
        assert answer["ok"] is False and answer["type"] == "SnapshotMissing", path
    answer = query_worker.POOL.run(published, "SELECT 1", limit=5, readable=query_guard.READABLE_TABLES,
                                   declared=frozenset(), seconds=5)
    assert answer["ok"] is True


def test_a_snapshot_the_worker_cannot_open_is_dropped_and_the_call_fails_once(world):
    world.sql("SELECT 1")
    published = next(world.folder.glob("*.db"))
    os.chmod(published, 0o600)
    published.write_bytes(b"not a database" * 100)
    with pytest.raises(query_snapshots.SnapshotUnavailable, match="retry"):
        world.sql("SELECT 1")
    assert world.sql("SELECT 1")["rows"] == [(1,)]


# -- T6 isolation, T7 restore -------------------------------------------------------------------------------------------


def test_a_commit_during_the_build_is_not_in_the_snapshot(world, monkeypatch):
    real = compatibility.build
    columns = [r[1] for r in world.writer.execute("PRAGMA table_info(domain_events)") if r[1] != "seq"]
    names = ",".join(columns)

    def with_a_writer(source, path, deadline):
        world.writer.execute(f"INSERT INTO domain_events({names}) SELECT {names} FROM domain_events ORDER BY seq DESC LIMIT 1")
        world.bump()
        return real(source, path, deadline)

    monkeypatch.setattr(compatibility, "build", with_a_writer)
    assert world.sql("SELECT count(*) FROM changes")["rows"] == [(13,)]      # the store has 14 by now
    monkeypatch.setattr(compatibility, "build", real)
    assert world.sql("SELECT count(*) FROM changes")["rows"] == [(14,)]      # the next call builds the newer one


def test_a_store_restored_to_a_lower_revision_does_not_use_the_newer_snapshot(world):
    world.revision = 5
    world.bump()
    world.sql("SELECT 1")
    high = world.cache._current.revision
    before = world.cache.builds
    world.revision = 1
    world.bump()                                           # same store, lower revision
    world.sql("SELECT 1")
    assert world.cache.builds == before + 1
    assert world.cache._current.revision < high


# -- T8 members ---------------------------------------------------------------------------------------------------------


MEMBER_DENIED = [
    "SELECT * FROM sessions", "SELECT * FROM main.sessions", "SELECT * FROM linear_queue", "SELECT * FROM projection",
    "SELECT * FROM temp.sessions", "WITH sessions AS (SELECT 1) SELECT * FROM main.sessions",
    "WITH linear_queue AS (SELECT 1) SELECT * FROM main.linear_queue", "SELECT * FROM meta", "SELECT * FROM projection_base",
    "SELECT * FROM entity_core",
]


@pytest.mark.parametrize("sql", MEMBER_DENIED)
def test_a_member_is_refused_every_host_and_private_table(world, sql):
    with query_guard.restricted(query_guard.READ_ENDPOINT_TABLES):
        with pytest.raises((sqlite3.DatabaseError, ValueError)):
            world.sql(sql)


def test_a_members_worker_gets_no_overlay_and_the_shared_file_holds_no_host_rows(world, monkeypatch):
    sent = []
    real = query_worker.POOL.run
    monkeypatch.setattr(query_worker.POOL, "run", lambda *a, **k: (sent.append(k.get("overlay")), real(*a, **k))[1])
    with query_guard.restricted(query_guard.READ_ENDPOINT_TABLES):
        assert world.sql("SELECT count(*) FROM entities WHERE id <> 'sessions linear_queue projection'")["rows"] == [(13,)]
        with pytest.raises(sqlite3.DatabaseError):
            world.sql("SELECT * FROM sessions")
    assert sent == [None, None]
    published = next(world.folder.glob("*.db"))
    for table in ("sessions", "linear_queue", "projection", "meta", "projection_base"):
        assert _rows(published, table) == [], table
    assert world.sql("SELECT claimed_by FROM linear_queue")["rows"] == [("peer",)]      # the operator's overlay
    assert sent[-1] is not None and set(sent[-1]) == {"linear_queue"}


def test_the_worker_itself_refuses_an_overlay_for_a_table_the_caller_may_not_read(world):
    world.sql("SELECT 1")
    published = next(world.folder.glob("*.db"))
    answer = query_worker.POOL.run(published, "SELECT * FROM sessions", limit=5, readable=query_guard.READ_ENDPOINT_TABLES,
                                   declared=frozenset(), seconds=5,
                                   overlay={"sessions": {"columns": [], "rows": []}})
    assert answer["ok"] is False and answer["type"] == "ValueError"


DENIED_FUNCTION = "SELECT hex('ab'), printf('%03d', 7)"


def test_the_served_deny_list_follows_the_profile(world, monkeypatch):
    monkeypatch.setattr(client, "ORIGIN_PROVIDER", lambda: None)               # a serving process in both profiles
    monkeypatch.setattr(query_guard, "DENY_FUNCTIONS_WHEN_SERVING", True)       # team
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        world.sql(DENIED_FUNCTION)
    monkeypatch.setattr(query_guard, "DENY_FUNCTIONS_WHEN_SERVING", False)      # local
    assert world.sql(DENIED_FUNCTION)["rows"] == [("6162", "007")]


# -- T9 limits -----------------------------------------------------------------------------------------------------------


def test_a_store_over_the_snapshot_budget_is_refused_with_todays_text_and_not_rebuilt_until_it_changes(world, monkeypatch):
    monkeypatch.setattr(query_worker, "SNAPSHOT_MAX_BYTES", 100)
    monkeypatch.setattr(compatibility, "SNAPSHOT_MAX_BYTES", 100)
    with pytest.raises(compatibility.StoreTooLarge, match="too large for backlog_query on this server"):
        world.sql("SELECT 1")
    builds = world.cache.builds
    with pytest.raises(compatibility.StoreTooLarge):
        world.sql("SELECT 1")
    assert world.cache.builds == builds                    # a negative entry for this revision
    world.bump()
    with pytest.raises(compatibility.StoreTooLarge):
        world.sql("SELECT 1")
    assert world.cache.builds == builds + 1
    assert world.files() == []


def test_a_build_below_the_disk_floor_is_refused_and_writes_nothing(world, monkeypatch):
    monkeypatch.setattr(query_snapshots, "DISK_FLOOR", 2**60)
    with pytest.raises(query_snapshots.SnapshotUnavailable, match="MiB free"):
        world.sql("SELECT 1")
    assert world.files() == []


def test_a_build_has_its_own_deadline(world, monkeypatch):
    monkeypatch.setattr(query_snapshots, "BUILD_SECONDS", 0.0)
    with pytest.raises(query_snapshots.SnapshotUnavailable, match="could not be built"):
        world.sql("SELECT 1")
    assert world.files() == []


def test_a_caller_whose_deadline_passes_while_waiting_gets_the_usual_error_and_the_build_goes_on(world, monkeypatch):
    real = compatibility.build
    release = threading.Event()
    monkeypatch.setattr(compatibility, "build", lambda *a: (release.wait(10), real(*a))[1])
    with pytest.raises(sqlite3.OperationalError, match="interrupt"):
        world.sql("SELECT 1", timeout=0.3)
    release.set()
    assert world.sql("SELECT 1")["rows"] == [(1,)]
    assert world.cache.builds == 1


def _put_session_bytes(world, size):
    columns = [r[1] for r in world.writer.execute("PRAGMA table_info(sessions)")]
    world.writer.execute("DELETE FROM sessions")
    row = ["s1"] + ["x"] * (len(columns) - 1)
    row[columns.index("cwd")] = "\xe9" * size
    world.writer.execute(f'INSERT INTO sessions({",".join(columns)}) VALUES({",".join("?" for _ in columns)})', row)


def test_an_overlay_at_the_limit_crosses_the_pipe_and_past_it_is_refused(world):
    # An overlay of about 4 MiB of JSON (escaped, six bytes a character) must fit the worker's request frame.
    _put_session_bytes(world, (query_worker.OVERLAY_MAX_BYTES - 600) // 6)
    rows = world.sql("SELECT session, length(cwd) FROM sessions")["rows"]
    assert rows[0][0] == "s1" and rows[0][1] > 600_000
    _put_session_bytes(world, query_worker.OVERLAY_MAX_BYTES // 6 + 10)
    with pytest.raises(ValueError, match="too large for backlog_query"):
        world.sql("SELECT session FROM sessions")
    assert world.sql("SELECT count(*) FROM entities")["rows"] == [(13,)]       # no overlay when no host table is named


def test_overlay_rows_are_visible_with_temp_store_on_file(world):
    world.writer.execute("UPDATE linear_queue SET claimed_by='ov'")
    rows = world.sql("SELECT claimed_by, (SELECT count(*) FROM entities) FROM linear_queue ORDER BY claimed_by")["rows"]
    assert rows == [("ov", 13)]
    assert world.sql("SELECT claimed_by FROM linear_queue ORDER BY 1 DESC")["rows"] == [("ov",)]


# -- T11 cleanup ---------------------------------------------------------------------------------------------------------


def test_a_superseded_unleased_file_is_deleted_at_publish_and_the_leased_one_at_release(world):
    world.sql("SELECT 1")
    assert len(world.files()) == 1
    world.bump()
    deadline = query_guard.Deadline(30)
    with closing(world.source()) as source:
        entry = world.cache.acquire(source, deadline)
    world.bump()
    with closing(world.source()) as source:
        newer = world.cache.acquire(source, deadline)
    assert newer is not entry and entry.superseded
    assert len(world.files()) == 2                             # the leased old file stays for its statement
    world.cache.release(entry)
    assert world.files() == [newer.path.name]
    world.cache.release(newer)
    assert world.files() == [newer.path.name]                  # the current file stays for the next call


@pytest.mark.skipif(not WIN, reason="a file open in another handle can only block a delete on Windows")
def test_a_delete_blocked_by_an_open_file_is_retried_and_succeeds_once_it_closes(world):
    world.sql("SELECT 1")
    old = next(world.folder.glob("*.db"))
    handle = open(old, "rb")                                   # a worker still has it open
    world.bump()
    world.sql("SELECT 1")                                      # publishes the successor; the old delete is refused
    assert old.exists() and world.cache._pending
    handle.close()
    world.sql("SELECT 1")                                      # the retry runs at the start of every query
    assert not old.exists() and not world.cache._pending


def test_over_the_bound_with_files_held_open_refuses_with_its_own_text_and_recovers(world, monkeypatch):
    world.sql("SELECT 1")
    world.cache.max_files = 3
    real = query_snapshots._remove
    monkeypatch.setattr(query_snapshots, "_remove", lambda path: False)       # every file stays "open"
    refused = None
    for _ in range(8):
        world.bump()
        try:
            world.sql("SELECT 1")
        except query_snapshots.SnapshotUnavailable as exc:
            refused = exc
            break
    assert refused is not None and str(refused) == query_snapshots.CLEANUP_TEXT
    assert not isinstance(refused, compatibility.StoreTooLarge)
    monkeypatch.setattr(query_snapshots, "_remove", real)                      # the workers let go
    assert world.sql("SELECT 1")["rows"] == [(1,)]
    assert not world.cache._pending


def test_the_published_file_is_read_only_and_still_deletable(world):
    world.sql("SELECT 1")
    published = next(world.folder.glob("*.db"))
    assert not os.access(published, os.W_OK)
    assert query_snapshots._remove(published) is True and not published.exists()


def test_start_sweep_removes_a_dead_processs_folder_and_keeps_live_and_young_ones(world):
    base = ownership.private_directory_at(world.path.parent / "query-snapshots")
    old = time.time() - 3600

    def fake(name, *, held=False, age=old):
        folder = base / name
        folder.mkdir()
        (folder / "r1-0123456789abcdef.db").write_bytes(b"x")
        os.chmod(folder / "r1-0123456789abcdef.db", 0o400)
        lock = base / f"{name}.lock"
        descriptor = ownership.take_lock_file(lock)
        if not held:
            os.close(descriptor)
            descriptor = None
        os.utime(lock, (age, age))
        return folder, lock, descriptor

    dead, dead_lock, _ = fake("p-111-aaaaaaaaaaaaaaaa")
    live, live_lock, descriptor = fake("p-222-bbbbbbbbbbbbbbbb", held=True)
    young, young_lock, _ = fake("p-333-cccccccccccccccc", age=time.time())
    try:
        world.sql("SELECT 1")                                    # the first build creates the folder and sweeps
        assert not dead.exists() and not dead_lock.exists()
        assert live.exists() and live_lock.exists()
        assert young.exists() and young_lock.exists()
    finally:
        os.close(descriptor)


def test_close_removes_its_own_folder_and_lock(world):
    world.sql("SELECT 1")
    folder, lock = world.folder, world.cache._lock_path
    assert folder.is_dir() and lock.exists()
    query_snapshots.close_all()
    assert not folder.exists() and not lock.exists()


# -- T12 disk bound under churn ------------------------------------------------------------------------------------------


def test_the_file_bound_holds_without_pending_deletes_when_callers_hold_leases(world):
    # 034 build M1: leases taken before a worker slot must not let the files pass the bound.
    deadline = query_guard.Deadline(30)
    world.cache.max_files = 3
    held = []
    refused = None
    for _ in range(6):
        world.bump()
        try:
            with closing(world.source()) as source:
                held.append(world.cache.acquire(source, deadline))
        except query_snapshots.SnapshotUnavailable as exc:
            refused = exc
            break
    assert refused is not None and str(refused) == query_snapshots.BUSY_TEXT
    assert len(world.files()) <= 3 and not world.cache._pending
    for entry in held:
        world.cache.release(entry)
    assert world.sql("SELECT 1")["rows"] == [(1,)]


def test_an_unexpected_error_in_the_builder_never_leaves_the_cache_stuck(world, monkeypatch):
    # 034 build M2: a non-Exception failure used to leave the build open, so every later query waited out its deadline.
    real = compatibility.build

    def boom(source, path, deadline):
        raise KeyboardInterrupt("simulated")

    monkeypatch.setattr(compatibility, "build", boom)
    with pytest.raises(query_snapshots.SnapshotUnavailable):
        world.sql("SELECT 1")
    assert world.cache._build is None
    monkeypatch.setattr(compatibility, "build", real)
    assert world.sql("SELECT 1")["rows"] == [(1,)]


def test_a_failed_folder_creation_releases_the_lock_and_the_next_call_recovers(world, monkeypatch):
    # 034 build M3: the lock descriptor leaked and made every retry fail against itself.
    cache = world.cache
    real = Path.mkdir
    calls = []

    def failing(self, *args, **kwargs):
        if self.name == cache._name and not calls:
            calls.append(1)
            raise OSError("no space")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failing)
    with pytest.raises(query_snapshots.SnapshotUnavailable, match="no space"):
        world.sql("SELECT 1")
    assert cache._lock_fd is None and cache._folder is None
    assert world.sql("SELECT 1")["rows"] == [(1,)]


def test_the_sweep_removes_an_orphan_lock_file_of_a_dead_process(world):
    base = ownership.private_directory_at(world.path.parent / "query-snapshots")
    lock = base / "p-444-dddddddddddddddd.lock"
    os.close(ownership.take_lock_file(lock))
    old = time.time() - 3600
    os.utime(lock, (old, old))
    world.sql("SELECT 1")
    assert not lock.exists()


def test_churn_never_leaves_more_files_than_the_bound(world):
    def one(i):
        world.bump()
        world.sql("SELECT count(*) FROM entities")

    with ThreadPoolExecutor(4) as pool:
        for batch in range(6):
            list(pool.map(one, range(4)))
            assert len(world.files()) <= world.cache.max_files, world.files()
