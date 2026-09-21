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

from .db import assert_native

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


def safe_relative(rel: str) -> PurePosixPath:
    """A projection path relative to the backlog directory, never outside it."""
    if not isinstance(rel, str) or not rel or ":" in rel or "\\" in rel:
        raise ValueError(f"unsafe projection path {rel!r}")
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe projection path {rel!r}")
    return path


def lease(connection) -> dict:
    """The exporter lease as stored: `{owner, generation, until}` (empty before any export)."""
    return _get(connection, EXPORTER_KEY, {})


def exported_through(connection) -> int:
    """The highest commit sequence whose projection jobs are all exported (held entities aside)."""
    return int(_get(connection, THROUGH_KEY, 0))


def export_record(connection, rel: str) -> dict | None:
    """The retained export record of one file: its latest exported job.

    `tombstone` is true when the file is absent on purpose: the record removed it
    (a move's old path, or an epic or phase whose heavy fields were cleared) and no
    projection row describes bytes there. Tombstones never expire in N11; only a
    later job for the same path replaces them.
    """
    row = connection.execute("SELECT job_key,effect,commit_seq FROM projection_jobs WHERE file=? AND state='exported' "
                             "ORDER BY commit_seq DESC,job_key DESC LIMIT 1", (rel,)).fetchone()
    if row is None:
        return None
    present = connection.execute("SELECT 1 FROM projection WHERE file=?", (rel,)).fetchone() is not None
    return {"job_key": row[0], "effect": row[1], "commit_seq": row[2], "tombstone": not present}


def _has_conflict_table(connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='projection_conflict'").fetchone() is not None


def held(connection, kind: str, ident: str | None) -> list[tuple[str, str]]:
    """`(file, reason)` for every quarantined or flagged file of one entity.

    The one answer to "may this entity's files be written?" that claims, store
    status and conflict resolution all use (N09 §4d: two readers of one safety
    question drifted apart). The hold is per entity, as the legacy
    `_export_blocked` is: a move writes one path and removes another, so holding
    only the flagged file would leave the id in two places.
    """
    found = {file: "quarantined" for (file,) in connection.execute(
        "SELECT file FROM projection WHERE kind=? AND id IS ? AND quarantined=1", (kind, ident))}
    if _has_conflict_table(connection):
        found.update((file, "flagged") for (file,) in connection.execute(
            "SELECT file FROM projection_conflict WHERE kind=? AND id IS ?", (kind, ident)))
    return sorted(found.items())


def held_file(connection, rel: str) -> str | None:
    """Why one file may not be written (`quarantined`, `flagged`), or None."""
    row = connection.execute("SELECT quarantined FROM projection WHERE file=?", (rel,)).fetchone()
    if row and row[0]:
        return "quarantined"
    if _has_conflict_table(connection) and connection.execute(
            "SELECT 1 FROM projection_conflict WHERE file=?", (rel,)).fetchone():
        return "flagged"
    return None


def flagged_files(connection) -> tuple[str, ...]:
    """Every flagged file, legacy (B-089, before activation) or native (N11)."""
    if not _has_conflict_table(connection):
        return ()
    return tuple(r[0] for r in connection.execute("SELECT file FROM projection_conflict ORDER BY file"))


def _held_sql(connection) -> str:
    """`held` as a predicate over `projection_jobs j`: the job's entity or its file is held."""
    entity = ("EXISTS(SELECT 1 FROM entity_core e JOIN projection p ON p.kind=e.kind AND p.id=e.public_id "
              "WHERE e.entity_key=j.entity_key AND p.quarantined=1)"
              " OR EXISTS(SELECT 1 FROM projection p WHERE p.file=j.file AND p.quarantined=1)")
    if _has_conflict_table(connection):
        entity += (" OR EXISTS(SELECT 1 FROM entity_core e JOIN projection_conflict c ON c.kind=e.kind "
                   "AND c.id=e.public_id WHERE e.entity_key=j.entity_key)"
                   " OR EXISTS(SELECT 1 FROM projection_conflict c WHERE c.file=j.file)")
    return f"({entity})"


def _through(connection) -> int:
    """Recompute the watermark: everything below the oldest unexported, unheld job."""
    oldest = connection.execute(
        "SELECT MIN(j.commit_seq) FROM projection_jobs j WHERE j.state IN ('pending','claimed') "
        f"AND NOT {_held_sql(connection)}").fetchone()[0]
    if oldest is not None:
        return int(oldest) - 1
    return int(connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0])


