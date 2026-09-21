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

    def intend(self, rendered: Iterable[tuple[Job, bytes | None]]) -> None:
        """Fence check before any file is touched (the publish-intent transaction)."""
        self._begin()
        try:
            self._fenced()
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def publish(self, job: Job, content: bytes | None) -> str:
        """Publish one claimed job's bytes (None removes its file), then ack it.

        Returns `exported`, `failed` (left for the next drain, with a warning) or
        `lost` (this exporter no longer owns the lease; nothing was recorded).
        """
        if job.effect == "delete":
            content = None
        return self._publish_file(job.file, job.kind, job.id, content, int(job.entity["last_seq"]), job=job)

    def _path(self, rel: str) -> Path:
        path = PurePosixPath(rel)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError(f"unsafe projection path {rel!r}")
        return self.backlog_dir / path

    def _publish_file(self, rel, kind, ident, content, exported_seq, *, job=None, tag=None) -> str:
        path = self._path(rel)
        self.checkpoint("before_write", rel)
        if not self._owns():
            return "lost"
        stat = None
        digest = None
        try:
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                digest = hashlib.sha1(content).hexdigest()
                record = self.connection.execute("SELECT content_hash FROM projection WHERE file=?", (rel,)).fetchone()
                if record and record[0] == digest and path.exists():
                    stat = path.stat()
                else:
                    stat = self._replace(path, content, tag or f"j{job.key}", rel)
        except OSError:
            self._failed(rel, job)
            return "failed"
        return self._ack(rel, kind, ident, digest, stat, exported_seq, job)

    def _replace(self, path: Path, content: bytes, tag: str, rel: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.tmp.{tag}")
        with temp.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        self.checkpoint("temp_written", rel)
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

