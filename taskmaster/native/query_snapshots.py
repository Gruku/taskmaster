# User intent: backlog_query reads one shared, read-only snapshot file per store revision instead of copying the whole
# store on every call, so 8 sessions (or 60 agents) can query at once without starving each other, while keeping every
# security property of the old per-call copy (team-server-034 design).
"""The query snapshot cache: one read-only database file of the legacy SQL surface per store revision.

`QuerySnapshots` owns, per store file and process, a private directory of published snapshot files, a single-flight
builder thread and leases. A call asks `acquire` for a snapshot that includes everything it could have seen (its own
committed writes included), runs its statement in a `query_worker` child against the file, and `release`s the lease.
Nothing is watched: a newer snapshot supersedes older ones and an unleased old file is deleted at once, a leased one
when its last lease ends.

Security notes that the code below depends on (design 4.2-4.4):
- A file is built under a name ending `.partial` and renamed to its published name only when complete and read-only;
  the worker refuses every other name, so no worker ever sees partial state.
- The base is the same for every caller (members see every entity), so it is shared. Host-private rows never enter
  it (see `compatibility.build`); if members ever see only some entities, the base must be keyed by visibility class.
- The key covers the event revision, graph repairs and a fingerprint of the derived tables the base copies, read in the
  same read transaction as the copy.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sqlite3
import stat
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path

from taskmaster import query_guard
from . import query_worker

# Bumped when the surface or VALUE_COPY_MAX changes; files never outlive the process, so this only guards tests.
SNAPSHOT_FORMAT = 1
# Its own deadline, not a caller's: a caller that gives up must not waste a build others are waiting for.
BUILD_SECONDS = 15.0
# Builds are spaced at least this far apart (caps the build duty cycle under write churn).
BUILD_MIN_INTERVAL_TEAM = 1.0
BUILD_MIN_INTERVAL_LOCAL = 0.25
# A build is refused below this much free space.
DISK_FLOOR = 4 * query_worker.SNAPSHOT_MAX_BYTES + 512 * 1024 * 1024
# The start sweep only deletes a dead process's folder once its lock file is this old (a process takes its lock
# before it creates its folder, so a young lock may belong to a process that is just starting).
SWEEP_MIN_AGE_SECONDS = 600.0
DERIVED_TABLES = ("entity_paths", "links", "related", "handover_tasks", "document_search_docsize")
CLEANUP_TEXT = "query snapshots are waiting for cleanup; try again shortly"
BUSY_TEXT = "too many query snapshots are in use by running statements; try again shortly"


class SnapshotUnavailable(sqlite3.OperationalError):
    """No snapshot could be provided right now (build failed, disk low, cleanup pending, cache closed)."""


class _Build:
    def __init__(self):
        self.key = None
        self.error = None           # (kind, message) once failed
        self.done = False


class Entry:
    """One published snapshot file."""

    def __init__(self, path, store_id, revision, fingerprint, started, counts):
        self.path, self.store_id, self.revision, self.fingerprint = path, store_id, revision, fingerprint
        self.started, self.counts = started, counts
        self.leases = 0
        self.superseded = False
        self.dead = False

    @property
    def key(self):
        return (self.store_id, self.revision, self.fingerprint)


def read_key(connection):
    """(store_id, revision, fingerprint) of the store as `connection`'s current read transaction sees it."""
    from taskmaster.native_routing import hook_reads
    row = connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()
    store_id = row[0] if row else ""
    revision = hook_reads.revision(connection)
    parts = []
    for table in DERIVED_TABLES:
        try:
            count, top = connection.execute(f'SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM "{table}"').fetchone()
        except sqlite3.Error:
            count, top = connection.execute(f'SELECT COUNT(*), 0 FROM "{table}"').fetchone()
        parts.append((count, top))
    return store_id, revision, (SNAPSHOT_FORMAT, tuple(parts))


