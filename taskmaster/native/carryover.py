# User intent: the N15 cutover oracle — prove that every piece of DB-local state a legacy
# store holds (queues, projection flags, sessions, changelogs, ID high water, reservations,
# tombstones, history sequence) survives backfill plus activation, and import it where needed.
"""Carry-over digest, verification and local-state import for the native cutover.

`snapshot_carryover` is read from the legacy side before backfill. `verify_carryover`
recomputes the same facts from the native side after activation (typically inside the
activation transaction, after `import_id_state` and `reconcile_progress`) and returns
human-readable differences; an empty list means everything was preserved.

Large values are recorded as `{rows|count, sha256}`, never raw content, so the digest is
small, deterministic and JSON-serializable. The reservation sidecar is re-read at verify
time (it is a legacy file the cutover never writes), so "every reservation kept" is checked
id by id without copying the list into the digest.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from taskmaster.admission import UnsupportedStoreError
from . import schema

DIGEST_VERSION = 1
SIDECAR = "id-reservations.json"
# Legacy local tables the native store keeps using in place (`schema.RETAINED_TABLES`),
# plus the conflict parking table: none of them may change across backfill + activation.
RETAINED = (*schema.RETAINED_TABLES, "projection_conflict")
# `meta` keys activation itself publishes (schema/protocol markers, the migration journal).
ACTIVATION_META_KEYS = frozenset({"schema_version", "minimum_client_protocol", "migration_state"})
ACTIVATION_META_PREFIXES = ("migration_", "cutover_")
# Native-only tables a repeated backfill must never touch.
NATIVE_LOCAL = ("projection_jobs", "command_receipts")
_LISTED = 5


def _prefixes():
    from .commands import PREFIXES
    return PREFIXES


# ── helpers ─────────────────────────────────────────────────────────────────

def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _cell(value):
    return {"$blob": bytes(value).hex()} if isinstance(value, (bytes, bytearray, memoryview)) else value


def _digest(items) -> dict:
    """`{rows, sha256}` of an iterable of JSON-able items, independent of their order."""
    encoded = sorted(_json(item) for item in items)
    return {"rows": len(encoded), "sha256": hashlib.sha256("\n".join(encoded).encode("utf-8")).hexdigest()}


def _exists(connection, table) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _activation_key(key: str) -> bool:
    return key in ACTIVATION_META_KEYS or key.startswith(ACTIVATION_META_PREFIXES)


def _table_digest(connection, table, *, skip_files=frozenset()) -> dict | None:
    if not _exists(connection, table):
        return None
    cursor = connection.execute(f'SELECT * FROM "{table}"')
    names = [d[0] for d in cursor.description]
    rows = (dict(zip(names, map(_cell, row))) for row in cursor)
    if table == "meta":
        rows = (row for row in rows if not _activation_key(str(row["key"])))
    if skip_files:
        rows = (row for row in rows if row.get("file") not in skip_files)
    return _digest(rows)


def _seeded_base_problems(connection, seeded) -> list[str]:
    """Each base activation seeded must be the published bytes of an unquarantined row."""
    bad = []
    for rel in sorted(seeded):
        row = connection.execute("SELECT b.content,p.content_hash,p.quarantined FROM projection_base b "
                                 "JOIN projection p ON p.file=b.file WHERE b.file=?", (rel,)).fetchone()
        if row is None or row[0] is None or row[2] or hashlib.sha1(bytes(row[0])).hexdigest() != row[1]:
            bad.append(rel)
    if not bad:
        return []
    shown = ", ".join(bad[:_LISTED]) + (" ..." if len(bad) > _LISTED else "")
    return [f"{len(bad)} activation-seeded projection_base row(s) are not the recorded published bytes: {shown}"]


def _sync_state_digest(connection) -> dict | None:
    """Non-changelog `sync_state` keys: the changelog is reconciled, everything else kept."""
    if not _exists(connection, "sync_state"):
        return None
    from taskmaster.native.workflow import PROGRESS_LEGACY_KEY
    return _digest([key, value] for key, value in connection.execute("SELECT key,value_json FROM sync_state")
                   if not key.startswith("progress.") and key != PROGRESS_LEGACY_KEY)


@contextmanager
def _read(connection):
    """One consistent read snapshot; joins a caller's transaction when there is one."""
    if connection.in_transaction:
        yield
        return
    connection.execute("BEGIN")
    try:
        yield
    finally:
        connection.rollback()


