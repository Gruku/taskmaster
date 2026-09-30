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

from taskmaster.projection_paths import UnsafePath, safe_path

from .db import assert_native

LEASE_SECONDS = 30.0
# Sharing violations an indexer or antivirus causes on Windows; the replace is retried.
_RETRYABLE_REPLACE_ERRNOS = {5, 13, 32, errno.EACCES, errno.EPERM}
EXPORTER_KEY = "projection_exporter"
THROUGH_KEY = "exported_through"
# Per file, the last few hashes an exporter was about to publish there (see `intend`).
OWN_PREFIX = "projection.own."
OWN_RING = 8
# Per file, the names a publication may set the file aside under (see `_publish_file`).
ASIDE_PREFIX = "projection.aside."
# Projection paths a managed checkout left differing from the published generation
# (N13 step 8). The checked-out bytes are drift, not authority: they are neither
# imported nor overwritten until explicitly resolved. {"op_id", "target", "files": {rel: sha1|null}}
DRIFT_KEY = "git.drift"
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
    # For a move's removal: the job that writes the entity's new path.
    moved_to: int | None = None

    def __post_init__(self):
        self.kind, self.id = self.entity["kind"], self.entity["id"]


def _digest(content: bytes) -> str:
    return hashlib.sha1(content).hexdigest()


CR, LF = b"\r", b"\n"


def _lf(data: bytes) -> bytes:
    return data.replace(CR + LF, LF)


def _crlf(data: bytes) -> bytes:
    return _lf(data).replace(LF, CR + LF)


def _move(source: Path, target: Path) -> bool:
    """Rename `source` over `target` (a name only this exporter uses); False if `source` is gone."""
    try:
        os.replace(source, target)
    except FileNotFoundError:
        return False
    return True


def _install(source: Path, target: Path) -> bool:
    """Rename `source` to `target` only if nothing is at `target`; False if something is.

    Never overwrites: on Windows a rename refuses an existing target, and on
    POSIX a hard link does, after which the source name is dropped.
    """
    try:
        if os.name == "nt":
            os.rename(source, target)
        else:
            os.link(source, target)
            os.unlink(source)
    except FileExistsError:
        return False
    return True


_RETRY_SECONDS = 2.0


def _retry(step: Callable[[], bool]) -> bool:
    """Run one rename step, retrying the sharing violations an indexer or antivirus causes."""
    deadline = None
    while True:
        try:
            return step()
        except FileExistsError:
            raise
        except OSError as exc:
            if exc.errno not in _RETRYABLE_REPLACE_ERRNOS:
                raise
            deadline = deadline or time.monotonic() + _RETRY_SECONDS
            if time.monotonic() >= deadline:
                raise
            time.sleep(random.uniform(0.02, 0.08))


def _drop(path: Path) -> bool:
    """Remove one file, retrying sharing violations; True once it is gone."""
    def step() -> bool:
        path.unlink(missing_ok=True)
        return True
    return _retry(step)


def _quietly(step: Callable[[], object]) -> bool:
    """Run a best-effort undo step: True if it ran, False if the filesystem refused."""
    try:
        step()
    except OSError:
        return False
    return True


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
    drift = set(drift_files(connection))
    if drift:
        found.update((file, DRIFT_REASON) for (file,) in connection.execute(
            "SELECT file FROM projection WHERE kind=? AND id IS ?", (kind, ident)) if file in drift)
    if _has_conflict_table(connection):
        found.update((file, "flagged") for (file,) in connection.execute(
            "SELECT file FROM projection_conflict WHERE kind=? AND id IS ?", (kind, ident)))
    return sorted(found.items())


def held_file(connection, rel: str) -> str | None:
    """Why one file may not be written (`quarantined`, `flagged`, drift), or None."""
    row = connection.execute("SELECT quarantined FROM projection WHERE file=?", (rel,)).fetchone()
    if row and row[0]:
        return "quarantined"
    if _has_conflict_table(connection) and connection.execute(
            "SELECT 1 FROM projection_conflict WHERE file=?", (rel,)).fetchone():
        return "flagged"
    if rel in drift_files(connection):
        return DRIFT_REASON
    return None


DRIFT_REASON = "managed checkout drift"