def _remove(path) -> bool:
    """Delete one file; False when it could not be (Windows: still open in a worker). The read-only attribute that
    protects a published file is cleared first, because Windows refuses to delete a read-only file."""
    try:
        if not os.path.islink(path):        # chmod follows links: never touch a link's target
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except FileNotFoundError:
        return True
    except OSError:
        pass
    try:
        os.unlink(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


class QuerySnapshots:
    def __init__(self, database, *, min_interval=None, max_files=None):
        self.database = Path(database)
        self.min_interval = ((BUILD_MIN_INTERVAL_TEAM if query_guard.functions_denied() else BUILD_MIN_INTERVAL_LOCAL)
                             if min_interval is None else float(min_interval))
        # current + one building + the superseded files a running statement still holds
        self.max_files = int(max_files or query_worker.MAX_CONCURRENT + 2)
        self.builds = 0                 # completed build attempts (tests and the resume file's measurements)
        self._cv = threading.Condition()
        self._current = None
        self._build = None
        self._thread = None
        self._entries = set()           # every file on disk that is published: current plus leased superseded
        self._pending = set()           # paths whose delete failed; they count toward the disk bound
        self._failed = None             # (key, message): a store too large stays refused until it changes
        self._closed = False
        self._last_end = 0.0
        self._name = f"p-{os.getpid()}-{uuid.uuid4().hex[:16]}"
        self._folder = None
        self._lock_path = None
        self._lock_fd = None

    # -- callers ----------------------------------------------------------------------------------

    def acquire(self, source, deadline):
        """A leased Entry whose snapshot includes everything `source`'s read transaction sees. Waits for a build
        under `deadline`; raises StoreTooLarge, SnapshotUnavailable, or OperationalError("interrupted")."""
        from .compatibility import StoreTooLarge
        self._retry()
        store_id, revision, fingerprint = read_key(source)
        # Taken after the key: a build that starts after this includes every write the key counts.
        arrival = time.monotonic()
        while True:
            with self._cv:
                if self._closed:
                    raise SnapshotUnavailable("the query snapshot cache is closed")
                entry = self._current
                if (entry is not None and not entry.dead and entry.store_id == store_id
                        and ((entry.revision, entry.fingerprint) == (revision, fingerprint)
                             or (entry.revision > revision and entry.started >= arrival))):
                    entry.leases += 1
                    return entry
                build = self._build
                if build is None:
                    if self._failed is not None and self._failed[0] == (store_id, revision, fingerprint):
                        raise StoreTooLarge(self._failed[1])
                    # The bound holds always, not only while deletes pend: callers waiting for a worker slot hold leases.
                    if len(self._entries) + len(self._pending) + 1 > self.max_files:
                        raise SnapshotUnavailable(CLEANUP_TEXT if self._pending else BUSY_TEXT)
                    build = self._build = _Build()
                    self._thread = threading.Thread(target=self._run_build, args=(build,), daemon=True,
                                                    name="query-snapshot-builder")
                    self._thread.start()
                while not build.done:
                    left = deadline.expires_at - time.monotonic()
                    if left <= 0 or deadline():
                        deadline.expired = True
                        raise sqlite3.OperationalError("interrupted")
                    self._cv.wait(min(left, 0.5))
                error = build.error
            if error is not None:
                # The build this call waited for failed: that is its answer too (the next call retries).
                kind, message = error
                raise (StoreTooLarge if kind == "too_large" else SnapshotUnavailable)(message)

    def release(self, entry):
        with self._cv:
            entry.leases -= 1
            delete = entry.leases <= 0 and (entry.superseded or entry.dead)
            if delete:
                self._entries.discard(entry)
        if delete:
            self._delete(entry.path)
        self._retry()

    def drop(self, entry):
        """The file could not be opened by a worker: stop serving it."""
        with self._cv:
            entry.dead = True
            if self._current is entry:
                self._current = None
            delete = entry.leases <= 0
            if delete:
                self._entries.discard(entry)
        if delete:
            self._delete(entry.path)

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(BUILD_SECONDS + 5)
        with self._cv:
            paths = [e.path for e in self._entries]
            self._entries.clear()
            self._current = None
        for path in paths:
            self._delete(path)
        self._retry()
        if self._folder is not None:
            try:
                self._folder.rmdir()
            except OSError:
                pass                    # a file still open in a worker: the next start's sweep takes it
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None
            if self._folder is None or not self._folder.exists():
                _remove(self._lock_path)

    # -- files ------------------------------------------------------------------------------------

    def _delete(self, path):
        if not _remove(path):
            with self._cv:
                self._pending.add(path)

    def _retry(self):
        with self._cv:
            pending = list(self._pending)
        for path in pending:
            if _remove(path):
                with self._cv:
                    self._pending.discard(path)

    def _ensure_folder(self):
        if self._folder is not None:
            return self._folder
        from taskmaster.coordinator import ownership
        base = ownership.private_directory_at(self.database.parent / "query-snapshots")
        # Lock first, folder second, so a sweeper that finds the folder finds a held lock (or an old dead one).
        self._lock_path = base / f"{self._name}.lock"
        self._lock_fd = ownership.take_lock_file(self._lock_path)
        folder = base / self._name
        try:
            folder.mkdir(mode=0o700)
        except BaseException:
            # Else a retry would take the same lock again through a new descriptor and fail against itself for good.
            os.close(self._lock_fd)
            self._lock_fd = None
            _remove(self._lock_path)
            raise
        self._folder = folder
        self._sweep(base)
        return folder

    def _sweep(self, base):
        """Delete the folders of dead processes: lock not held and older than SWEEP_MIN_AGE_SECONDS. A live
        process's folder, and a young one, are never touched."""
        from taskmaster.coordinator import ownership
        now = time.time()
        try:
            names = os.listdir(base)
        except OSError:
            return
        for name in names:
            if not name.startswith("p-") or name == self._name or name == self._name + ".lock":
                continue
            lock = base / (name[:-5] + ".lock" if name.endswith(".lock") else name + ".lock")
            if ownership.lock_file_held(lock):
                continue
            try:
                age = now - (lock.stat().st_mtime if lock.exists() else (base / name).stat().st_mtime)
            except OSError:
                continue
            if age < SWEEP_MIN_AGE_SECONDS:
                continue
            folder = lock.with_suffix("")
            if folder.is_dir():
                for child in folder.iterdir():
                    _remove(child)
                try:
                    folder.rmdir()
                except OSError:
                    continue
            if not folder.exists():
                _remove(lock)

    # -- builder ----------------------------------------------------------------------------------

    def _run_build(self, build):
        from .compatibility import StoreTooLarge
        entry = error = None
        try:
            with self._cv:
                while not self._closed and self._last_end + self.min_interval > time.monotonic():
                    self._cv.wait(self._last_end + self.min_interval - time.monotonic())
                if self._closed:
                    raise SnapshotUnavailable("the query snapshot cache is closed")
            entry = self._build_entry(build)
        except StoreTooLarge as exc:
            error = ("too_large", str(exc))
        except SnapshotUnavailable as exc:
            error = ("unavailable", str(exc))
        except sqlite3.OperationalError as exc:
            error = ("unavailable", f"query snapshot could not be built: "
                     f"{'it ran past ' + format(BUILD_SECONDS, 'g') + ' s' if str(exc) == 'interrupted' else exc}")
        except BaseException as exc:  # noqa: BLE001 - every failure reaches the waiters as an error, never a hang
            error = ("unavailable", f"query snapshot could not be built: {exc}")
        stale = []
        with self._cv:
            self.builds += 1
            self._last_end = time.monotonic()
            if entry is not None:
                old = self._current
                self._current = entry
                self._entries.add(entry)
                if old is not None:
                    old.superseded = True
                    if old.leases <= 0:
                        self._entries.discard(old)
                        stale.append(old.path)
                if self._closed:
                    self._entries.discard(entry)
                    stale.append(entry.path)
            else:
                build.error = error
                if error[0] == "too_large" and build.key is not None:
                    self._failed = (build.key, error[1])
            build.done = True
            self._build = None
            self._cv.notify_all()
        for path in stale:
            self._delete(path)
        self._retry()

    def _build_entry(self, build):
        from . import compatibility
        folder = self._ensure_folder()
        free = shutil.disk_usage(folder).free
        if free < DISK_FLOOR:
            raise SnapshotUnavailable(f"the disk has {free // 2**20} MiB free, below the {DISK_FLOOR // 2**20} MiB "
                                      "a query snapshot needs; free space or use another tool")
        deadline = query_guard.Deadline(BUILD_SECONDS)
        partial = final = None
        try:
            with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, isolation_level=None,
                                         timeout=30)) as source:
                source.execute("PRAGMA query_only=ON")
                source.set_progress_handler(deadline, query_guard.PROGRESS_INSTRUCTIONS)
                started = time.monotonic()
                source.execute("BEGIN")
                try:
                    store_id, revision, fingerprint = build.key = read_key(source)
                    partial = folder / f"r{revision}-{uuid.uuid4().hex[:16]}.partial"
                    counts = compatibility.build(source, partial, deadline)
                finally:
                    source.rollback()
            os.chmod(partial, stat.S_IREAD)
            final = partial.with_suffix(".db")
            os.replace(partial, final)
            partial = None
            return Entry(final, store_id, revision, fingerprint, started, counts)
        except BaseException:
            for path in (partial, final):
                if path is not None:
                    self._delete(path)
            raise


# -- one cache per store file per process ----------------------------------------------------------

_REGISTRY: dict = {}
_REGISTRY_LOCK = threading.Lock()


def for_source(connection):
    """The cache of the store file `connection` reads."""
    path = connection.execute("PRAGMA database_list").fetchone()[2]
    if not path:
        raise ValueError("backlog_query needs a store file, not an in-memory database")
    key = os.path.normcase(os.path.realpath(path))
    with _REGISTRY_LOCK:
        snapshots = _REGISTRY.get(key)
        if snapshots is None:
            snapshots = _REGISTRY[key] = QuerySnapshots(Path(path))
        return snapshots


def close_all():
    """Delete every cache's files (the serve daemon's stop, the process exit, a test's end)."""
    with _REGISTRY_LOCK:
        caches = list(_REGISTRY.values())
        _REGISTRY.clear()
    for cache in caches:
        cache.close()


atexit.register(close_all)