@contextmanager
def _write(connection):
    """Join a caller's transaction (activation), or own one `BEGIN IMMEDIATE` unit."""
    if connection.in_transaction:
        yield
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _database_dir(connection) -> Path | None:
    for _, name, path in connection.execute("PRAGMA database_list"):
        if name == "main":
            return Path(path).parent if path else None
    return None


def read_reservations(path: Path) -> dict[str, list[str]] | None:
    """The legacy sidecar as `{kind: [id, ...]}`; None when absent; ValueError when malformed.

    A malformed sidecar refuses the cutover: silently reading it as empty (as the legacy
    store's tolerant loader does) would let native recycle a reserved id.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{SIDECAR} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict) or not all(
            isinstance(ids, list) and all(isinstance(i, str) for i in ids) for ids in value.values()):
        raise ValueError(f"{SIDECAR} must map each kind to a list of id strings")
    return {str(kind): list(ids) for kind, ids in value.items()}


def _sidecar(connection) -> tuple[Path | None, dict[str, list[str]] | None]:
    directory = _database_dir(connection)
    if directory is None:
        return None, None
    path = directory / SIDECAR
    return path, read_reservations(path)


def _reservation_digest(reserved) -> dict:
    if reserved is None:
        return {"present": False, "count": 0, "sha256": None, "by_kind": {}}
    pairs = sorted({(kind, ident) for kind, ids in reserved.items() for ident in ids})
    return {"present": True, "count": len(pairs), "sha256": _digest(pairs)["sha256"],
            "by_kind": dict(sorted(Counter(kind for kind, _ in pairs).items()))}


def _high(ids, prefix) -> int:
    pattern = re.compile(re.escape(prefix) + r"(\d+)")
    return max((int(m.group(1)) for m in map(pattern.fullmatch, ids) if m), default=0)


def _high_water(connection, table, id_column, reserved) -> dict:
    """The twins' rule: every row of the kind (tombstones included) plus the sidecar."""
    result = {}
    for kind, prefix in _prefixes().items():
        ids = [r[0] for r in connection.execute(f"SELECT {id_column} FROM {table} WHERE kind=?", (kind,))]
        result[kind] = {"prefix": prefix, "high": _high(ids + list((reserved or {}).get(kind, [])), prefix)}
    return result


def _log_digest(entries) -> dict:
    return {"count": len(entries), "sha256": hashlib.sha256(_json(entries).encode("utf-8")).hexdigest()}


def _changes_digest(connection, table) -> dict:
    count, top = connection.execute(f"SELECT COUNT(*),COALESCE(MAX(seq),0) FROM {table}").fetchone()
    sequence = connection.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
    sequence = sequence[0] if sequence else 0
    return {"count": count, "max_seq": top, "sequence": sequence, "high_water": max(top, sequence)}


def _projection_flags(connection) -> dict:
    dirty, quarantined, both = connection.execute(
        "SELECT COALESCE(SUM(dirty=1),0),COALESCE(SUM(quarantined=1),0),COALESCE(SUM(dirty=1 AND quarantined=1),0) "
        "FROM projection").fetchone()
    return {"dirty": dirty, "quarantined": quarantined, "dirty_quarantined": both}


def _queue_states(connection) -> dict:
    return dict(sorted((str(state), n) for state, n in connection.execute(
        "SELECT state,COUNT(*) FROM linear_queue GROUP BY state")))


# ── public API ──────────────────────────────────────────────────────────────