def drift_files(connection) -> tuple[str, ...]:
    """Paths a managed checkout left differing from the published generation."""
    return tuple(sorted((_get(connection, DRIFT_KEY) or {}).get("files") or {}))


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
    drift = (f"SELECT d.key FROM sync_state s, json_each(s.value_json, '$.files') d WHERE s.key='{DRIFT_KEY}'")
    entity += (f" OR j.file IN ({drift}) OR EXISTS(SELECT 1 FROM entity_core e JOIN projection p ON p.kind=e.kind "
               f"AND p.id=e.public_id WHERE e.entity_key=j.entity_key AND p.file IN ({drift}))")
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
        self.outcomes: dict[int, str] = {}
        # Per file, the hash of aside bytes this attempt verified (see `_remember`).
        self._verified: dict[str, str] = {}

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
            taken = current.get("owner") != self.owner or current.get("generation") != self.generation
            if taken:
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
        except BaseException:
            self.connection.rollback()
            raise
        if taken:
            self._recover_asides()
        return jobs

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
        removals = {job.entity_key: job for job in jobs if job.effect == "delete"}
        for job in jobs:
            removal = removals.get(job.entity_key)
            if job.effect != "delete" and removal is not None:
                job.moved_from, removal.moved_to = removal.file, job.key

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
            for target, _content in rendered:
                rel = target if isinstance(target, str) else target.file
                # Durable before any rename: the name this generation may set the
                # file aside under, so recovery can always put it back, and the
                # temp it may leave, so recovery can drop it.
                base = PurePosixPath(rel).name
                own = [self._aside_name(rel), f"{base}.tmp.{self._tag(target)}"]
                names = [n for n in _get(self.connection, ASIDE_PREFIX + rel, []) if n not in own]
                _put(self.connection, ASIDE_PREFIX + rel, names + own)
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
            if job.moved_to is not None and self.outcomes.get(job.moved_to) != "exported":
                # Never remove a move's old path before its new path is exported:
                # that would leave the entity with no file. The removal waits.
                outcome = self._defer(job)
                self.outcomes[job.key] = outcome
                return outcome
        outcome = self._publish_file(job.file, job.kind, job.id, content, int(job.entity["last_seq"]), job=job,
                                     tag=self._tag(job))
        self.outcomes[job.key] = outcome
        return outcome

    def _defer(self, job: Job) -> str:
        self._begin()
        try:
            try:
                self._fenced()
            except LeaseLost:
                self.connection.rollback()
                return "lost"
            self.connection.execute("UPDATE projection_jobs SET state='pending',lease_owner=NULL,lease_until=NULL "
                                    "WHERE job_key=? AND state='claimed'", (job.key,))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return "deferred"

    def publish_derived(self, rel: str, kind: str, content: bytes | None, exported_seq: int) -> str:
        """Publish a whole derived file (`backlog.yaml`, `ideas/IDEAS.md`), which has no job."""
        return self._publish_file(rel, kind, None, content, exported_seq, tag=self._tag(rel))

    def _tag(self, target: Job | str) -> str:
        """The temp-name tag of one publication, unique to this exporter generation.

        A stale exporter and its successor may publish the same job; they must
        never share a temp name, or one could install or delete the other's.
        """
        return f"d.g{self.generation}" if isinstance(target, str) else f"j{target.key}.g{self.generation}"

    def _aside_name(self, rel: str) -> str:
        return f"{PurePosixPath(rel).name}.aside.g{self.generation}"

    def _still_on_disk(self, rel: str) -> list[str]:
        """The recorded aside and temp names of `rel` whose files still exist.

        A name is forgotten only once its file is gone, so recovery can always
        find an aside file some step could not restore or remove.
        """
        names = _get(self.connection, ASIDE_PREFIX + rel, [])
        try:
            path = self._path(rel)
        except (OSError, UnsafePath):
            return names             # unprovable now: every name stays on record
        return [name for name in names if path.with_name(name).exists()]

    def _remember(self, rel: str, names: list[str]) -> None:
        """Inside a write transaction: keep exactly `names` on record for `rel`.

        An aside file left behind after its bytes were verified is the store's
        own: its hash joins the file's own-bytes ring, so recovery drops it
        rather than flagging it once the record has moved on.
        """
        if names:
            _put(self.connection, ASIDE_PREFIX + rel, names)
            if rel in self._verified:
                key = OWN_PREFIX + rel
                ring = [h for h in _get(self.connection, key, []) if h != self._verified[rel]]
                _put(self.connection, key, (ring + [self._verified[rel]])[-OWN_RING:])
        else:
            self.connection.execute("DELETE FROM sync_state WHERE key=?", (ASIDE_PREFIX + rel,))

    def _path(self, rel: str) -> Path:
        return safe_path(self.backlog_dir, str(safe_relative(rel)))

    def _classify(self, rel: str, path: Path, content: bytes | None) -> tuple[str, bytes | None]:
        """§2.4: what the bytes on disk say about publishing over them.

        `agrees` (the file already holds this content), `publish`, or `flag`.
        """
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return "publish", None       # a new file, or a missing one repaired
        return self._judge(rel, data, content), data

    def _judge(self, rel: str, data: bytes, content: bytes | None) -> str:
        """Whether `data`, found at `rel`, may be replaced by `content`.

        The base is the projection record, what the exporter last wrote there,
        never the job's `expected_hash`, which records what the committing
        transaction saw and is stale by design once jobs coalesce. Line endings
        alone never make a conflict, as they do not for the legacy exporter: a
        file whose only change is CRLF against LF matches its record.
        """
        variants = {_digest(data), _digest(_lf(data)), _digest(_crlf(data))}
        if content is not None and _lf(data) == _lf(content):
            return "agrees"
        record = self.connection.execute("SELECT content_hash FROM projection WHERE file=?", (rel,)).fetchone()
        if record is not None and record[0] in variants:
            return "publish"
        if variants & set(_get(self.connection, OWN_PREFIX + rel, [])):
            return "publish"             # bytes an exporter wrote here and never acked
        return "flag"

    def _publish_file(self, rel, kind, ident, content, exported_seq, *, job=None, tag=None) -> str:
        """Verify, then publish without ever overwriting bytes that were not verified.

        The file is first renamed aside to a name this exporter generation owns,
        and the aside bytes are checked: that closes the window between the check
        and the install, because an edit made before the rename is in the aside
        file and one made after it lands on the (now vacant) path, where the
        install refuses to overwrite it. On a mismatch the aside file goes back
        and the file is flagged. A crash with the file aside is undone by lease
        recovery (`_recover_asides`), which knows the aside name from `intend`.
        """
        try:
            path = self._path(rel)
        except (OSError, UnsafePath) as exc:
            return self.refuse(job if job is not None else rel, exc)
        tag = tag or self._tag(job if job is not None else rel)
        self.checkpoint("before_write", rel)
        if not self._owns():
            return "lost"
        temp = aside = None
        try:
            verdict, data = self._classify(rel, path, content)
            if verdict == "flag":
                return self._flag(rel, kind, ident, data, job)
            if verdict == "agrees":
                return self._ack(rel, kind, ident, _digest(data), path.stat(), exported_seq, job, content=data)
            if content is not None:
                temp = self._write_temp(path, content, tag, rel)
            if data is not None:
                aside = path.with_name(self._aside_name(rel))
                if not _retry(lambda: _move(path, aside)):
                    aside = None         # the file vanished meanwhile: nothing to set aside
                self.checkpoint("aside", rel)
                if aside is not None:
                    seen = aside.read_bytes()
                    if self._judge(rel, seen, content) == "flag":
                        return self._put_back(rel, kind, ident, path, aside, temp, seen, job)
                    self._verified[rel] = _digest(seen)
            if not self._owns():
                self._undo(path, aside, temp)
                return "lost"
            # §2.3(4): an exporter paused here past its lease finds its successor's
            # file (recovery put the aside file back first) and the install refuses.
            # Paused after the install instead, its bytes are own bytes, and the
            # re-queue on its refused ack makes the next exporter repair them.
            self.checkpoint("before_replace", rel)
            if temp is not None:
                if not _retry(lambda: _install(temp, path)):
                    # Something was written at the vacated path: it is not ours.
                    newcomer = path.read_bytes()
                    _quietly(lambda: _drop(temp))
                    if aside is not None:
                        _quietly(lambda: _drop(aside))  # verified bytes: the store's own
                    return self._flag(rel, kind, ident, newcomer, job)
                temp = None
                self.checkpoint("replaced", rel)
            if aside is not None:
                if content is None and path.exists():
                    # A removal found something new at the path it vacated: that
                    # file is not ours. It stays and is flagged; the aside bytes
                    # were verified as the store's own and are dropped.
                    _quietly(lambda: _drop(aside))
                    return self._flag(rel, kind, ident, path.read_bytes(), job)
                if content is None:
                    _drop(aside)         # a removal is done only once the aside is gone
                    aside = None
                    self.checkpoint("removed", rel)
                else:
                    # The new bytes are installed: the old ones are verified store
                    # bytes. If they cannot go now, they stay on record for recovery.
                    _quietly(lambda: _drop(aside))
                    aside = None
            stat = path.stat() if content is not None else None
        except OSError:
            self._undo(path, aside, temp)
            self._failed(rel, job)
            return "failed"
        return self._ack(rel, kind, ident, None if content is None else _digest(content), stat,
                         exported_seq, job, content=content)

    def _write_temp(self, path: Path, content: bytes, tag: str, rel: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        # This job's own temp name: a stray from a crashed attempt at the same job
        # is overwritten here, and nobody else's temp is ever touched.
        temp = path.with_name(f"{path.name}.tmp.{tag}")
        with temp.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        self.checkpoint("temp_written", rel)
        return temp

    def _undo(self, path: Path, aside: Path | None, temp: Path | None) -> None:
        """Best effort, never raising: put a set-aside file back first, then drop the temp.

        The aside file is the one that matters (it may be the only copy of the
        bytes at the path); the temp is a render of committed state. Whatever
        cannot be undone stays on record (`_still_on_disk`) for lease recovery.
        """
        if aside is not None:
            _quietly(lambda: aside.exists() and _retry(lambda: _install(aside, path)))
        if temp is not None:
            _quietly(lambda: _drop(temp))

    def _put_back(self, rel, kind, ident, path, aside, temp, seen: bytes, job) -> str:
        """The aside bytes were edited: restore them untouched and flag.

        If a second writer took the path too, or the restore is refused, both stay
        on disk: the flag keeps the edited bytes and the aside name stays on record.
        """
        _quietly(lambda: _retry(lambda: _install(aside, path)))
        if temp is not None:
            _quietly(lambda: _drop(temp))
        return self._flag(rel, kind, ident, seen, job)

    def _recover_asides(self) -> None:
        """Undo every set-aside file an earlier exporter generation left behind.

        Called right after a claim that took the lease, so no live exporter can
        be using them. A file whose path is empty goes back (the next export then
        verifies it like any other file). When the path was refilled, verified
        aside bytes are the store's own and are dropped; anything else stays on
        disk and is flagged, so an edit caught aside is never lost.
        """
        rows = self.connection.execute("SELECT key,value_json FROM sync_state WHERE key>=? AND key<?",
                                       (ASIDE_PREFIX, ASIDE_PREFIX[:-1] + "/")).fetchall()
        for key, value in rows:
            rel = key[len(ASIDE_PREFIX):]
            try:
                path = self._path(rel)
            except (OSError, UnsafePath):
                continue                 # refused path: its names stay on record for a later recovery
            keep = []
            for name in json.loads(value):
                aside = path.with_name(name)
                try:
                    if not aside.exists():
                        continue
                    if ".tmp." in name:
                        _drop(aside)     # a render of committed state, never an only copy
                        continue
                    if _retry(lambda: _install(aside, path)):
                        continue
                    seen = aside.read_bytes()
                    if self._judge(rel, seen, None) != "flag":
                        _drop(aside)
                        continue
                    kind, ident = (self.connection.execute(
                        "SELECT kind,id FROM projection WHERE file=?", (rel,)).fetchone() or ("unknown", None))
                    self._flag(rel, kind, ident, seen, None, keep=[name])
                    keep.append(name)    # left on disk for the person, and on record
                except OSError:
                    keep.append(name)    # the filesystem refused: the next recovery tries again
            self._begin()
            try:
                self._remember(rel, keep)
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def _flag(self, rel, kind, ident, data: bytes, job: Job | None, *, keep: list[str] | None = None) -> str:
        """Flag-and-keep-both (D2, the B-089 shape): the file stays exactly as found,
        its bytes are kept in `projection_conflict`, the store keeps its version and
        every export of the entity waits for `backlog_resolve_conflict`."""
        keep = self._still_on_disk(rel) if keep is None else keep
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
            self._remember(rel, keep)
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

    def refuse(self, target: Job | str, reason) -> str:
        """The path guard refused this file: nothing on disk was touched.

        Only this job goes back to pending; the drain never raises for it, so
        unrelated files still publish and the committed caller gets a notice.
        """
        rel = target if isinstance(target, str) else target.file
        if not isinstance(target, str):
            self._begin()
            try:
                self._fenced()
                self.connection.execute("UPDATE projection_jobs SET state='pending',lease_owner=NULL,lease_until=NULL "
                                        "WHERE job_key=? AND state='claimed'", (target.key,))
                self.connection.commit()
            except LeaseLost:
                self.connection.rollback()
                return "lost"
            except BaseException:
                self.connection.rollback()
                raise
            self.outcomes[target.key] = "failed"
        self.warnings.append(f"export pending: {rel} refused ({reason})")
        return "failed"

    def _failed(self, rel: str, job: Job | None) -> None:
        """A write or removal the filesystem refused: retried by the next drain."""
        keep = self._still_on_disk(rel)
        self._begin()
        try:
            self._fenced()
            self.connection.execute("UPDATE projection SET dirty=1,quarantined=0 WHERE file=?", (rel,))
            self._remember(rel, keep)
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

    def _ack(self, rel, kind, ident, digest, stat, exported_seq, job, *, content) -> str:
        # N13 needs the exact bytes this generation observed/wrote, not a render
        # of a later database revision. Keep them atomically with their manifest
        # digest; an editor changing the file after publication is then a merge
        # against this real base rather than an invented one.
        if (content is None) != (digest is None) or (content is not None and _digest(content) != digest):
            raise ValueError("projection acknowledgement bytes do not match digest")
        keep = self._still_on_disk(rel)
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
                self.connection.execute("DELETE FROM projection_base WHERE file=?", (rel,))
            else:
                self.connection.execute(
                    "INSERT INTO projection(file,kind,id,content_hash,mtime,size,dirty,quarantined,exported_seq) "
                    "VALUES(?,?,?,?,?,?,0,0,?) ON CONFLICT(file) DO UPDATE SET kind=excluded.kind,id=excluded.id,"
                    "content_hash=excluded.content_hash,mtime=excluded.mtime,size=excluded.size,dirty=0,quarantined=0,"
                    "exported_seq=excluded.exported_seq",
                    (rel, kind, ident, digest, stat.st_mtime, stat.st_size, exported_seq))
                self.connection.execute("INSERT INTO projection_base(file,content) VALUES(?,?) "
                                        "ON CONFLICT(file) DO UPDATE SET content=excluded.content", (rel, content))
            self._remember(rel, keep)
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
_RESOLVE_ARGUMENTS = {"file", "take", "replaced_base64", "replaced_hash"}


def resolvable(connection, rel: str) -> tuple[str, str | None, bytes | None] | None:
    """`(kind, id, flagged bytes)` when `rel` may be resolved, else None.

    A B-089 flag (legacy or native) carries the bytes it captured. A quarantine
    inherited from the legacy store is resolvable too: native never creates one,
    and without this nothing native could ever clear it. It has no captured bytes.
    """
    if _has_conflict_table(connection):
        row = connection.execute("SELECT kind,id,file_content FROM projection_conflict WHERE file=?",
                                 (rel,)).fetchone()
        if row is not None:
            return row[0], row[1], bytes(row[2])
    row = connection.execute("SELECT kind,id FROM projection WHERE file=? AND quarantined=1", (rel,)).fetchone()
    return None if row is None else (row[0], row[1], None)


def validate_resolve(arguments: dict) -> None:
    import base64
    import binascii
    if set(arguments) - _RESOLVE_ARGUMENTS or "file" not in arguments:
        raise ValueError("projection.resolve takes file, take, replaced_base64 and replaced_hash")
    safe_relative(arguments["file"])
    if arguments.get("take") != "store":
        raise ValueError('projection.resolve only keeps the store version; take="file" needs the importer (N13)')
    encoded, digest = arguments.get("replaced_base64"), arguments.get("replaced_hash")
    if (encoded is None) != (digest is None):
        raise ValueError("replaced_base64 and replaced_hash come together")
    if encoded is not None:
        if not isinstance(encoded, str) or not isinstance(digest, str):
            raise ValueError("replaced_base64 and replaced_hash must be text")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("replaced_base64 is not base64") from None
        if _digest(raw) != digest:
            raise ValueError("replaced_hash does not match replaced_base64")


def apply_resolve(transaction, arguments: dict) -> None:
    """Keep the store's version of a flagged or inherited-quarantined file (take="store").

    The flag (or quarantine) goes. The bytes the resolver saw on disk, and chose
    to discard, become the file's base, so the next export replaces exactly them
    and a newer hand edit is flagged again. The entity's latest revision is queued
    for export (a derived file is marked stale). The resolution's domain event
    keeps the replaced bytes exactly (`file_base64`) and the bytes captured when
    the file was flagged (`flagged_base64`); `file` is their text for reading.
    When the file is gone by the time of the resolution, the flagged bytes are
    what it replaces.
    """
    import base64
    from . import events
    from .commands import projection_path
    from .migrate import encode
    connection, rel = transaction.connection, arguments["file"]
    found = resolvable(connection, rel)
    if found is None:
        raise ValueError(f"{rel} is not flagged; there is nothing to resolve")
    kind, ident, flagged = found
    if _has_conflict_table(connection):
        connection.execute("DELETE FROM projection_conflict WHERE file=?", (rel,))
    if arguments.get("replaced_hash") is not None:
        connection.execute(
            "INSERT INTO projection(file,kind,id,content_hash,mtime,size,dirty,quarantined,exported_seq) "
            "VALUES(?,?,?,?,NULL,NULL,1,0,NULL) ON CONFLICT(file) DO UPDATE SET content_hash=excluded.content_hash,"
            "dirty=1,quarantined=0,quarantine_mtime=NULL,quarantine_size=NULL,quarantine_hash=NULL,exported_seq=NULL",
            (rel, kind, ident, arguments["replaced_hash"]))
    else:
        connection.execute("UPDATE projection SET dirty=1,quarantined=0,quarantine_mtime=NULL,quarantine_size=NULL,"
                           "quarantine_hash=NULL,exported_seq=NULL WHERE file=?", (rel,))
    replaced = arguments.get("replaced_base64")
    raw = base64.b64decode(replaced) if replaced is not None else flagged
    before = {"file": None if raw is None else raw.decode("utf-8", errors="replace"),
              "file_base64": None if raw is None else base64.b64encode(raw).decode("ascii"),
              "flagged_base64": None if flagged is None else base64.b64encode(flagged).decode("ascii"),
              "file_missing": replaced is None, "store": None}
    event_id = ident or ("__backlog__" if kind == "backlog" else rel)
    transaction.group, transaction.seq = events.append(
        connection, transaction.request, transaction.group, kind, event_id, "resolve",
        before, {"file": rel, "took": "store"})
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


# ── Linked-checkout publication (N13 step 9) ─────────────────────────────────

def _checkout_names(path: Path, token: str) -> tuple[Path, Path]:
    return path.with_name(f"{path.name}.tmp.co-{token}"), path.with_name(f"{path.name}.aside.co-{token}")


def publish_checkout_file(backlog_dir: Path, rel: str, content: bytes | None, expected: str | None,
                          token: str) -> str:
    """Compare-and-swap one file of a linked checkout: replace it only while it still
    holds `expected` (a digest of the checkout's base; None = the path is absent).

    Same no-overwrite discipline as the exporter: the file is set aside under a name
    the caller recorded (`token`) before any rename, the aside bytes are verified,
    and the new bytes are installed without overwriting. Returns `published`,
    `removed`, `agrees`, `changed` (someone else's bytes: left untouched) or
    `failed` (the filesystem refused; nothing unverified was replaced)."""
    try:
        path = safe_path(backlog_dir, str(safe_relative(rel)))
    except (OSError, UnsafePath, ValueError):
        return "failed"
    temp, aside = _checkout_names(path, token)

    def matches(data: bytes) -> bool:
        return expected is not None and expected in {_digest(data), _digest(_lf(data)), _digest(_crlf(data))}
    try:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            data = None
        if content is not None and data is not None and _lf(data) == _lf(content):
            return "agrees"
        if data is None and content is None:
            return "removed"
        if data is not None and not matches(data):
            return "changed"
        if content is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temp.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        moved = False
        if data is not None:
            moved = _retry(lambda: _move(path, aside))
            if moved and not matches(aside.read_bytes()):
                _quietly(lambda: _retry(lambda: _install(aside, path)))
                if content is not None:
                    _quietly(lambda: _drop(temp))
                return "changed"
        if content is not None:
            if not _retry(lambda: _install(temp, path)):
                _quietly(lambda: _drop(temp))
                if moved:
                    _quietly(lambda: _drop(aside))  # verified base bytes; the newcomer stays
                return "changed"
        elif path.exists():
            if moved:
                _quietly(lambda: _drop(aside))
            return "changed"
        if moved:
            _drop(aside)
        return "published" if content is not None else "removed"
    except OSError:
        if aside.exists() and not path.exists():
            _quietly(lambda: _retry(lambda: _install(aside, path)))
        _quietly(lambda: _drop(temp))
        return "failed"


def recover_checkout_file(backlog_dir: Path, rel: str, token: str, target: str | None) -> str:
    """Undo what an interrupted `publish_checkout_file` left behind (its names are recorded).

    The temp is a copy of published bytes and is dropped. A set-aside file goes back
    when the path is empty; when the path already carries the target the aside bytes
    were the verified base and are dropped; anything else stays for inspection."""
    try:
        path = safe_path(backlog_dir, str(safe_relative(rel)))
    except (OSError, UnsafePath, ValueError):
        return "refused"
    temp, aside = _checkout_names(path, token)
    _quietly(lambda: _drop(temp))
    if not aside.exists():
        return "clean"
    if _quietly(lambda: _retry(lambda: _install(aside, path))) and not aside.exists():
        return "restored"
    try:
        current = path.read_bytes()
    except OSError:
        return "aside kept"
    if target is not None and target in {_digest(current), _digest(_lf(current)), _digest(_crlf(current))}:
        _quietly(lambda: _drop(aside))
        return "dropped"
    return "aside kept"


# Authored configuration beside the projection that a coordinator route may rewrite
# (N13 step 7). It is not a projection of the store: no job, base or flag covers it.
CONFIG_FILES = frozenset({"linear.yaml"})
CONFIG_MAX_BYTES = 1024 * 1024


def read_config(backlog_dir: Path, name: str) -> bytes | None:
    """The configuration file's bytes, or None when absent; links and oversized files refuse."""
    if name not in CONFIG_FILES:
        raise ValueError(f"not a coordinator-owned configuration file: {name!r}")
    path = safe_path(backlog_dir, name)
    try:
        with open(path, "rb") as handle:
            content = handle.read(CONFIG_MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(content) > CONFIG_MAX_BYTES:
        raise ValueError(f"{name} exceeds {CONFIG_MAX_BYTES} bytes")
    return content


def replace_config(backlog_dir: Path, name: str, *, expected: bytes | None, content: bytes) -> None:
    """Install `content` as the configuration file, all or nothing.

    The caller holds the coordinator's publication boundary. The new bytes are
    written and synced under a private temp name, the file is rechecked against
    `expected` (what the caller parsed), then renamed into place: a replace when
    it existed, a no-overwrite install when it did not. Any failure leaves the
    old file and no temp. Editors that ignore the boundary can still race the
    final rename; that window is the same one publication documents.
    """
    path = safe_path(backlog_dir, name)
    stale = f"{name} changed while it was being updated; it was left as it is"
    if read_config(backlog_dir, name) != expected:
        raise ValueError(stale)
    temp = path.with_name(f"{name}.tmp.{os.getpid()}.{random.getrandbits(64):016x}")
    try:
        with open(temp, "xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if read_config(backlog_dir, name) != expected:
            raise ValueError(stale)
        if expected is None:
            if not _retry(lambda: _install(temp, path)):
                raise ValueError(f"{name} appeared while it was being created; it was left as it is")
        else:
            _retry(lambda: os.replace(temp, path) or True)
    finally:
        if os.path.lexists(temp):
            _quietly(lambda: _drop(temp))
