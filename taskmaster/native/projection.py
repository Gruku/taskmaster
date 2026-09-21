# User intent: make native file export a durable, recoverable outbox (N11): committed
# jobs are claimed under one store-wide exporter lease, published outside any database
# lock, acknowledged one by one, and can never let a stale writer overwrite newer bytes.
"""Projection outbox protocol: leasing, claims, recovery, publication and acks.

This module renders nothing. Callers pass the bytes to publish for each claimed job
(`native_routing.projection` renders them with the legacy exporter's functions), so
one protocol serves the synchronous compatibility drain now and N12's service later.

Concurrency control is one store-wide exporter lease in `sync_state`
(`projection_exporter = {owner, generation, until}`), taken in the claim transaction
and renewed at each ack. Every write the exporter makes is fenced by its generation:
an exporter whose lease was taken over records nothing. Per-job `lease_owner` and
`lease_until` only say which generation claimed a job; taking the lease recovers every
job an earlier generation left `claimed`, because nobody else can hold them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import random
import sqlite3
import time
from typing import Callable, Iterable

LEASE_SECONDS = 30.0
# Sharing violations an indexer or antivirus causes on Windows; the replace is retried.
_RETRYABLE_REPLACE_ERRNOS = {5, 13, 32, errno.EACCES, errno.EPERM}
EXPORTER_KEY = "projection_exporter"
THROUGH_KEY = "exported_through"
# Per file, the last few hashes an exporter was about to publish there (see `intend`).
OWN_PREFIX = "projection.own."
OWN_RING = 8
# The legacy store's B-089 flag table. A store no 6.0.3 writer opened lacks it; the
# first native flag creates it with the legacy definition, the only DDL here.
_CONFLICT_DDL = ("CREATE TABLE IF NOT EXISTS projection_conflict(file TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                 "id TEXT, flagged_at TEXT NOT NULL, file_hash TEXT NOT NULL, file_content BLOB NOT NULL)")


class LeaseLost(RuntimeError):
    """This exporter's lease generation is no longer the current one."""


@dataclass
class Job:
    key: int
    entity_key: int | None
    commit_seq: int
    file: str
    effect: str
    entity: dict
    kind: str = field(init=False)
    id: str = field(init=False)
    # The file this entity is moving away from, when the same claim removes it.
    moved_from: str | None = None

    def __post_init__(self):
        self.kind, self.id = self.entity["kind"], self.entity["id"]


def _digest(content: bytes) -> str:
    return hashlib.sha1(content).hexdigest()


def ensure_conflict_table(connection) -> None:
    connection.execute(_CONFLICT_DDL)


def _get(connection, key, default=None):
    row = connection.execute("SELECT value_json FROM sync_state WHERE key=?", (key,)).fetchone()
    return default if row is None else json.loads(row[0])