def snapshot_carryover(connection: sqlite3.Connection) -> dict:
    """A deterministic, JSON-serializable digest of the legacy store's local state.

    Take it before backfill (or before a repeat backfill of a staging store), under the
    cutover fence. Refuses a store that is already native: its legacy state is gone.
    """
    from taskmaster.native_routing import progress
    from taskmaster.native.workflow import PROGRESS_LEGACY_KEY
    with _read(connection):
        meta = dict(connection.execute("SELECT key,value FROM meta"))
        if meta.get("schema_version") != "1":
            raise UnsupportedStoreError("the carry-over snapshot is taken from a schema-1 legacy authority before activation")
        _, reserved = _sidecar(connection)
        native_list = None
        seeded = None
        pending_rows = 0
        if _exists(connection, "sync_state"):
            native_list = progress._get(connection, PROGRESS_LEGACY_KEY)
            native_list = None if native_list is None else progress._entries(native_list)
            seeded = progress._get(connection, progress.SEEDED_KEY)
            pending_rows = len(progress._pending_rows(connection))
        tombstones = [list(r) for r in connection.execute("SELECT kind,id FROM entities WHERE deleted=1")]
        return {
            "version": DIGEST_VERSION,
            "retained": {table: _table_digest(connection, table) for table in RETAINED},
            "linear_queue": _queue_states(connection),
            "projection": _projection_flags(connection),
            "ids": {"high_water": _high_water(connection, "entities", "id", reserved),
                    "reservations": _reservation_digest(reserved)},
            "tombstones": {"count": len(tombstones), "sha256": _digest(tombstones)["sha256"]},
            "changes": _changes_digest(connection, "changes"),
            "progress": {"meta_applied": _log_digest(progress._meta_entries(connection, "progress_log")),
                         "meta_pending": _log_digest(progress._meta_entries(connection, "pending_progress_log")),
                         # Pre-N11 native list: only a staging store could hold one, and rarely.
                         "native_list": native_list, "seeded": seeded, "pending_rows": pending_rows},
            "native_local": {**{table: _table_digest(connection, table) for table in NATIVE_LOCAL},
                             "sync_state": _sync_state_digest(connection)},
        }


def _changed(label, before, now) -> str:
    rows = lambda d: "absent" if d is None else f"{d['rows']} rows"  # noqa: E731
    detail = f"{rows(before)} -> {rows(now)}" if rows(before) != rows(now) else "same row count, content differs"
    return f"{label} changed: {detail}"


