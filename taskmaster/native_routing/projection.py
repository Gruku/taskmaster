# User intent: keep the git-facing files exactly as visible as the legacy tools
# made them while commands run on the native core (N08), without inventing the
# durable outbox protocol that N11 owns — one synchronous drain after each command.
"""Synchronous compatibility drain of native `projection_jobs`.

This is the explicit export operation the N08 bypass gate allowlists; it is the
only native-routing code that reads or writes projection files. It renders with
the legacy exporter's own functions (`store.render_entity_file`,
`store.render_backlog_file`, `render_ideas_index`), so one entity cannot be
rendered two ways.

Ordering and staleness: the whole drain holds the database writer lock, so jobs
are applied in commit order and a slower drain can never write an older revision
over a newer one. A file write that fails, or a quarantined file, leaves its job
`pending` and reports `export pending: …` exactly as the legacy exporter warns;
the next drain retries it. Leases, expected-base checks and asynchronous export
belong to N11 and are deliberately absent.

Whole derived files have no per-entity job, so their freshness is the highest
`last_seq` among their inputs against the `exported_seq` recorded for the file:
`backlog.yaml` (backlog, epic and phase rows) and `ideas/IDEAS.md` (idea rows).
The PROGRESS.md dashboard and changelog are not rendered here (N11).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import random
import sqlite3
import threading
import time

from taskmaster import store
from taskmaster.native import db as native_db
from taskmaster.native.migrate import rows
from taskmaster.native.queries import Snapshot, _core_columns
from taskmaster.taskmaster_v3 import BODY_KEY, render_ideas_index

from . import derived

BACKLOG_FILE = "backlog.yaml"
_BACKLOG_ID = "__backlog__"
_DOMINANT: dict[Path, bool] = {}
_DOMINANT_LOCK = threading.Lock()


def _dominant_crlf(backlog_dir: Path) -> bool:
    with _DOMINANT_LOCK:
        if backlog_dir not in _DOMINANT:
            _DOMINANT[backlog_dir] = store.detect_dominant_crlf(backlog_dir)
        return _DOMINANT[backlog_dir]


def reset_for_tests() -> None:
    with _DOMINANT_LOCK:
        _DOMINANT.clear()


def _relative(rel: str) -> PurePosixPath:
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe projection path {rel!r}")
    return path


def _replace_bytes(path: Path, content: bytes, session: str) -> os.stat_result:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{session}")
    with temp.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    deadline = None
    while True:
        try:
            os.replace(temp, path)
            return path.stat()
        except OSError as exc:
            if exc.errno not in store._RETRYABLE_REPLACE_ERRNOS:
                temp.unlink(missing_ok=True)
                raise
            deadline = deadline or time.monotonic() + 2.0
            if time.monotonic() >= deadline:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(random.uniform(0.02, 0.08))


def _record(connection, rel, kind, ident, content, stat, exported_seq):
    connection.execute(
        "INSERT INTO projection(file,kind,id,content_hash,mtime,size,dirty,quarantined,exported_seq) "
        "VALUES(?,?,?,?,?,?,0,0,?) ON CONFLICT(file) DO UPDATE SET kind=excluded.kind,id=excluded.id,"
        "content_hash=excluded.content_hash,mtime=excluded.mtime,size=excluded.size,dirty=0,quarantined=0,"
        "exported_seq=excluded.exported_seq",
        (rel, kind, ident, hashlib.sha1(content).hexdigest(), stat.st_mtime, stat.st_size, exported_seq))
    connection.execute("DELETE FROM projection_base WHERE file=?", (rel,))


def flagged_files(connection) -> tuple[str, ...]:
    """Files the legacy store flagged (B-089) and nobody resolved before activation."""
    try:
        return tuple(r[0] for r in connection.execute("SELECT file FROM projection_conflict ORDER BY file"))
    except sqlite3.OperationalError:
        # Activated from a store no 6.0.3 writer opened: there is no flag table.
        return ()


def _flagged(connection, rel: str) -> bool:
    try:
        return connection.execute("SELECT 1 FROM projection_conflict WHERE file=?", (rel,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def _held_files(connection, kind: str, ident: str) -> list[tuple[str, str]]:
    """`(file, reason)` for every quarantined or flagged file of one entity."""
    held = {file: "quarantined" for (file,) in connection.execute(
        "SELECT file FROM projection WHERE kind=? AND id=? AND quarantined=1", (kind, ident))}
    try:
        held.update((file, "flagged") for (file,) in connection.execute(
            "SELECT file FROM projection_conflict WHERE kind=? AND id=?", (kind, ident)))
    except sqlite3.OperationalError:
        pass
    return sorted(held.items())


class _Drain:
    def __init__(self, connection: sqlite3.Connection, backlog_dir: Path, session: str):
        self.connection, self.backlog_dir, self.session = connection, backlog_dir, session
        self.warnings: list[str] = []
        self.moved_crlf: dict[tuple[str, str], bool] = {}
        self.held: dict[tuple[str, str], bool] = {}

    def _blocked(self, rel: str) -> bool:
        row = self.connection.execute("SELECT quarantined FROM projection WHERE file=?", (rel,)).fetchone()
        if row and row[0]:
            self.connection.execute("UPDATE projection SET dirty=1 WHERE file=?", (rel,))
            self.warnings.append(f"export pending: {rel} is quarantined")
            return True
        if _flagged(self.connection, rel):
            # Flagged by the legacy store before activation (B-089): the file and
            # the store both changed and nobody has chosen. Writing or removing
            # it here would discard the file's side, so it waits like a
            # quarantined file does.
            self.connection.execute("UPDATE projection SET dirty=1 WHERE file=?", (rel,))
            self.warnings.append(f"export pending: {rel} is flagged")
            return True
        return False

    def _remove(self, rel: str, kind: str, ident: str) -> bool:
        path = self.backlog_dir / _relative(rel)
        if path.exists():
            probe = store._probe_crlf(path)
            if probe is not None:
                self.moved_crlf[(kind, ident)] = probe
            try:
                path.unlink()
            except OSError:
                self.connection.execute("UPDATE projection SET dirty=1,quarantined=0 WHERE file=?", (rel,))
                self.warnings.append(f"export pending: {rel} — retried on next call")
                return False
        self.connection.execute("DELETE FROM projection WHERE file=?", (rel,))
        self.connection.execute("DELETE FROM projection_base WHERE file=?", (rel,))
        return True

    def _write(self, rel: str, kind: str, ident: str | None, content: bytes, exported_seq: int,
               default_crlf: bool | None = None) -> bool:
        path = self.backlog_dir / _relative(rel)
        if default_crlf is None:
            default_crlf = _dominant_crlf(self.backlog_dir)
        content = store._match_line_endings(content, path, default_crlf)
        digest = hashlib.sha1(content).hexdigest()
        existing = self.connection.execute("SELECT content_hash FROM projection WHERE file=?", (rel,)).fetchone()
        if existing and existing[0] == digest and path.exists():
            stat = path.stat()
            self.connection.execute("UPDATE projection SET mtime=?,size=?,dirty=0,exported_seq=? WHERE file=?",
                                    (stat.st_mtime, stat.st_size, exported_seq, rel))
            return True
        try:
            stat = _replace_bytes(path, content, self.session)
        except OSError:
            if existing:
                self.connection.execute("UPDATE projection SET dirty=1,quarantined=0 WHERE file=?", (rel,))
            self.warnings.append(f"export pending: {rel} — retried on next call")
            return False
        _record(self.connection, rel, kind, ident, content, stat, exported_seq)
        return True

    def _entity_held(self, kind: str, ident: str) -> bool:
        """Whether any file of this entity is quarantined or flagged.

        Held per entity, as the legacy `_export_blocked` does: a move writes the
        new path and removes the old, so holding only the blocked file would
        leave one id in two places. Answered once per entity per drain.
        """
        key = (kind, ident)
        if key not in self.held:
            held = _held_files(self.connection, kind, ident)
            for file, reason in held:
                self.connection.execute("UPDATE projection SET dirty=1 WHERE file=?", (file,))
                self.warnings.append(f"export pending: {file} is {reason}")
            self.held[key] = bool(held)
        return self.held[key]

    def job(self, rel: str, effect: str, entity: dict) -> bool:
        kind, ident = entity["kind"], entity["id"]
        if self._entity_held(kind, ident) or self._blocked(rel):
            return False
        if effect == "delete":
            return self._remove(rel, kind, ident)
        content, _oracle = store.render_entity_file(kind, entity["fields"], entity.get("body"))
        if content is None:
            return self._remove(rel, kind, ident)
        return self._write(rel, kind, ident, content, int(entity["last_seq"]),
                           default_crlf=self.moved_crlf.get((kind, ident)))

    def _snapshot(self) -> Snapshot:
        return Snapshot(self.connection, native_db.manifest(self.connection, authorities=("native",)))

    def _stale(self, rel: str, kinds: tuple[str, ...]) -> int | None:
        placeholders = ",".join("?" for _ in kinds)
        high = self.connection.execute(
            f"SELECT COALESCE(MAX(last_seq),0) FROM entity_core WHERE kind IN ({placeholders})", kinds).fetchone()[0]
        row = self.connection.execute("SELECT exported_seq FROM projection WHERE file=?", (rel,)).fetchone()
        if row is not None and row[0] is not None and int(row[0]) >= int(high):
            return None
        return int(self.connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0])

    def _entities(self, snapshot: Snapshot, kind: str, order: str) -> list[dict]:
        cores = rows(self.connection, "SELECT " + _core_columns(None) +
                     f" FROM entity_core WHERE kind=? AND deleted=0 ORDER BY {order}", (kind,))
        return snapshot._assemble(cores, None, include_body=True)

    def derived(self) -> None:
        seq = self._stale(BACKLOG_FILE, ("backlog", "epic", "phase") + derived.KINDS)
        if seq is not None and not self._blocked(BACKLOG_FILE):
            snapshot = self._snapshot()
            try:
                backlog = self._entities(snapshot, "backlog", "entity_key")
                data = derived.apply(snapshot, dict(backlog[0]["fields"]) if backlog else {})
                for kind, key in (("epic", "epics"), ("phase", "phases")):
                    data[key] = []
                    for entity in self._entities(snapshot, kind, "entity_key"):
                        document = dict(entity["fields"])
                        if entity.get("body"):
                            document[BODY_KEY] = entity["body"]
                        data[key].append(document)
            finally:
                snapshot.active = False
            content, _document = store.render_backlog_file(data)
            self._write(BACKLOG_FILE, "backlog", None, content, seq)
        seq = self._stale(store._IDEAS_INDEX_REL, ("idea",))
        if seq is not None and not self._blocked(store._IDEAS_INDEX_REL):
            snapshot = self._snapshot()
            try:
                entries = [entity["fields"] for entity in self._entities(snapshot, "idea", "public_id")]
            finally:
                snapshot.active = False
            if entries or (self.backlog_dir / store._IDEAS_INDEX_REL).exists():
                content = render_ideas_index(entries).encode("utf-8")
                self._write(store._IDEAS_INDEX_REL, store._IDEAS_INDEX_KIND, None, content, seq)


def drain(connection: sqlite3.Connection, backlog_dir: Path, *, session: str) -> list[str]:
    """Apply every pending projection job, then refresh stale derived files.

    Returns the `export pending: …` notices a caller must surface. Requires a
    connection with no open transaction; holds the writer lock throughout.
    """
    if connection.in_transaction:
        raise RuntimeError("projection drain requires its own transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        native_db.assert_native(connection)
        worker = _Drain(connection, backlog_dir, session)
        jobs = connection.execute(
            "SELECT job_key,file,effect,input_json FROM projection_jobs WHERE state='pending' "
            "ORDER BY commit_seq,job_key").fetchall()
        latest: dict[str, int] = {}
        for job_key, rel, _effect, _input in jobs:
            latest[rel] = job_key
        exported, superseded = [], []
        for job_key, rel, effect, input_json in jobs:
            if latest[rel] != job_key:
                superseded.append(job_key)
            elif worker.job(rel, effect, json.loads(input_json)):
                exported.append(job_key)
        connection.executemany("UPDATE projection_jobs SET state='superseded' WHERE job_key=?",
                               [(key,) for key in superseded])
        connection.executemany("UPDATE projection_jobs SET state='exported' WHERE job_key=?",
                               [(key,) for key in exported])
        worker.derived()
        connection.commit()
        return worker.warnings
    except BaseException:
        connection.rollback()
        raise