def _put(connection, key, value) -> None:
    connection.execute("INSERT INTO sync_state(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                       "value_json=excluded.value_json", (key, json.dumps(value, sort_keys=True)))


def lease(connection) -> dict:
    """The exporter lease as stored: `{owner, generation, until}` (empty before any export)."""
    return _get(connection, EXPORTER_KEY, {})


def exported_through(connection) -> int:
    """The highest commit sequence whose projection jobs are all exported (held entities aside)."""
    return int(_get(connection, THROUGH_KEY, 0))


def _held_entity_sql() -> str:
    """Held-entity predicate over `projection_jobs j` (see `held`)."""
    return ("EXISTS(SELECT 1 FROM entity_core e WHERE e.entity_key=j.entity_key AND ("
            "EXISTS(SELECT 1 FROM projection p WHERE p.kind=e.kind AND p.id=e.public_id AND p.quarantined=1)"
            " OR EXISTS(SELECT 1 FROM projection_conflict c WHERE c.kind=e.kind AND c.id=e.public_id)))"
            " OR EXISTS(SELECT 1 FROM projection p WHERE p.file=j.file AND p.quarantined=1)"
            " OR EXISTS(SELECT 1 FROM projection_conflict c WHERE c.file=j.file)")


def _has_conflict_table(connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='projection_conflict'").fetchone() is not None


def _through(connection) -> int:
    """Recompute the watermark: everything below the oldest unexported, unheld job."""
    held = f" AND NOT ({_held_entity_sql()})" if _has_conflict_table(connection) else (
        " AND NOT EXISTS(SELECT 1 FROM entity_core e JOIN projection p ON p.kind=e.kind AND p.id=e.public_id "
        "WHERE e.entity_key=j.entity_key AND p.quarantined=1)")
    oldest = connection.execute(
        f"SELECT MIN(j.commit_seq) FROM projection_jobs j WHERE j.state IN ('pending','claimed'){held}").fetchone()[0]
    if oldest is not None:
        return int(oldest) - 1
    return int(connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0])


def _update_through(connection) -> None:
    _put(connection, THROUGH_KEY, _through(connection))


class Exporter:
    """One exporter attempt: claim, publish each job, ack each job, finish.

    `checkpoint(stage, file)` is called at every protocol boundary so tests can
    inject a crash or an interleaving there; it does nothing in production.
    """

    def __init__(self, connection: sqlite3.Connection, backlog_dir: Path, *, owner: str, session: str,
                 clock: Callable[[], float] = time.time, lease: float = LEASE_SECONDS,
                 checkpoint: Callable[[str, str], None] | None = None):
        self.connection, self.backlog_dir = connection, backlog_dir
        self.owner, self.session, self.clock, self.lease_seconds = owner, session, clock, lease
        self.checkpoint = checkpoint or (lambda stage, file: None)
        self.generation: int | None = None
        self.warnings: list[str] = []
        self.acked: set[str] = set()

    # ── Transactions ────────────────────────────────────────────────────────

    def _begin(self) -> None:
        if self.connection.in_transaction:
            raise RuntimeError("the projection outbox requires its own transactions")
        self.connection.execute("BEGIN IMMEDIATE")

    def _fenced(self) -> None:
        """Inside a write transaction: refuse unless this generation still owns the lease."""
        current = lease(self.connection)
        if self.generation is None or current.get("owner") != self.owner or current.get("generation") != self.generation:
            raise LeaseLost(f"exporter {self.owner} generation {self.generation} lost the lease")
        _put(self.connection, EXPORTER_KEY, dict(current, until=self.clock() + self.lease_seconds))

    def _owns(self) -> bool:
        """Outside a transaction: whether this generation still owns the lease (a read, no lock)."""
        current = lease(self.connection)
        return self.generation is not None and current.get("owner") == self.owner and \
            current.get("generation") == self.generation

    @property
    def _lease_owner(self) -> str:
        return f"{self.owner}#{self.generation}"

    # ── Claim ───────────────────────────────────────────────────────────────

    def claim(self) -> list[Job] | None:
        """Take (or renew) the exporter lease and claim the latest pending job per file.

        Returns None, claiming nothing, while another exporter's lease is live.
        """
        self._begin()
        try:
            now = self.clock()
            current = lease(self.connection)
            if current.get("owner") not in (None, self.owner) and float(current.get("until", 0)) > now:
                self.connection.rollback()
                return None
            if current.get("owner") != self.owner or current.get("generation") != self.generation:
                self.generation = int(current.get("generation", 0)) + 1
                # Whatever an earlier generation claimed and did not ack goes back,
                # keeping its job_key and commit_seq, so ordering is unchanged.
                self.connection.execute("UPDATE projection_jobs SET state='pending',lease_owner=NULL,lease_until=NULL "
                                        "WHERE state='claimed'")
            until = now + self.lease_seconds
            _put(self.connection, EXPORTER_KEY, {"owner": self.owner, "generation": self.generation, "until": until})
            jobs = self._claim_latest(until)
            self.checkpoint("claim", "")
            self.connection.commit()
            return jobs
        except BaseException:
            self.connection.rollback()
            raise

    def _claim_latest(self, until: float) -> list[Job]:
        pending = self.connection.execute(
            "SELECT job_key,entity_key,commit_seq,file,effect,input_json FROM projection_jobs "
            "WHERE state='pending' ORDER BY commit_seq,job_key").fetchall()
        latest: dict[str, tuple] = {}
        for row in pending:
            latest[row[3]] = row
        superseded = [(row[0],) for row in pending if latest[row[3]][0] != row[0]]
        self.connection.executemany("UPDATE projection_jobs SET state='superseded' WHERE job_key=?", superseded)
        jobs = [Job(key, entity_key, seq, rel, effect, json.loads(payload))
                for key, entity_key, seq, rel, effect, payload in latest.values()]
        self.connection.executemany(
            "UPDATE projection_jobs SET state='claimed',lease_owner=?,lease_until=? WHERE job_key=?",
            [(self._lease_owner, until, job.key) for job in jobs])
        return self._ordered(jobs)

    @staticmethod
    def _ordered(jobs: list[Job]) -> list[Job]:
        """Writes before removals, each in commit order.

        A move commits a delete of the old path and a write of the new one. Writing
        first means a crash between the two leaves the id in two places, which is
        visible and heals on recovery, rather than in none.
        """
        removals = {job.entity_key: job.file for job in jobs if job.effect == "delete"}
        for job in jobs:
            if job.effect != "delete":
                job.moved_from = removals.get(job.entity_key)
        return sorted(jobs, key=lambda job: (job.effect == "delete", job.commit_seq, job.key))

    # ── Publication ─────────────────────────────────────────────────────────

    def intend(self, rendered: Iterable[tuple[Job | str, bytes | None]]) -> None:
        """Record, fenced and before any file is touched, the bytes about to be published.

        Each file keeps a short ring of the hashes exporters were about to write
        there (`projection.own.<file>`). Verification treats bytes in that ring as
        the exporter's own: a crash after a replace, or a paused exporter that
        replaced after losing its lease, leaves them on disk, and they are safe to
        overwrite. Re-rendering retained jobs cannot answer this, because retention
        deletes exactly the superseded job whose bytes a paused exporter publishes.
        """
        self._begin()
        try:
            self._fenced()
            for target, content in rendered:
                if content is None:
                    continue
                rel = target if isinstance(target, str) else target.file
                key = OWN_PREFIX + rel
                ring = [h for h in _get(self.connection, key, []) if h != _digest(content)]
                _put(self.connection, key, (ring + [_digest(content)])[-OWN_RING:])
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def publish(self, job: Job, content: bytes | None) -> str:
        """Publish one claimed job's bytes (None removes its file), then ack it.

        Returns `exported`; `flagged` (the file changed since it was last written:
        both versions are kept and the entity's exports pause); `failed` (the
        filesystem refused, retried by the next drain, with a warning); or `lost`
        (this exporter no longer owns the lease and recorded nothing).
        """
        if job.effect == "delete":
            content = None
        return self._publish_file(job.file, job.kind, job.id, content, int(job.entity["last_seq"]), job=job)

    def publish_derived(self, rel: str, kind: str, content: bytes | None, exported_seq: int) -> str:
        """Publish a whole derived file (`backlog.yaml`, `ideas/IDEAS.md`), which has no job."""
        return self._publish_file(rel, kind, None, content, exported_seq, tag=f"d{exported_seq}")

    def _path(self, rel: str) -> Path:
        path = PurePosixPath(rel)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError(f"unsafe projection path {rel!r}")
        return self.backlog_dir / path

    def _classify(self, rel: str, path: Path, content: bytes | None) -> tuple[str, bytes | None]:
        """§2.4: what the bytes on disk say about publishing over them.

        `agrees` (the file already holds exactly this content), `publish`, or
        `flag`. The base is the projection record, what the exporter last wrote
        there, never the job's `expected_hash`, which records what the committing
        transaction saw and is stale by design once jobs coalesce.
        """
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return "publish", None       # a new file, or a missing one repaired
        disk = _digest(data)
        if content is not None and disk == _digest(content):
            return "agrees", data
        record = self.connection.execute("SELECT content_hash FROM projection WHERE file=?", (rel,)).fetchone()
        if record is not None and record[0] == disk:
            return "publish", data
        if disk in _get(self.connection, OWN_PREFIX + rel, []):
            return "publish", data       # bytes an exporter wrote here and never acked
        return "flag", data

    def _publish_file(self, rel, kind, ident, content, exported_seq, *, job=None, tag=None) -> str:
        path = self._path(rel)
        self.checkpoint("before_write", rel)
        if not self._owns():
            return "lost"
        digest = None if content is None else _digest(content)
        try:
            verdict, data = self._classify(rel, path, content)
            if verdict == "flag":
                return self._flag(rel, kind, ident, data, job)
            if verdict == "agrees":
                stat = path.stat()
            elif content is None:
                stat = None
                if data is not None:
                    path.unlink()
                    self.checkpoint("removed", rel)
            else:
                stat = self._replace(path, content, tag or f"j{job.key}", rel, kind, ident, job)
                if isinstance(stat, str):
                    return stat
        except OSError:
            self._failed(rel, job)
            return "failed"
        return self._ack(rel, kind, ident, digest, stat, exported_seq, job)

    def _replace(self, path: Path, content: bytes, tag: str, rel: str, kind, ident, job):
        path.parent.mkdir(parents=True, exist_ok=True)
        # This job's own temp name: a stray from a crashed attempt at the same job
        # is overwritten here, and nobody else's temp is ever touched.
        temp = path.with_name(f"{path.name}.tmp.{tag}")
        with temp.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        self.checkpoint("temp_written", rel)
        # Verified again at the last moment: an edit that landed while this job
        # rendered and wrote its temp is kept, not replaced.
        verdict, data = self._classify(rel, path, content)
        if verdict == "flag":
            temp.unlink(missing_ok=True)
            return self._flag(rel, kind, ident, data, job)
        if not self._owns():
            temp.unlink(missing_ok=True)
            return "lost"
        deadline = None
        while True:
            try:
                os.replace(temp, path)
                break
            except OSError as exc:
                if exc.errno not in _RETRYABLE_REPLACE_ERRNOS:
                    temp.unlink(missing_ok=True)
                    raise
                deadline = deadline or time.monotonic() + 2.0
                if time.monotonic() >= deadline:
                    temp.unlink(missing_ok=True)
                    raise
                time.sleep(random.uniform(0.02, 0.08))
        self.checkpoint("replaced", rel)
        return path.stat()

    def _flag(self, rel, kind, ident, data: bytes, job: Job | None) -> str:
        """Flag-and-keep-both (D2, the B-089 shape): the file stays exactly as found,
        its bytes are kept in `projection_conflict`, the store keeps its version and
        every export of the entity waits for `backlog_resolve_conflict`."""
        self._begin()
        try:
            try:
                self._fenced()
            except LeaseLost:
                self.connection.rollback()
                return "lost"
            ensure_conflict_table(self.connection)
            self.connection.execute(
                "INSERT INTO projection_conflict(file,kind,id,flagged_at,file_hash,file_content) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(file) DO UPDATE SET file_hash=excluded.file_hash,file_content=excluded.file_content",
                (rel, kind, ident, datetime.now(timezone.utc).isoformat(), _digest(data), data))
            self.connection.execute("UPDATE projection SET dirty=1 WHERE file=?", (rel,))
            if job is not None:
                self.connection.execute("UPDATE projection_jobs SET state='conflict',lease_owner=NULL,lease_until=NULL "
                                        "WHERE job_key=?", (job.key,))
            _update_through(self.connection)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        self.warnings.append(f"export pending: {rel} is flagged")
        return "flagged"

    def _failed(self, rel: str, job: Job | None) -> None:
        """A write or removal the filesystem refused: retried by the next drain."""
        self._begin()
        try:
            self._fenced()
            self.connection.execute("UPDATE projection SET dirty=1,quarantined=0 WHERE file=?", (rel,))
            if job is not None:
                self.connection.execute("UPDATE projection_jobs SET state='pending',lease_owner=NULL,lease_until=NULL "
                                        "WHERE job_key=? AND state='claimed'", (job.key,))
            self.connection.commit()
        except LeaseLost:
            self.connection.rollback()
        except BaseException:
            self.connection.rollback()
            raise
        self.warnings.append(f"export pending: {rel} — retried on next call")

    def _ack(self, rel, kind, ident, digest, stat, exported_seq, job) -> str:
        self._begin()
        try:
            try:
                self._fenced()
            except LeaseLost:
                self.connection.rollback()
                self._requeue_after_lost_ack(rel, job)
                return "lost"
            if digest is None:
                self.connection.execute("DELETE FROM projection WHERE file=?", (rel,))
            else:
                self.connection.execute(
                    "INSERT INTO projection(file,kind,id,content_hash,mtime,size,dirty,quarantined,exported_seq) "
                    "VALUES(?,?,?,?,?,?,0,0,?) ON CONFLICT(file) DO UPDATE SET kind=excluded.kind,id=excluded.id,"
                    "content_hash=excluded.content_hash,mtime=excluded.mtime,size=excluded.size,dirty=0,quarantined=0,"
                    "exported_seq=excluded.exported_seq",
                    (rel, kind, ident, digest, stat.st_mtime, stat.st_size, exported_seq))
            self.connection.execute("DELETE FROM projection_base WHERE file=?", (rel,))
            self.checkpoint("ack_manifest", rel)
            if job is not None:
                self.connection.execute("UPDATE projection_jobs SET state='exported',lease_owner=NULL,lease_until=NULL "
                                        "WHERE job_key=?", (job.key,))
            _update_through(self.connection)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        self.acked.add(rel)
        self.checkpoint("acked", rel)
        return "exported"

    def _requeue_after_lost_ack(self, rel: str, job: Job | None) -> None:
        """§2.3(4): this exporter may have replaced the file after a successor
        published it, leaving older bytes on disk that no job will rewrite.

        It records nothing as its own, but it re-queues the file: the latest
        exported job goes back to `pending` (a derived file is marked stale), so the
        next exporter verifies the file, recognizes the stale bytes as an
        exporter's own and repairs it. A file that still has a pending, claimed or
        conflicted job needs nothing: that job will verify it.
        """
        try:
            self._begin()
        except sqlite3.Error:
            return
        try:
            if job is None:
                self.connection.execute("UPDATE projection SET exported_seq=NULL WHERE file=?", (rel,))
            else:
                self.connection.execute(
                    "UPDATE projection_jobs SET state='pending',lease_owner=NULL,lease_until=NULL WHERE job_key=("
                    "SELECT job_key FROM projection_jobs WHERE file=? AND state='exported' "
                    "ORDER BY commit_seq DESC,job_key DESC LIMIT 1) AND NOT EXISTS("
                    "SELECT 1 FROM projection_jobs WHERE file=? AND state IN ('pending','claimed','conflict'))",
                    (rel, rel))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    # ── Finish ──────────────────────────────────────────────────────────────

    def finish(self) -> None:
        """Release the lease so the next exporter need not wait for it to expire."""
        self.release()

    def release(self) -> None:
        if self.generation is None or self.connection.in_transaction:
            return
        try:
            self._begin()
        except sqlite3.Error:
            return
        try:
            current = lease(self.connection)
            if current.get("owner") == self.owner and current.get("generation") == self.generation:
                _put(self.connection, EXPORTER_KEY, dict(current, until=self.clock()))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

