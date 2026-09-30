# User intent: the one seam through which the native drain renders PROGRESS.md (N11).
# Session-changelog paragraphs must never be lost across a crash at any step, the
# legacy store's existing session log must survive activation (D5), and the dashboard
# is refreshed at most every 5 s (D6), byte-identical to the legacy exporter's file.
"""Local PROGRESS.md export on native stores (N11 S7-S8).

State, all in `sync_state` (no DDL):

- `progress.pending.<commit_seq:012d>.<n:04d>`: one row per paragraph a completion
  queued (`native.workflow._queue_progress_log`), deleted only by the transaction
  that moves it into the applied log after the file carrying it is on disk.
- `progress.applied`: the paragraphs the session-log region is rendered from,
  capped at `CAP` with the legacy rule: the cap trims only the applied tail, never
  an unwritten paragraph.
- `progress.seeded`: set, in the same transaction, when the legacy `meta` session
  log (`progress_log`, `pending_progress_log`) was copied in. `meta` is only read:
  a write to it would mark the native store stale.
- `progress.writer`: `{owner, generation, until, rendered_at}`, the lease that
  makes one process at a time render and move, and the cross-process 5 s throttle.
- `progress.temps`: the temp names (`PROGRESS.md.tmp.<session>`) a lease holder
  recorded before writing one. A process killed between the temp write and the
  replace leaves its temp; the next lease holder drops every recorded name that
  still matches that pattern, and nothing else.

The region is regenerated whole from `applied[-room:] + pending` on every render,
so a crash between the file write and the move re-renders the identical file.
PROGRESS.md is a local, store-owned region inside a user-owned file: it is re-read
on each render and not verified against a recorded base (scope §2.4), as legacy.
The dashboard reads `reads.dashboard_tree` — every task, but only the fields the
dashboard renders and no prose (N16-B) — bounded by the throttle (D6).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from taskmaster.native.db import assert_native
from taskmaster.native.queries import Repository
from taskmaster.native.workflow import (
    PROGRESS_LEGACY_KEY,
    PROGRESS_PENDING_PREFIX,
    progress_key_range,
    progress_pending_key,
)

from . import reads

CAP = 200
APPLIED_KEY = "progress.applied"
SEEDED_KEY = "progress.seeded"
WRITER_KEY = "progress.writer"
TEMPS_KEY = "progress.temps"
_META_APPLIED, _META_PENDING = "progress_log", "pending_progress_log"
REL = "local/PROGRESS.md"
NOTICE = f"export pending: {REL} — retried on next call"
THROTTLE_SECONDS = 5.0
LEASE_SECONDS = 30.0
WAIT_SECONDS = 5.0
_POLL_SECONDS = 0.05
# Test seams: checkpoint(stage) at every step that a crash must survive, the clock
# the lease and throttle are measured on, and how a waiting caller sleeps.
HOOKS: dict = {"checkpoint": None, "clock": time.time, "sleep": time.sleep}
PENDING_PREFIX = PROGRESS_PENDING_PREFIX
_SEED_PREFIX = progress_pending_key(0, 0)[:-4]


class _Lost(RuntimeError):
    """Another writer took the lease; this one records nothing."""


def _get(connection, key, default=None):
    row = connection.execute("SELECT value_json FROM sync_state WHERE key=?", (key,)).fetchone()
    return default if row is None else json.loads(row[0])


def _put(connection, key, value) -> None:
    connection.execute("INSERT INTO sync_state(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                       "value_json=excluded.value_json", (key, json.dumps(value)))


def _entries(value) -> list[dict]:
    """A stored log as `{ts, text}` dicts, read the way the legacy `_progress_entries` reads it."""
    if not isinstance(value, list):
        return []
    return [entry if isinstance(entry, dict) else {"ts": "", "text": str(entry)} for entry in value]


def _pending_rows(connection) -> list[tuple[str, dict]]:
    return [(key, json.loads(value)) for key, value in connection.execute(
        "SELECT key,value_json FROM sync_state WHERE key>=? AND key<? ORDER BY key",
        progress_key_range(PENDING_PREFIX))]


def _owed(connection) -> bool:
    """Whether a paragraph waits for the file: a pending row, the pre-N11 list, or an unrun seed."""
    if connection.execute("SELECT 1 FROM sync_state WHERE key>=? AND key<? LIMIT 1",
                          progress_key_range(PENDING_PREFIX)).fetchone():
        return True
    return _get(connection, PROGRESS_LEGACY_KEY) is not None or _get(connection, SEEDED_KEY) is None


def _append_seed_rows(connection, entries: list[dict]) -> None:
    """Rows at commit sequence 0, after any already there: older than every completion."""
    taken = connection.execute("SELECT COUNT(*) FROM sync_state WHERE key>=? AND key<?",
                               progress_key_range(_SEED_PREFIX)).fetchone()[0]
    for n, entry in enumerate(entries, start=taken):
        connection.execute("INSERT INTO sync_state(key,value_json) VALUES(?,?)",
                           (progress_pending_key(0, n), json.dumps(entry)))


def _meta_entries(connection, key) -> list[dict]:
    row = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is None:
        return []
    try:
        return _entries(json.loads(row[0]))
    except (TypeError, ValueError):
        return []


def seed(connection) -> None:
    """Inside a write transaction: copy the legacy session log once (D5), read-only,
    and fold the pre-N11 native list into rows. Both are no-ops once done."""
    if _get(connection, SEEDED_KEY) is None:
        applied, pending = _meta_entries(connection, _META_APPLIED), _meta_entries(connection, _META_PENDING)
        # Keep both: an applied log already here (it cannot be, without the marker,
        # but a data path never overwrites on doubt) keeps its entries after the seed's.
        _put(connection, APPLIED_KEY, applied + _entries(_get(connection, APPLIED_KEY, [])))
        _append_seed_rows(connection, pending)
        high = int(connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0])
        _put(connection, SEEDED_KEY, {"seq": high, "applied": len(applied), "pending": len(pending)})
    legacy = _get(connection, PROGRESS_LEGACY_KEY)
    if legacy is not None:
        _append_seed_rows(connection, _entries(legacy))
        connection.execute("DELETE FROM sync_state WHERE key=?", (PROGRESS_LEGACY_KEY,))


class _Writer:
    def __init__(self, connection: sqlite3.Connection, session: str):
        import socket
        self.connection, self.session = connection, session
        self.owner = f"{session}:{uuid.uuid4().hex[:12]}:{os.getpid()}@{socket.gethostname()}"
        self.generation: int | None = None
        self.clock, self.sleep = HOOKS["clock"], HOOKS["sleep"]
        self.checkpoint = HOOKS["checkpoint"] or (lambda stage: None)
        database = Path(connection.execute("PRAGMA database_list").fetchone()[2])
        self.target = database.parent / "PROGRESS.md"
        self.temp = self.target.with_name(f"{self.target.name}.tmp.{session}")

    def _due(self, writer: dict, now: float) -> bool:
        rendered = writer.get("rendered_at")
        return rendered is None or now < float(rendered) or now - float(rendered) >= THROTTLE_SECONDS

    def _take(self) -> str:
        """`taken`, `busy` (another writer's lease is live) or `skip` (nothing due)."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            assert_native(self.connection)
            now = self.clock()
            writer = _get(self.connection, WRITER_KEY, {})
            if not _owed(self.connection) and not self._due(writer, now):
                self.connection.rollback()
                return "skip"
            if writer.get("owner") not in (None, self.owner) and float(writer.get("until", 0)) > now:
                self.connection.rollback()
                return "busy"
            self.generation = int(writer.get("generation", 0)) + 1
            _put(self.connection, WRITER_KEY, dict(writer, owner=self.owner, generation=self.generation,
                                                   until=now + LEASE_SECONDS))
            # Durable before the temp exists, so a kill after its write is recoverable.
            temps = [name for name in _get(self.connection, TEMPS_KEY, []) if name != self.temp.name]
            _put(self.connection, TEMPS_KEY, temps + [self.temp.name])
            seed(self.connection)
            self.checkpoint("progress_seeded")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return "taken"

    def _own_temp(self, name) -> Path | None:
        """The path of a recorded name only if it is this exporter's temp pattern, in
        PROGRESS.md's own directory; anything else is never touched."""
        prefix = f"{self.target.name}.tmp."
        if not isinstance(name, str) or not name.startswith(prefix) or len(name) == len(prefix):
            return None
        if "/" in name or "\\" in name or name in (".", "..") or Path(name).name != name:
            return None
        return self.target.with_name(name)

    def _drop_strays(self) -> None:
        """Holding the lease: remove temps an earlier writer recorded and left (it was
        killed before its replace). A temp is a render of committed state, never data."""
        for name in _get(self.connection, TEMPS_KEY, []):
            path = self._own_temp(name)
            if path is None or path == self.temp:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass    # still on record: the next lease holder tries again

    def _owns(self, writer: dict) -> bool:
        return writer.get("owner") == self.owner and writer.get("generation") == self.generation

    def release(self) -> None:
        """After a failure: end this generation's lease now, so the next caller need not wait."""
        if self.generation is None or self.connection.in_transaction:
            return
        try:
            self.connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error:
            return
        try:
            writer = _get(self.connection, WRITER_KEY, {})
            if self._owns(writer):
                _put(self.connection, WRITER_KEY, dict(writer, until=self.clock()))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def render(self) -> list[str]:
        from taskmaster import backlog_server as bs
        with Repository(self.connection).snapshot() as snapshot:
            applied = _entries(_get(self.connection, APPLIED_KEY, []))
            rows = _pending_rows(self.connection)
            data = reads.dashboard_tree(snapshot)
        pending = [entry for _key, entry in rows]
        room = max(CAP - len(pending), 0)
        entries = (applied[-room:] if room else []) + pending
        existing = self.target.read_text(encoding="utf-8") if self.target.exists() else ""
        rendered = bs._render_progress_dashboard(data, existing, entries)
        temp = self.temp
        self._drop_strays()
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            self.checkpoint("progress_temp_written")
            if not self._owns(_get(self.connection, WRITER_KEY, {})):
                raise _Lost("progress writer lease lost before replace")
            os.replace(temp, self.target)
        except BaseException:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        self.checkpoint("progress_written")
        self._apply(rows)
        return []

    def _apply(self, rows: list[tuple[str, dict]]) -> None:
        """Move the rendered paragraphs to the applied log and end the lease, as one unit."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            writer = _get(self.connection, WRITER_KEY, {})
            if not self._owns(writer):
                raise _Lost("progress writer lease lost before the move")
            moved = [entry for key, entry in rows
                     if self.connection.execute("DELETE FROM sync_state WHERE key=?", (key,)).rowcount]
            if moved:
                applied = _entries(_get(self.connection, APPLIED_KEY, []))
                room = max(CAP - len(moved), 0)
                _put(self.connection, APPLIED_KEY, (applied[-room:] if room else []) + moved)
            recorded = _get(self.connection, TEMPS_KEY, [])
            left = [name for name in recorded
                    if (path := self._own_temp(name)) is not None and path.exists()]
            if left != recorded:
                _put(self.connection, TEMPS_KEY, left)
            now = self.clock()
            _put(self.connection, WRITER_KEY, dict(writer, until=now, rendered_at=now))
            self.checkpoint("progress_applying")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise


def export(connection: sqlite3.Connection, backlog_dir: Path, session: str, *, through: int | None = None,
           wait: bool = True) -> list[str]:
    """Render PROGRESS.md if its changelog or dashboard is due; return `export pending` notices.

    Called by `native_routing.projection.drain` after the projection jobs, with no
    transaction open. `through` is the caller's commit: the caller owes only the
    paragraphs that commit queued (every pending paragraph when it is None). The
    command has committed, so a failure never raises: the paragraphs stay pending
    rows, the failure goes to `store.log` as legacy logs it, and a caller whose own
    paragraph is still pending is told. While another process holds the writer
    lease, only a caller owed a paragraph waits (bounded, and only when `wait`);
    one that owes only a dashboard refresh returns at once and reports nothing.
    """
    if connection.in_transaction:
        raise RuntimeError("the PROGRESS export requires its own transactions")
    writer = _Writer(connection, session)
    deadline = writer.clock() + WAIT_SECONDS
    try:
        while (outcome := writer._take()) != "taken":
            if outcome == "skip" or not wait or not _owes(connection, through):
                return []
            if writer.clock() >= deadline:
                return [NOTICE]
            writer.sleep(_POLL_SECONDS)
    except Exception as exc:  # noqa: BLE001 - the command committed; report, never raise
        return _failed(writer, connection, through, exc)
    try:
        return writer.render()
    except _Lost as exc:
        return _failed(writer, connection, through, exc)
    except Exception as exc:  # noqa: BLE001 - see docstring
        writer.release()
        return _failed(writer, connection, through, exc)
    except BaseException:
        writer.release()
        raise


def _owes(connection, through: int | None) -> bool:
    """Whether the caller's own paragraphs (its commit's rows) still wait for the file."""
    if through is None:
        return _owed(connection)
    return connection.execute("SELECT 1 FROM sync_state WHERE key>=? AND key<? LIMIT 1",
                              progress_key_range(progress_pending_key(through, 0)[:-4])).fetchone() is not None


def owes_through(connection, through: int) -> bool:
    """Cumulative coordinator barrier debt, unlike a command's exact-seq notice.

    Seeding/pre-N11 list import is owed before every barrier. Bound the key scan
    to pending rows and compare the numeric sequence, so the minimum 12-digit
    key padding is not mistaken for a maximum supported sequence width.
    """
    if _get(connection, PROGRESS_LEGACY_KEY) is not None or _get(connection, SEEDED_KEY) is None:
        return True
    return connection.execute(
        'SELECT 1 FROM sync_state WHERE key>=? AND key<? '
        'AND CAST(substr(key,?) AS INTEGER)<=? LIMIT 1',
        (*progress_key_range(PENDING_PREFIX), len(PENDING_PREFIX) + 1, through)).fetchone() is not None


def _failed(writer: "_Writer", connection, through: int | None, exc: BaseException) -> list[str]:
    _log(writer.target.parent, f"progress export failed: {exc!r}")
    try:
        owes = _owes(connection, through)
    except sqlite3.Error:
        owes = True
    return [NOTICE] if owes else []


def _log(directory: Path, message: str) -> None:
    """Append one line to `store.log` beside the database, as the legacy `Store._log` does."""
    from taskmaster import store
    path = directory / "store.log"
    try:
        if path.exists() and path.stat().st_size > 1024 * 1024:
            path.write_bytes(path.read_bytes()[-512 * 1024:])
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{store._now()} {message}\n")
    except OSError:
        pass