def update_through(connection) -> None:
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
            assert_native(self.connection)
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
            f"SELECT j.job_key,j.entity_key,j.commit_seq,j.file,j.effect,j.input_json,{self._held_column()} "
            "FROM projection_jobs j WHERE j.state='pending' ORDER BY j.commit_seq,j.job_key").fetchall()
        latest: dict[str, tuple] = {}
        for row in pending:
            latest[row[3]] = row
        superseded = [(row[0],) for row in pending if latest[row[3]][0] != row[0]]
        self.connection.executemany("UPDATE projection_jobs SET state='superseded' WHERE job_key=?", superseded)
        jobs, holds = [], set()
        for key, entity_key, seq, rel, effect, payload, is_held in latest.values():
            job = Job(key, entity_key, seq, rel, effect, json.loads(payload))
            if is_held:
                # Stays `pending`, unleased, until the hold is resolved; its
                # newer siblings keep coalescing into it meanwhile.
                holds.add((job.kind, job.id, rel))
            else:
                jobs.append(job)
        self._report_holds(holds)
        self.connection.executemany(
            "UPDATE projection_jobs SET state='claimed',lease_owner=?,lease_until=? WHERE job_key=?",
            [(self._lease_owner, until, job.key) for job in jobs])
        return self._ordered(jobs)

    def _held_column(self) -> str:
        return f"{_held_sql(self.connection)} AS held"

    def _report_holds(self, holds) -> None:
        """Warn once per held file, as the legacy exporter does, and keep it dirty."""
        files = set()
        for kind, ident, rel in sorted(holds):
            reasons = held(self.connection, kind, ident)
            if not reasons:
                reason = held_file(self.connection, rel)
                reasons = [(rel, reason)] if reason else []
            files.update(reasons)
        for rel, reason in sorted(files):
            self.connection.execute("UPDATE projection SET dirty=1 WHERE file=?", (rel,))
            self.warnings.append(f"export pending: {rel} is {reason}")

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
        return self.backlog_dir / safe_relative(rel)

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
        # The residual window of §2.3(4): no lock spans a rename, so an exporter
        # paused here past its lease can still replace after a successor published.
        # Own-bytes recognition and the re-queue on its refused ack repair that.
        self.checkpoint("before_replace", rel)
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
            update_through(self.connection)
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
            update_through(self.connection)
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
        """Apply retention to every file this attempt acked, then release the lease."""
        try:
            if self.acked:
                self.checkpoint("retention", "")
                self._begin()
                try:
                    for rel in sorted(self.acked):
                        record = export_record(self.connection, rel)
                        if record is not None:
                            # D7: the latest exported job is the file's export record
                            # (its tombstone when the file is absent on purpose); every
                            # other terminal job for the file is history nothing reads.
                            self.connection.execute(
                                "DELETE FROM projection_jobs WHERE file=? AND state IN ('exported','superseded') "
                                "AND job_key!=?", (rel, record["job_key"]))
                    self.connection.commit()
                except BaseException:
                    self.connection.rollback()
                    raise
        finally:
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



# ── Resolution (S10) ────────────────────────────────────────────────────────

RESOLVE = "projection.resolve"
_RESOLVE_ARGUMENTS = {"file", "take", "replaced", "replaced_hash"}


def validate_resolve(arguments: dict) -> None:
    if set(arguments) - _RESOLVE_ARGUMENTS or "file" not in arguments:
        raise ValueError("projection.resolve takes file, take, replaced and replaced_hash")
    safe_relative(arguments["file"])
    if arguments.get("take") != "store":
        raise ValueError('projection.resolve only keeps the store version; take="file" needs the importer (N13)')
    if arguments.get("replaced") is not None and not isinstance(arguments["replaced"], str):
        raise ValueError("replaced must be text or null")
    digest = arguments.get("replaced_hash")
    if digest is not None and (not isinstance(digest, str) or len(digest) != 40
                               or any(c not in "0123456789abcdef" for c in digest)):
        raise ValueError("replaced_hash must be a sha1 hex digest or null")