def verify_carryover(connection: sqlite3.Connection, before: dict, *, seeded_bases=()) -> list[str]:
    """Recompute the carry-over digest from the native side; return the differences.

    Call it after backfill, activation, `import_id_state` and `reconcile_progress`, and
    before any native write (inside the activation transaction is ideal: a non-empty
    result there rolls the switch back). Read-only; joins a caller transaction.

    `seeded_bases`: the paths activation added to `projection_base` after its own
    verification (N16; the cutover journals them on its `activate` row). Seeding is
    additive, so the carried rows are exactly the others, and they must still match the
    snapshot; each seeded row must be the recorded published bytes of an unquarantined file.
    """
    from taskmaster.native_routing import progress
    from taskmaster.native.workflow import PROGRESS_LEGACY_KEY
    if not isinstance(before, dict) or before.get("version") != DIGEST_VERSION:
        return [f"carry-over digest version {before.get('version') if isinstance(before, dict) else None!r} "
                f"is not {DIGEST_VERSION}; take a new snapshot"]
    out: list[str] = []
    with _read(connection):
        authority = connection.execute("SELECT value FROM native_manifest WHERE key='authority'").fetchone() \
            if _exists(connection, "native_manifest") else None
        if authority is None or authority[0] != "native":
            out.append(f"native authority is not active (authority={authority[0] if authority else None!r})")

        # Retained legacy tables, read in place by the native store.
        seeded = frozenset(seeded_bases or ())
        for table in RETAINED:
            now = _table_digest(connection, table, skip_files=seeded if table == "projection_base" else frozenset())
            if now != before["retained"].get(table):
                out.append(_changed(f"retained table {table}", before["retained"].get(table), now))
        if seeded and _exists(connection, "projection_base"):
            out.extend(_seeded_base_problems(connection, seeded))
        if _exists(connection, "linear_queue") and (states := _queue_states(connection)) != before["linear_queue"]:
            out.append(f"linear_queue states changed: {before['linear_queue']} -> {states}")
        if _exists(connection, "projection") and (flags := _projection_flags(connection)) != before["projection"]:
            out.append(f"projection dirty/quarantine flags changed: {before['projection']} -> {flags}")

        # ID high water and reservations.
        for kind, expected in before["ids"]["high_water"].items():
            row = connection.execute("SELECT high_water FROM id_counters WHERE kind=? AND prefix=?",
                                     (kind, expected["prefix"])).fetchone()
            if row is None:
                out.append(f"id counter {kind}/{expected['prefix']} missing (legacy high water {expected['high']})")
            elif row[0] < expected["high"]:
                out.append(f"id counter {kind}/{expected['prefix']} is {row[0]}, below legacy high water {expected['high']}")
        path, reserved = None, None
        try:
            path, reserved = _sidecar(connection)
        except ValueError as exc:
            out.append(f"{SIDECAR} unreadable at verify: {exc}")
        else:
            if _reservation_digest(reserved) != before["ids"]["reservations"]:
                out.append(f"{SIDECAR} changed since the snapshot; reservations cannot be verified against it")
            elif reserved:
                missing = sorted((kind, ident) for kind, ids in reserved.items() for ident in set(ids)
                                 if connection.execute("SELECT 1 FROM id_reservations WHERE kind=? AND public_id=?",
                                                       (kind, ident)).fetchone() is None)
                if missing:
                    shown = ", ".join(f"{k}:{i}" for k, i in missing[:_LISTED])
                    out.append(f"{len(missing)} reservation(s) from {SIDECAR} not in id_reservations: {shown}"
                               + (" ..." if len(missing) > _LISTED else ""))

        # Tombstones.
        tombstones = [list(r) for r in connection.execute("SELECT kind,public_id FROM entity_core WHERE deleted=1")]
        now = {"count": len(tombstones), "sha256": _digest(tombstones)["sha256"]}
        if now != before["tombstones"]:
            out.append(f"tombstones changed: {before['tombstones']['count']} -> {now['count']} deleted identities")

        # History sequence.
        events, changes = _changes_digest(connection, "domain_events"), before["changes"]
        if events["max_seq"] != changes["max_seq"] or events["count"] != changes["count"]:
            out.append(f"domain_events max seq/count {events['max_seq']}/{events['count']} != legacy changes "
                       f"{changes['max_seq']}/{changes['count']}")
        # Backfill publishes the legacy high water as the AUTOINCREMENT sequence, so a
        # deleted tail seq is never reissued.
        if events["sequence"] != changes["high_water"]:
            out.append(f"domain_events sequence high water {events['sequence']} != legacy {changes['high_water']}")

        # Pending changelog, reconciled into sync_state.
        out.extend(_verify_progress(connection, before["progress"], progress, PROGRESS_LEGACY_KEY))

        # Native-only local state a repeat backfill must leave alone.
        for table in NATIVE_LOCAL:
            expected = before["native_local"].get(table) or _digest([])
            now = _table_digest(connection, table)
            if now != expected:
                out.append(_changed(f"native table {table}", expected, now))
        if before["native_local"].get("sync_state") is not None:
            now = _sync_state_digest(connection)
            if now != before["native_local"]["sync_state"]:
                out.append(_changed("sync_state (non-changelog keys)", before["native_local"]["sync_state"], now))
    return out


