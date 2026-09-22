# User intent: keep the git-facing files exactly as visible as the legacy tools
# made them while commands run on the native core (N08), now as a thin synchronous
# adapter over the N11 outbox protocol: render here, lease/verify/publish/ack there.
"""Synchronous compatibility drain of native `projection_jobs` (N11 S6).

This is the explicit export operation the N08 bypass gate allowlists; it is the
only native-routing code that reads or writes projection files. It renders with
the legacy exporter's own functions (`store.render_entity_file`,
`store.render_backlog_file`, `render_ideas_index`), so one entity cannot be
rendered two ways, and hands the bytes to `taskmaster.native.projection`, which
owns leasing, expected-base verification, publication and acknowledgement.

No database lock is held across file I/O: the outbox claims, publishes and acks
in short transactions, fenced by its exporter lease. A caller whose export is in
another exporter's hands waits (bounded) until the watermark passes its commit,
then reports `export pending: … — retried on next call`, as the legacy exporter
warns.

Whole derived files have no per-entity job, so their freshness is the highest
`last_seq` among their inputs against the `exported_seq` recorded for the file:
`backlog.yaml` (backlog, epic and phase rows) and `ideas/IDEAS.md` (idea rows).
They are rendered from the current database and verified like entity files.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sqlite3
import threading
import time
import uuid

from taskmaster import store
from taskmaster.projection_paths import UnsafePath, safe_path
from taskmaster.native import db as native_db
from taskmaster.native import projection as outbox
from taskmaster.native.migrate import rows
from taskmaster.native.queries import Snapshot, _core_columns
from taskmaster.taskmaster_v3 import BODY_KEY, render_ideas_index

from . import derived, progress

BACKLOG_FILE = "backlog.yaml"
# How long a caller waits behind another exporter before reporting its export pending.
WAIT_SECONDS = 5.0
_POLL_SECONDS = 0.05
# Test seams: a checkpoint(stage, file) called at every protocol boundary, the clock
# leases are measured on, and how a waiting caller sleeps.
HOOKS: dict = {"checkpoint": None, "clock": time.time, "sleep": time.sleep}
_DOMINANT: dict[Path, bool] = {}
_DOMINANT_LOCK = threading.Lock()


def _dominant_crlf(backlog_dir: Path) -> bool:
    with _DOMINANT_LOCK:
        if backlog_dir not in _DOMINANT:
            _DOMINANT[backlog_dir] = store.detect_dominant_crlf(
                backlog_dir, path_guard=lambda rel: safe_path(backlog_dir, rel))
        return _DOMINANT[backlog_dir]


def reset_for_tests() -> None:
    with _DOMINANT_LOCK:
        _DOMINANT.clear()


class _Render:
    """Bytes for a claimed job or a stale derived file, exactly as the legacy exporter writes them."""

    def __init__(self, connection: sqlite3.Connection, backlog_dir: Path):
        self.connection, self.backlog_dir = connection, backlog_dir

    def job(self, job: outbox.Job) -> bytes | None:
        safe_path(self.backlog_dir, job.file)
        if job.effect == "delete":
            return None
        entity = job.entity
        content, _oracle = store.render_entity_file(entity["kind"], entity["fields"], entity.get("body"))
        if content is None:
            return None
        default_crlf = None
        if job.moved_from:
            # A move carries the old file's line endings to the new path; the old
            # file is still on disk here, because every render precedes every publish.
            # A refused old path only loses its vote; its removal is refused on its own.
            try:
                default_crlf = store._probe_crlf(safe_path(self.backlog_dir, job.moved_from))
            except (OSError, UnsafePath):
                default_crlf = None
        if default_crlf is None:
            default_crlf = _dominant_crlf(self.backlog_dir)
        return store._match_line_endings(content, safe_path(self.backlog_dir, job.file), default_crlf)

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

    def _matched(self, rel: str, content: bytes) -> bytes:
        return store._match_line_endings(content, safe_path(self.backlog_dir, rel), _dominant_crlf(self.backlog_dir))

    def derived(self, warnings: list[str]) -> list[tuple[str, str, bytes, int]]:
        """`(file, kind, bytes, seq)` for every stale, unheld derived file, from one read snapshot."""
        out = []
        self.connection.execute("BEGIN")
        try:
            snapshot = Snapshot(self.connection, native_db.manifest(self.connection, authorities=("native",)))
            try:
                seq = self._stale(BACKLOG_FILE, ("backlog", "epic", "phase") + derived.KINDS)
                if seq is not None and not self._held(BACKLOG_FILE, warnings):
                    content = self._refusable(BACKLOG_FILE, warnings, lambda: self._matched(
                        BACKLOG_FILE, self.backlog(snapshot)))
                    if content is not None:
                        out.append((BACKLOG_FILE, "backlog", content, seq))
                seq = self._stale(store._IDEAS_INDEX_REL, ("idea",))
                if seq is not None and not self._held(store._IDEAS_INDEX_REL, warnings):
                    content = self.ideas(snapshot)
                    if content is not None:
                        content = self._refusable(store._IDEAS_INDEX_REL, warnings,
                                                  lambda: self._matched(store._IDEAS_INDEX_REL, content))
                    if content is not None:
                        out.append((store._IDEAS_INDEX_REL, store._IDEAS_INDEX_KIND, content, seq))
            finally:
                snapshot.active = False
        finally:
            self.connection.rollback()
        return out

    def backlog(self, snapshot: Snapshot) -> bytes:
        backlog = self._entities(snapshot, "backlog", "entity_key")
        data = derived.apply(snapshot, dict(backlog[0]["fields"]) if backlog else {})
        for kind, key in (("epic", "epics"), ("phase", "phases")):
            data[key] = []
            for entity in self._entities(snapshot, kind, "entity_key"):
                document = dict(entity["fields"])
                if entity.get("body"):
                    document[BODY_KEY] = entity["body"]
                data[key].append(document)
        content, _document = store.render_backlog_file(data)
        return content

    def ideas(self, snapshot: Snapshot) -> bytes | None:
        entries = [entity["fields"] for entity in self._entities(snapshot, "idea", "public_id")]
        if entries or (self.backlog_dir / store._IDEAS_INDEX_REL).exists():
            return render_ideas_index(entries).encode("utf-8")
        return None

    @staticmethod
    def _refusable(rel: str, warnings: list[str], render):
        """One derived file the path guard refuses is skipped with a notice, not fatal."""
        try:
            return render()
        except (OSError, UnsafePath) as exc:
            warnings.append(f"export pending: {rel} refused ({exc})")
            return None

    def _held(self, rel: str, warnings: list[str]) -> bool:
        reason = outbox.held_file(self.connection, rel)
        if reason is not None:
            warnings.append(f"export pending: {rel} is {reason}")
        return reason is not None


_DERIVED_INPUTS = ((BACKLOG_FILE, ("backlog", "epic", "phase") + derived.KINDS),
                   (store._IDEAS_INDEX_REL, ("idea",)))


def _behind(connection, through: int) -> list[str]:
    """The files a commit at or before `through` still owes, judged file by file.

    A job file is behind while an unheld job at or before `through` is pending or
    claimed. A derived file is behind while its own `exported_seq` is older than
    `through` and it is stale (older than its newest input row). Its inputs'
    `last_seq` alone cannot say: a later commit to the same row moves it past
    `through` and would hide the caller's change. Held files are reported by
    their own notice, not here. The stored `exported_through` watermark is not
    trusted for this: it covers jobs only, never `backlog.yaml` or `IDEAS.md`.
    """
    connection.execute("BEGIN")
    try:
        files = [r[0] for r in connection.execute(
            "SELECT DISTINCT j.file FROM projection_jobs j WHERE j.state IN ('pending','claimed') "
            f"AND j.commit_seq<=? AND NOT {outbox._held_sql(connection)} ORDER BY j.file", (through,))]
        for rel, kinds in _DERIVED_INPUTS:
            placeholders = ",".join("?" for _ in kinds)
            newest = connection.execute(
                f"SELECT COALESCE(MAX(last_seq),0) FROM entity_core WHERE kind IN ({placeholders})",
                kinds).fetchone()[0]
            row = connection.execute("SELECT exported_seq FROM projection WHERE file=?", (rel,)).fetchone()
            exported = None if row is None or row[0] is None else int(row[0])
            stale = newest and (exported is None or exported < int(newest))
            if stale and (exported is None or exported < through) and outbox.held_file(connection, rel) is None:
                files.append(rel)
        return files
    finally:
        connection.rollback()


def _pending_notices(connection, files: list[str] | None = None) -> list[str]:
    if files is None:
        files = [r[0] for r in connection.execute(
            "SELECT DISTINCT file FROM projection_jobs WHERE state IN ('pending','claimed') ORDER BY file")]
    return [f"export pending: {rel} — retried on next call" for rel in files] or [
        "export pending: projection files — retried on next call"]


def _owner(session: str) -> str:
    return f"{session}:{uuid.uuid4().hex[:12]}:{os.getpid()}@{socket.gethostname()}"


def drain(connection: sqlite3.Connection, backlog_dir: Path, *, session: str, through: int | None = None,
          progress_wait: bool = True) -> list[str]:
    """Export every pending projection job, then refresh stale derived files.

    Returns the `export pending: …` notices a caller must surface; it never
    raises for an export that could not finish, because the caller's command has
    already committed. Requires a connection with no open transaction. `through`
    is the caller's commit: while another exporter holds the lease the caller
    waits, at most `WAIT_SECONDS`, until every file that commit touched is
    exported (`_behind`), and otherwise reports those files pending. `through`
    and `progress_wait` also go to the PROGRESS export (`progress.export`).
    """
    if connection.in_transaction:
        raise RuntimeError("projection drain requires its own transaction")
    clock, sleep = HOOKS["clock"], HOOKS["sleep"]
    # The owner names its process (`:<pid>@<host>`), so `backlog_store_status` can say
    # whether a live lease's holder is still running (scope §5.4).
    exporter = outbox.Exporter(connection, backlog_dir, owner=_owner(session),
                               session=session, clock=clock, checkpoint=HOOKS["checkpoint"])
    deadline = clock() + WAIT_SECONDS
    while (jobs := exporter.claim()) is None:
        if through is not None:
            behind = _behind(connection, through)
            if not behind:
                return exporter.warnings + progress.export(connection, backlog_dir, session, through=through,
                                                           wait=progress_wait)
            if clock() >= deadline:
                return _pending_notices(connection, behind)
        elif clock() >= deadline:
            return _pending_notices(connection)
        sleep(_POLL_SECONDS)
    try:
        render = _Render(connection, backlog_dir)
        rendered = []
        for job in jobs:
            # A refused path (link/reparse entry, missing root) is one job's
            # problem: it goes back to pending with a notice, the rest publish.
            try:
                rendered.append((job, render.job(job)))
            except (OSError, UnsafePath) as exc:
                exporter.refuse(job, exc)
        files = render.derived(exporter.warnings)
        lost = False
        if rendered or files:
            try:
                exporter.intend(rendered + [(rel, content) for rel, _kind, content, _seq in files])
            except outbox.LeaseLost:
                # Another exporter took over while this one rendered: it owns the
                # jobs now. Nothing was touched; the caller is told, not raised at.
                lost = True
        try:
            if not lost:
                for job, content in rendered:
                    if exporter.publish(job, content) == "lost":
                        lost = True
                        break
            if not lost:
                for rel, kind, content, seq in files:
                    if exporter.publish_derived(rel, kind, content, seq) == "lost":
                        lost = True
                        break
        except OSError:
            # The command has committed; a filesystem refusal the protocol could
            # not turn into a notice itself still must not reach the caller.
            lost = True
        if lost:
            exporter.warnings.extend(_pending_notices(connection))
        exporter.finish()
    except BaseException:
        exporter.release()
        raise
    warnings = list(dict.fromkeys(exporter.warnings))
    return warnings + progress.export(connection, backlog_dir, session, through=through, wait=progress_wait)


# ── Conflict resolution reads (S10) ─────────────────────────────────────────
# Resolving a flag compares and records the bytes on disk. Reading a projection
# file is this module's privilege alone (the N08 bypass gate), so the resolver
# asks here rather than opening the file itself.


def read_file(backlog_dir: Path, rel: str) -> bytes | None:
    """The bytes of one projection file now, or None when it is missing."""
    try:
        return (backlog_dir / outbox.safe_relative(rel)).read_bytes()
    except FileNotFoundError:
        return None


def store_version(connection: sqlite3.Connection, backlog_dir: Path, kind: str, ident: str | None,
                  rel: str) -> tuple[str | None, str | None]:
    """`(text the store would write at rel, where the store's file lives)`, as the
    legacy `_store_version_of` answers it. Requires an open read transaction."""
    from taskmaster.native.commands import projection_path
    render = _Render(connection, backlog_dir)
    snapshot = Snapshot(connection, native_db.manifest(connection, authorities=("native",)))
    try:
        if kind == "backlog" or rel == BACKLOG_FILE:
            return render.backlog(snapshot).decode("utf-8"), rel
        if kind == store._IDEAS_INDEX_KIND:
            content = render.ideas(snapshot)
            return (None, None) if content is None else (content.decode("utf-8"), rel)
        try:
            entity = snapshot.get(kind, ident, include_body=True)
        except (KeyError, ValueError):
            return None, None
        target = projection_path(kind, ident, entity["archived"])
        content, _oracle = store.render_entity_file(kind, entity["fields"], entity.get("body"))
        if content is None or target is None:
            return None, None
        if target != rel:
            return None, target
        return content.decode("utf-8"), target
    finally:
        snapshot.active = False