def apply_resolve(transaction, arguments: dict) -> None:
    """Keep the store's version of a flagged file (take="store").

    The flag goes. The bytes the resolver saw on disk, and chose to discard,
    become the file's base, so the next export replaces exactly them and a newer
    hand edit is flagged again. The entity's latest revision is queued for export
    (a derived file is marked stale), and the replaced text is kept in the
    resolution's domain event, as the legacy store keeps it in its change row.
    """
    from . import events
    from .commands import projection_path
    from .migrate import encode
    connection, rel = transaction.connection, arguments["file"]
    row = connection.execute("SELECT kind,id FROM projection_conflict WHERE file=?", (rel,)).fetchone() \
        if _has_conflict_table(connection) else None
    if row is None:
        raise ValueError(f"{rel} is not flagged; there is nothing to resolve")
    kind, ident = row
    connection.execute("DELETE FROM projection_conflict WHERE file=?", (rel,))
    if arguments.get("replaced_hash") is not None:
        connection.execute(
            "INSERT INTO projection(file,kind,id,content_hash,mtime,size,dirty,quarantined,exported_seq) "
            "VALUES(?,?,?,?,NULL,NULL,1,0,NULL) ON CONFLICT(file) DO UPDATE SET content_hash=excluded.content_hash,"
            "dirty=1,quarantined=0,quarantine_mtime=NULL,quarantine_size=NULL,quarantine_hash=NULL,exported_seq=NULL",
            (rel, kind, ident, arguments["replaced_hash"]))
    else:
        connection.execute("UPDATE projection SET dirty=1,quarantined=0,exported_seq=NULL WHERE file=?", (rel,))
    event_id = ident or ("__backlog__" if kind == "backlog" else rel)
    transaction.group, transaction.seq = events.append(
        connection, transaction.request, transaction.group, kind, event_id, "resolve",
        {"file": arguments.get("replaced"), "store": None}, {"file": rel, "took": "store"})
    core = None
    if ident is not None:
        core = connection.execute("SELECT entity_key,revision,last_seq,archived,deleted FROM entity_core "
                                  "WHERE kind=? AND public_id=?", (kind, ident)).fetchone()
    if core is None:
        # A derived file (backlog.yaml, ideas/IDEAS.md) has no job: the stale
        # record above makes the next drain render it from the store.
        connection.execute("UPDATE projection_jobs SET state='superseded' WHERE state='conflict' AND file=?", (rel,))
        transaction.affected[(kind, event_id)] = {"kind": kind, "id": event_id, "revision": 0,
                                                  "last_seq": transaction.seq, "fields": {}}
        update_through(connection)
        return
    key, revision, last_seq, archived, deleted = core
    # A conflicted job of this entity rejoins the queue; the fresh jobs below are
    # newer, so the next claim coalesces it away.
    connection.execute("UPDATE projection_jobs SET state='pending',lease_owner=NULL,lease_until=NULL "
                       "WHERE state='conflict' AND (entity_key=? OR file=?)", (key, rel))
    entity = transaction.snapshot.get(kind, ident, include_body=True, include_deleted=True)
    payload = encode({"kind": kind, "id": ident, "revision": revision, "last_seq": last_seq,
                      "archived": bool(archived), "deleted": bool(deleted),
                      "fields": entity["fields"], "body": entity["body"]})
    target = None if deleted else projection_path(kind, ident, bool(archived))
    effects = ([(target, "write")] if target else []) + ([(rel, "delete")] if rel != target else [])
    for file, effect in effects:
        connection.execute("INSERT INTO projection_jobs(entity_key,revision,commit_seq,file,effect,input_json,"
                           "expected_hash) VALUES(?,?,?,?,?,?,NULL)",
                           (key, revision, transaction.seq, file, effect, payload))
    transaction.affected[(kind, ident)] = {"kind": kind, "id": ident, "revision": revision,
                                           "last_seq": transaction.seq, "fields": {}}
    update_through(connection)