def _verify_progress(connection, before, progress, legacy_key) -> list[str]:
    out = []
    applied = progress._meta_entries(connection, "progress_log")
    pending = progress._meta_entries(connection, "pending_progress_log")
    if _log_digest(applied) != before["meta_applied"] or _log_digest(pending) != before["meta_pending"]:
        out.append("meta changelog (progress_log/pending_progress_log) changed since the snapshot")
    marker = progress._get(connection, progress.SEEDED_KEY)
    if marker is None:
        out.append(f"{progress.SEEDED_KEY} not recorded: the pending changelog was never reconciled")
    elif before["seeded"] is None and (marker.get("applied"), marker.get("pending")) != (len(applied), len(pending)):
        out.append(f"{progress.SEEDED_KEY} seeded {marker.get('applied')}/{marker.get('pending')} applied/pending "
                   f"entries, meta holds {len(applied)}/{len(pending)}")
    if progress._get(connection, legacy_key) is not None:
        out.append(f"sync_state {legacy_key} (pre-N11 native list) was not folded into pending rows")
    have = Counter(_json(e) for e in progress._entries(progress._get(connection, progress.APPLIED_KEY, [])))
    have.update(_json(entry) for _, entry in progress._pending_rows(connection))
    need = Counter(_json(e) for e in (before["native_list"] or []))
    if before["seeded"] is None:
        need.update(_json(e) for e in applied + pending)
    lost = need - have
    if lost:
        shown = "; ".join(json.loads(e).get("text", "")[:60] for e in list(lost.elements())[:_LISTED])
        out.append(f"{sum(lost.values())} pending changelog entr(ies) missing from native sync_state: {shown}")
    return out


def import_id_state(connection: sqlite3.Connection, root) -> dict:
    """Seed native `id_counters`/`id_reservations` from the rows and the legacy sidecar.

    Every allocated prefix gets a counter at the highest number among the kind's rows
    (tombstones included) and its sidecar reservations; every sidecar reservation is
    kept. Idempotent, and a counter is never lowered. Joins a caller's transaction
    (activation), else owns one. Raises ValueError on a malformed sidecar, before writing.
    """
    reserved = read_reservations(Path(root) / ".taskmaster" / "local" / SIDECAR) or {}
    with _write(connection):
        high_water = {}
        for kind, entry in _high_water(connection, "entity_core", "public_id", reserved).items():
            connection.execute("INSERT INTO id_counters(kind,prefix,high_water) VALUES(?,?,?) ON CONFLICT(kind,prefix) "
                               "DO UPDATE SET high_water=MAX(high_water,excluded.high_water)",
                               (kind, entry["prefix"], entry["high"]))
            high_water[kind] = connection.execute("SELECT high_water FROM id_counters WHERE kind=? AND prefix=?",
                                                  (kind, entry["prefix"])).fetchone()[0]
        pairs = sorted({(kind, ident) for kind, ids in reserved.items() for ident in ids})
        connection.executemany("INSERT OR IGNORE INTO id_reservations VALUES(?,?)", pairs)
    return {"high_water": high_water, "reservations": len(pairs)}


def reconcile_progress(connection: sqlite3.Connection) -> dict:
    """The explicit cutover form of N11's seed: copy the legacy `meta` session log and
    fold any pre-N11 native list into `sync_state` pending rows, recording `progress.seeded`.

    Runs `native_routing.progress.seed` itself (never a fork of it), then checks the
    result. Idempotent; a later `seed()` is a no-op. Call it after the final backfill,
    under the fence — inside the activation transaction is ideal — because a `meta`
    entry written after the marker is never copied.
    """
    from taskmaster.native_routing import progress
    from taskmaster.native.workflow import PROGRESS_LEGACY_KEY
    with _write(connection):
        progress.seed(connection)
        marker = progress._get(connection, progress.SEEDED_KEY)
        if marker is None or progress._get(connection, PROGRESS_LEGACY_KEY) is not None:
            raise RuntimeError("progress reconciliation did not complete")
        return {"seeded": marker, "pending": len(progress._pending_rows(connection)),
                "applied": len(progress._entries(progress._get(connection, progress.APPLIED_KEY, [])))}
