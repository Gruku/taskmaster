# User intent: the one real, operator-run command that turns a legacy Taskmaster store into
# a native authority (N15): fenced, backed up, crash-safe and resumable, exactly reversible
# before activation commits and roll-forward-only after it (decision M1 = A).
"""Legacy -> native cutover: `python -m taskmaster.native.cutover --root <project>`.

Stages, in order, each idempotent and recorded in `native_cutover_journal`:

    fence      publish meta.migration_state='migrating' + owner/token (BEGIN IMMEDIATE,
               coordinator ownership lock held for the whole run)
    reconcile  re-check, under the fence, that no projection export is still pending
    backup     carry-over snapshot, sqlite3 online backup + JSON manifest, verified by reopening
    backfill   `migrate.backfill` with its checkpoints wired into the journal
    compare    a trial activation (always rolled back): `carryover.verify_carryover` must be []
    activate   one transaction: markers, authority, graph repair, ID import, progress
               reconcile, `verify_carryover` == [] (else the whole switch rolls back), ready
    release    record completion and drop the ownership lock

Journal design: a dedicated table, not `meta` keys. Every write to `meta` fires the
backfill's `native_stale_*` triggers and marks a verified staging snapshot stale, so
stage records written to `meta` after backfill would invalidate the very snapshot the
next stage activates. A table also keeps an append-only history (takeovers, sub-stage
checkpoints, failures) and commits in the same transaction as the stage it records.
Only the fence itself (`migration_state`/`migration_owner`/`migration_token`) lives in
`meta`, because that is what `admission.assert_compatible` reads.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import datetime as _dt
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import uuid
import zipfile

from taskmaster.admission import UnsupportedStoreError, assert_compatible, migration_owner

STAGES = ("fence", "reconcile", "backup", "backfill", "compare", "activate", "release")
JOURNAL = "native_cutover_journal"
FENCE_KEYS = ("migration_state", "migration_owner", "migration_token")
RUNBOOK = "docs/runbooks/native-cutover.md"
ESCAPE_HATCH = (
    "This store is already a native authority; rollback is refused after activation (decision M1: "
    "roll forward only). Escape hatch: flush a coherent projection generation from the native store "
    "and adopt those projection files into a fresh legacy store; DB-local state (receipts, sessions, "
    f"queue leases, history sequence) is lost. See {RUNBOOK}, 'Post-activation escape hatch'.")
# Legacy domain tables whose rows define "the same store" for rollback equivalence.
DOMAIN_TABLES = ("meta", "entities", "changes", "projection", "projection_base", "projection_conflict",
                 "sessions", "linear_queue", "entity_paths", "links", "related", "handover_tasks")
# Test seam: `HOOKS["checkpoint"](name)` runs at every stage boundary a crash must survive.
HOOKS: dict = {"checkpoint": None}
# A client that keeps writing through the fence is not absorbed forever.
MAX_DRIFT_ABSORPTIONS = 3


class CutoverRefused(RuntimeError):
    """A precondition or ownership check failed; nothing was changed by this call."""


class CutoverAborted(RuntimeError):
    """A stage failed after the fence went up; the fence stays for --resume or --rollback."""


def _checkpoint(name: str) -> None:
    hook = HOOKS.get("checkpoint")
    if hook is not None:
        hook(name)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")


# ── Seams to track-B primitives (resolved lazily so tests can stub them) ─────

def _migrate():
    return importlib.import_module("taskmaster.native.migrate")


def _quiesce():
    return importlib.import_module("taskmaster.native.quiesce")


MIGRATE_PRIMITIVES = ("backfill", "repair_graph_for_activation")
CARRYOVER_PRIMITIVES = ("snapshot_carryover", "verify_carryover", "import_id_state", "reconcile_progress",
                        "read_reservations")
QUIESCE_PRIMITIVES = ("live_owner", "open_writers", "scan_processes")


def _carryover():
    return importlib.import_module("taskmaster.native.carryover")


def missing_primitives() -> list[str]:
    """Primitives this command needs that the installed code lacks; none may be missing
    before a fence goes up."""
    missing = [f"migrate.{name}" for name in MIGRATE_PRIMITIVES if not hasattr(_migrate(), name)]
    try:
        carryover = _carryover()
        missing += [f"carryover.{name}" for name in CARRYOVER_PRIMITIVES if not hasattr(carryover, name)]
    except ImportError:
        missing.append("taskmaster.native.carryover")
    try:
        quiesce = _quiesce()
    except ImportError:
        return missing + ["taskmaster.native.quiesce"]
    return missing + [f"quiesce.{name}" for name in QUIESCE_PRIMITIVES if not hasattr(quiesce, name)]


def _require_primitives() -> None:
    missing = missing_primitives()
    if missing:
        raise CutoverRefused("this Taskmaster build lacks cutover primitives: " + ", ".join(missing))


def _encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=repr)


# ── Paths and store inspection ───────────────────────────────────────────────

def database_path(root: Path) -> Path:
    return Path(root) / ".taskmaster" / "local" / "store.db"


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None, timeout=30)


def _has_table(connection, name: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _meta(connection) -> dict:
    return dict(connection.execute("SELECT key,value FROM meta")) if _has_table(connection, "meta") else {}


def _native_manifest(connection) -> dict:
    return dict(connection.execute("SELECT key,value FROM native_manifest")) if _has_table(connection, "native_manifest") else {}


def _drop_journal(connection) -> None:
    connection.execute("DROP TABLE " + JOURNAL)


def journal(connection) -> list[dict]:
    if not _has_table(connection, JOURNAL):
        return []
    return [dict(seq=r[0], stage=r[1], status=r[2], owner=r[3], token=r[4], at=r[5], detail=json.loads(r[6] or "{}"))
            for r in connection.execute(f"SELECT seq,stage,status,owner,token,at,detail_json FROM {JOURNAL} ORDER BY seq")]


def _done(entries) -> list[str]:
    """Stages whose latest run still stands. A `drift` row (legacy writes absorbed after the
    backup) voids everything after the fence: reconcile, backup, backfill, compare re-run."""
    done: list[str] = []
    for entry in entries:
        if entry["stage"] == "drift":
            done = [stage for stage in done if stage == "fence"]
        elif entry["status"] == "done" and entry["stage"] in STAGES and entry["stage"] not in done:
            done.append(entry["stage"])
    return done


def _detail(entries, stage) -> dict:
    for entry in reversed(entries):
        if entry["stage"] == stage and entry["status"] == "done":
            return entry["detail"]
    return {}


def _record(connection, stage, status, owner, token, detail=None) -> None:
    connection.execute(f"INSERT INTO {JOURNAL}(stage,status,owner,token,at,detail_json) VALUES(?,?,?,?,?,?)",
                       (stage, status, owner, token, _now(), _encode(detail or {})))


def domain_digest(connection) -> str:
    """Digest of every legacy domain row, the fence keys excepted; the rollback equivalence."""
    digest = hashlib.sha256()
    for table in DOMAIN_TABLES:
        if not _has_table(connection, table):
            continue
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
        order = ",".join(f'"{c}"' for c in columns)
        where = f" WHERE key NOT IN ({','.join('?' for _ in FENCE_KEYS)})" if table == "meta" else ""
        digest.update(f"\0table:{table}\0".encode())
        for row in connection.execute(f'SELECT * FROM "{table}"{where} ORDER BY {order}',
                                      FENCE_KEYS if table == "meta" else ()):
            digest.update(_encode([r.hex() if isinstance(r, bytes) else r for r in row]).encode("utf-8"))
    if _has_table(connection, "entity_fts"):
        digest.update(b"\0table:entity_fts\0")
        for row in connection.execute("SELECT kind,id,title,body FROM entity_fts ORDER BY kind,id,title,body"):
            digest.update(_encode(list(row)).encode("utf-8"))
    sequence = connection.execute("SELECT seq FROM sqlite_sequence WHERE name='changes'").fetchone() \
        if _has_table(connection, "sqlite_sequence") else None
    digest.update(f"\0changes.seq:{sequence[0] if sequence else None}".encode())
    return digest.hexdigest()


def classify(connection) -> dict:
    """What kind of store this is, read-only."""
    meta, native = _meta(connection), _native_manifest(connection)
    entries = journal(connection)
    try:
        schema_version = int(meta.get("schema_version", "0"))
    except ValueError:
        schema_version = -1
    return {
        "schema_version": meta.get("schema_version"),
        "authority": native.get("authority", "legacy"),
        "staging_state": native.get("state"),
        "migration_state": meta.get("migration_state", "ready"),
        "migration_owner": meta.get("migration_owner"),
        "native": native.get("authority") == "native",
        "newer": schema_version > 1 and native.get("authority") != "native",
        "invalid": schema_version < 0,
        "journal": [{k: e[k] for k in ("seq", "stage", "status", "at")} for e in entries],
        "completed_stages": _done(entries),
    }


def reconcile_counts(connection, root: Path) -> dict:
    """Pending legacy export/import work that a cutover must not strand."""
    def count(sql):
        try:
            return connection.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            return 0
    intents = 0
    for path in sorted(database_path(root).parent.glob("export-intent.*.json")):
        try:
            entries = json.loads(path.read_text(encoding="utf-8")).get("entries", {})
        except (OSError, ValueError, AttributeError):
            entries = {"<unreadable>": None}
        intents += bool(entries)
    return {
        "dirty_unexported": count("SELECT COUNT(*) FROM projection WHERE dirty=1 AND quarantined=0"),
        "quarantined": count("SELECT COUNT(*) FROM projection WHERE quarantined=1"),
        "flagged_conflicts": count("SELECT COUNT(*) FROM projection_conflict"),
        "export_intents": intents,
        "linear_queue_open": count("SELECT COUNT(*) FROM linear_queue WHERE COALESCE(state,'') NOT IN ('done','failed')"),
        "entities": count("SELECT COUNT(*) FROM entities"),
        "changes": count("SELECT COUNT(*) FROM changes"),
    }


def _blocking(counts: dict) -> list[str]:
    reasons = []
    if counts["dirty_unexported"]:
        reasons.append(f"{counts['dirty_unexported']} dirty projection row(s) not yet exported")
    if counts["export_intents"]:
        reasons.append(f"{counts['export_intents']} unfinished export-intent file(s)")
    return reasons


def sidecar_refusal(root: Path) -> str | None:
    """A malformed `id-reservations.json` would abort the snapshot and the ID import."""
    try:
        _carryover().read_reservations(database_path(root).parent / "id-reservations.json")
    except ValueError as error:
        return f"malformed ID reservation sidecar: {error}; fix it before the cutover"
    return None


def _flush_hint(reasons) -> str:
    return ("Refusing cutover: " + "; ".join(reasons) + ". Flush them first: run any Taskmaster tool call "
            "(e.g. backlog_status) through a bridge client so the legacy store drains its exports, stop it "
            "again, then retry.")


# ── Preconditions ────────────────────────────────────────────────────────────

def _process_label(process: dict) -> str:
    parts = [f"pid {process.get('pid', '?')}"]
    for key in ("name", "launcher", "scope", "command_line"):
        if process.get(key):
            parts.append(str(process[key])[:160])
    return " ".join(parts)


def check_quiesced(root: Path, *, confirm_stopped: bool, probe_writers: bool = True) -> dict:
    """Refuse live owners and open writers; name matching processes unless confirmed stopped.

    `probe_writers=False` (the dry run) skips `open_writers`: its read-write probe
    connection can checkpoint a leftover WAL into the store file, which is a write.
    """
    quiesce = _quiesce()
    owner = quiesce.live_owner(Path(root))
    writers = quiesce.open_writers(database_path(root)) if probe_writers else "not probed in dry run"
    scan = quiesce.scan_processes(Path(root))
    processes = list(scan or [])
    report = {"live_owner": owner, "open_writers": writers, "processes": processes,
              "confirm_stopped": confirm_stopped, "refusals": [], "warnings": []}
    if getattr(scan, "note", None):
        report["warnings"].append(f"process scan: {scan.note}")
    if owner:
        report["refusals"].append(f"a live coordinator or service owns this project: {_encode(owner)}; stop it first")
    if writers is True:
        report["refusals"].append("the store has another open writer; stop every Taskmaster client first")
    elif writers is None:
        report["warnings"].append("open-writer probe was inconclusive (not a WAL database, or unreadable)")
    elif not probe_writers:
        report["warnings"].append("open writers not probed in dry run (the probe can checkpoint the WAL); "
                                  "the real run probes them")
    if processes:
        names = "; ".join(_process_label(p) for p in processes)
        if confirm_stopped:
            report["warnings"].append(f"proceeding past matching processes (--confirm-stopped): {names}")
        else:
            report["refusals"].append(
                f"processes from the launcher inventory may still use this project: {names}. Stop them "
                f"(see {RUNBOOK}) and re-run with --confirm-stopped once they are stopped")
    return report


def _store_refusals(state: dict, *, mode: str) -> list[str]:
    if state["invalid"]:
        return [f"invalid meta.schema_version {state['schema_version']!r}"]
    if state["newer"]:
        return [f"store schema_version {state['schema_version']} is newer than this legacy->native cutover supports"]
    fenced = state["migration_state"] != "ready"
    if mode == "run":
        if state["native"]:
            return ["the store is already a native authority; nothing to cut over"]
        if fenced:
            return [f"a cutover fence is already up (migration_state={state['migration_state']!r}, "
                    f"owner {state['migration_owner']!r}); use --resume or --rollback"]
        if state["journal"]:
            return [f"a cutover journal already exists ({len(state['journal'])} row(s), last "
                    f"{state['journal'][-1]['stage']!r}); use --resume or --rollback, never a fresh run over it"]
    return []


def handover_refusal(connection) -> str | None:
    """The legacy one-shot handover-status backfill (`backlog_server._ensure_handover_status_backfilled`)
    never runs on a native store, so handovers still owed a `status` must get it first."""
    if not _has_table(connection, "entities"):
        return None
    marker = connection.execute("SELECT json_extract(doc,'$.handover_status_backfilled') FROM entities "
                                "WHERE kind='backlog' LIMIT 1").fetchone()
    if marker and marker[0]:
        return None
    owed = connection.execute("SELECT COUNT(*) FROM entities WHERE kind='handover' AND deleted=0 "
                              "AND json_type(doc,'$.status') IS NULL").fetchone()[0]
    if not owed:
        return None
    return (f"the legacy handover-status backfill has not run on this store ({owed} handover(s) without a "
            "status). Start one bridge client, run backlog_handover_list once so it backfills them, stop the "
            "client again, then retry")


# ── Backup ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def projection_files(root: Path) -> list[dict]:
    base = Path(root) / ".taskmaster"
    files = []
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base).as_posix()
        if path.is_file() and not rel.startswith("local/") and ".tmp." not in rel:
            files.append({"path": rel, "sha256": _sha256(path), "size": path.stat().st_size})
    return files


def _fsync(path: Path) -> None:
    """Flush a file, then its directory entry (a no-op where directories cannot be opened)."""
    with open(path, "rb+") as stream:
        os.fsync(stream.fileno())
    try:
        descriptor = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return  # Windows: directories cannot be opened for fsync; NTFS journals the rename.
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _stamped(root: Path, prefix: str) -> Path:
    directory = database_path(root).parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target, n = directory / f"{prefix}-{stamp}.db", 1
    while target.exists() or target.with_suffix(".json").exists():
        target, n = directory / f"{prefix}-{stamp}-{n}.db", n + 1
    return target


def _backup_target(root: Path) -> Path:
    return _stamped(root, "pre-native")


def verify_backup(path: Path, expected_digest: str | None = None) -> str:
    """Reopen the backup read-only; integrity_check must be ok. Returns its domain digest."""
    if not path.exists():
        raise CutoverAborted(f"backup {path} is missing")
    with closing(_connect_readonly(path)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchall()
        if [tuple(r) for r in result] != [("ok",)]:
            raise CutoverAborted(f"backup {path} failed integrity_check: {result[:5]}")
        digest = domain_digest(connection)
    if expected_digest is not None and digest != expected_digest:
        raise CutoverAborted(f"backup {path} does not match the store it was taken from")
    return digest


def write_backup(connection, root: Path, carryover: dict) -> dict:
    target = _backup_target(root)
    temp = target.with_name(target.name + ".partial")
    temp.unlink(missing_ok=True)
    with closing(sqlite3.connect(temp)) as destination:
        connection.backup(destination)
    os.replace(temp, target)
    _fsync(target)
    live = domain_digest(connection)
    backup_digest = verify_backup(target, live)
    sidecar = database_path(root).parent / "id-reservations.json"
    sidecar_sha = _sha256(sidecar) if sidecar.exists() else None
    archive = archive_projection(root, target.with_suffix(".projection.zip"))
    sidecar_copy = None
    if sidecar.exists():
        sidecar_copy = target.with_name(target.stem + ".id-reservations.json")
        shutil.copyfile(sidecar, sidecar_copy)
        _fsync(sidecar_copy)
    manifest = {
        "created_at": _now(), "store": str(database_path(root)), "backup": str(target),
        "backup_sha256": _sha256(target), "domain_digest": backup_digest,
        "projection_files": projection_files(root),
        "id_reservations": None if sidecar_copy is None else {
            "source": str(sidecar), "copy": str(sidecar_copy), "sha256": _sha256(sidecar_copy)},
        "projection_archive": archive,
        "carryover": carryover,
    }
    manifest_path = target.with_suffix(".json")
    manifest_path.write_text(_encode(manifest), encoding="utf-8")
    _fsync(manifest_path)
    return {"path": str(target), "manifest": str(manifest_path), "domain_digest": backup_digest,
            "sha256": manifest["backup_sha256"], "projection_files": len(manifest["projection_files"]),
            "projection_archive": archive, "sidecar_sha256": sidecar_sha}


# ── Activation core (production; the twins fixture flips authority through it) ──

class CarryoverMismatch(RuntimeError):
    """`verify_carryover` found local state the switch would lose; the switch rolled back."""


def activate(connection, root: Path, *, token: str | None = None, before: dict | None = None,
             record=None, trial: bool = False) -> dict:
    """One transaction: publish native markers and authority, repair the graph, import ID
    state, reconcile the progress changelog and, given the pre-backfill carry-over snapshot
    `before`, require `verify_carryover` to report nothing lost. A fenced cutover also
    returns `migration_state` to `ready` here.

    `token=None` is the unfenced fast path for test fixtures; it requires an unfenced store.
    `record(connection)` runs inside the transaction, before commit (the journal row).
    `trial=True` does all of it and rolls back: the cutover's compare stage.
    """
    migrate, carryover = _migrate(), _carryover()
    from .db import manifest
    if connection.in_transaction:
        raise RuntimeError("activation requires its own transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        meta = _meta(connection)
        state = meta.get("migration_state", "ready")
        if token is None:
            if state != "ready":
                raise UnsupportedStoreError(f"unfenced activation refused: migration_state={state!r}")
        elif state != "migrating" or meta.get("migration_token") != token:
            raise UnsupportedStoreError("activation requires the cutover fence this caller owns")
        if meta.get("schema_version") != "1":
            raise UnsupportedStoreError(f"activation requires a schema-1 legacy authority, found {meta.get('schema_version')!r}")
        staging = manifest(connection)
        if staging.get("state") != "verified":
            raise UnsupportedStoreError(f"native staging is {staging.get('state')!r}, not verified; repeat backfill")
        # Meta writes fire the staging-stale triggers, so they come before `state='ready'`.
        connection.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        connection.execute("INSERT INTO meta(key,value) VALUES('minimum_client_protocol','2') "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        if token is not None:
            connection.execute("UPDATE meta SET value='ready' WHERE key='migration_state'")
            connection.execute("DELETE FROM meta WHERE key IN ('migration_owner','migration_token')")
        connection.execute("UPDATE native_manifest SET value='native' WHERE key='authority'")
        connection.execute("UPDATE native_manifest SET value='ready' WHERE key='state'")
        connection.execute("INSERT INTO native_manifest VALUES('local_state_imported','1') "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        # The one-time graph repair belongs to the authority switch (N14).
        repair = migrate.repair_graph_for_activation(connection)
        # The carry-over primitives join this transaction: IDs, then the progress
        # changelog (after the final backfill, under the fence), then verification.
        ids = carryover.import_id_state(connection, Path(root))
        progress = carryover.reconcile_progress(connection)
        problems = [] if before is None else list(carryover.verify_carryover(connection, before))
        if problems:
            raise CarryoverMismatch("carry-over verification failed; activation rolled back: " + "; ".join(problems))
        result = {"graph_repair": repair, "ids": ids, "progress": progress, "verified": before is not None}
        if trial:
            connection.rollback()
            return result
        if record is not None:
            record(connection)
        _checkpoint("activate:before-commit")
        connection.commit()
        return result
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


# ── The cutover run ──────────────────────────────────────────────────────────

class _Run:
    def __init__(self, root: Path, connection, owner: str, token: str, log):
        self.root, self.connection, self.owner, self.token, self.log = root, connection, owner, token, log
        self.report: dict = {"stages": {}}

    def _begin_owned(self):
        self.connection.execute("BEGIN IMMEDIATE")
        meta = _meta(self.connection)
        if meta.get("migration_state") != "migrating" or meta.get("migration_token") != self.token:
            self.connection.rollback()
            raise CutoverAborted("the cutover fence changed underneath this run; stopping")

    def _commit_stage(self, stage, detail):
        _record(self.connection, stage, "done", self.owner, self.token, detail)
        _checkpoint(f"{stage}:before-commit")
        self.connection.commit()
        self.report["stages"][stage] = detail
        self.log(f"[{stage}] done")
        _checkpoint(f"{stage}:after-commit")

    def _fail(self, stage, error):
        try:
            if self.connection.in_transaction:
                self.connection.rollback()
            self.connection.execute("BEGIN IMMEDIATE")
            _record(self.connection, stage, "failed", self.owner, self.token, {"error": str(error)})
            self.connection.commit()
        except sqlite3.Error:
            pass

    def fence(self):
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            assert_compatible(connection)
            meta = _meta(connection)
            if meta.get("schema_version") != "1" or not meta.get("creation_token"):
                raise UnsupportedStoreError("cutover requires an initialized schema-1 legacy authority")
            previous = {key: meta.get(key) for key in FENCE_KEYS}
            digest = domain_digest(connection)
            connection.execute(f"CREATE TABLE IF NOT EXISTS {JOURNAL}(seq INTEGER PRIMARY KEY, stage TEXT NOT NULL, "
                               "status TEXT NOT NULL, owner TEXT, token TEXT, at TEXT NOT NULL, detail_json TEXT)")
            connection.executemany("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                   [("migration_state", "migrating"), ("migration_owner", self.owner),
                                    ("migration_token", self.token)])
            self._commit_stage("fence", {"previous_meta": previous, "domain_digest": digest,
                                         "pid": os.getpid(), "host": socket.gethostname()})
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    def reconcile(self):
        # The progress changelog itself is reconciled inside the activation transaction,
        # after the final backfill (`carryover.reconcile_progress`): a meta entry written
        # after its seed marker would never be copied. Here: no export may be stranded.
        self._begin_owned()
        counts = reconcile_counts(self.connection, self.root)
        blocking = _blocking(counts)
        if blocking:
            self.connection.rollback()
            raise CutoverAborted(_flush_hint(blocking) + " The fence is up: run --rollback, flush, and start again.")
        self._commit_stage("reconcile", {"counts": counts, "domain_digest": domain_digest(self.connection)})

    def backup(self):
        try:
            with migration_owner(self.token):
                carryover = _carryover().snapshot_carryover(self.connection)
        except ValueError as error:
            raise CutoverAborted(f"carry-over snapshot failed: {error}") from error
        if self.connection.in_transaction:
            raise CutoverAborted("snapshot_carryover left a transaction open")
        result = write_backup(self.connection, self.root, carryover)
        self._begin_owned()
        if domain_digest(self.connection) != result["domain_digest"]:
            self.connection.rollback()
            raise CutoverAborted("the store changed while it was being backed up")
        # The snapshot commits with the stage row: resume never depends on the external manifest.
        self._commit_stage("backup", {**result, "carryover": carryover})

    def backfill(self):
        def checkpoint(sub):
            # Runs inside backfill's own transaction: the rows commit with it or not at all.
            _record(self.connection, f"backfill.{sub}", "done", self.owner, self.token)
            if sub == "verified":
                _record(self.connection, "backfill", "done", self.owner, self.token)
                _checkpoint("backfill:before-commit")
            else:
                _checkpoint(f"backfill.{sub}")
        with migration_owner(self.token):
            result = _migrate().backfill(self.connection, checkpoint=checkpoint)
        self.report["stages"]["backfill"] = result
        self.log(f"[backfill] done: {result.get('entities')} entities, event high water {result.get('event_high_water')}")
        _checkpoint("backfill:after-commit")

    def _before(self) -> dict:
        detail = _detail(journal(self.connection), "backup")
        if "carryover" in detail:
            return detail["carryover"]
        manifest_path = Path(detail.get("manifest", "<unrecorded>"))
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))["carryover"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CutoverAborted(
                f"the carry-over snapshot is not in the journal and the backup manifest {manifest_path} is "
                f"unreadable ({type(error).__name__}: {error}); run --rollback and start the cutover again") from error

    def compare(self):
        try:
            trial = activate(self.connection, self.root, token=self.token, before=self._before(), trial=True)
        except (CarryoverMismatch, UnsupportedStoreError, ValueError) as error:
            raise CutoverAborted(f"trial activation failed; nothing activated: {error}") from error
        self._begin_owned()
        self._commit_stage("compare", {"problems": [], "ids": trial["ids"], "progress": trial["progress"]})

    def activate(self):
        def record(connection):
            _record(connection, "activate", "done", self.owner, self.token)
        try:
            result = activate(self.connection, self.root, token=self.token, before=self._before(), record=record)
        except (CarryoverMismatch, ValueError) as error:
            raise CutoverAborted(str(error)) from error
        self.report["stages"]["activate"] = result
        self.log(f"[activate] done: authority=native; graph repair {result['graph_repair']}; ids {result['ids']}")
        _checkpoint("activate:after-commit")

    def release(self):
        self.connection.execute("BEGIN IMMEDIATE")
        self._commit_stage("release", {})

    def _drift(self) -> list[str]:
        """Legacy writes since the latest backup: domain rows (retained tables included) or the
        ID-reservation sidecar. The carry-over snapshot and staging no longer describe them."""
        detail = _detail(journal(self.connection), "backup")
        reasons = []
        if domain_digest(self.connection) != detail.get("domain_digest"):
            source = Path(detail["path"])
            with closing(_connect_readonly(source)) as reference:
                changes = domain_differences(self.connection, reference)
            reasons.append(_summarize(changes) or "legacy domain rows changed")
        sidecar = database_path(self.root).parent / "id-reservations.json"
        if (_sha256(sidecar) if sidecar.exists() else None) != detail.get("sidecar_sha256"):
            reasons.append("id-reservations.json changed")
        return reasons

    def run_from(self, done):
        done, absorbed = list(done), 0
        while True:
            pending = [stage for stage in STAGES if stage not in done]
            if not pending:
                return
            stage = pending[0]
            if stage in ("backfill", "compare", "activate") and "backup" in done:
                reasons = self._drift()
                if reasons:
                    absorbed += 1
                    if absorbed > MAX_DRIFT_ABSORPTIONS:
                        raise CutoverAborted("the store keeps changing under the fence (" + "; ".join(reasons)
                                             + "): a client is still writing. Stop it, then --resume or --rollback")
                    self.connection.execute("BEGIN IMMEDIATE")
                    _record(self.connection, "drift", "done", self.owner, self.token, {"reasons": reasons})
                    self.connection.commit()
                    warning = ("absorbed writes made through the fence after the backup (" + "; ".join(reasons)
                               + "): re-running reconcile, backup and backfill so they carry into the native store")
                    self.report.setdefault("warnings", []).append(warning)
                    self.report["drift_absorbed"] = absorbed
                    self.log(f"warning: {warning}")
                    _checkpoint("drift:recorded")
                    done = [s for s in done if s == "fence"]
                    continue
            _checkpoint(f"{stage}:begin")
            try:
                getattr(self, stage)()
            except BaseException as error:
                if stage not in ("fence",) and not isinstance(error, (KeyboardInterrupt, SystemExit)):
                    self._fail(stage, error)
                raise
            done.append(stage)


def _ownership(root: Path):
    from taskmaster.coordinator.ownership import Ownership, OwnershipUnavailable
    try:
        return Ownership(Path(root)).acquire()
    except OwnershipUnavailable as error:
        raise CutoverRefused("the coordinator ownership lock is held by a live process; stop it first") from error


def dry_run(root: Path, *, confirm_stopped: bool = False) -> dict:
    """Every check, the counts and the planned actions. Writes nothing: the store is opened
    read-only and `open_writers` is not probed. `live_owner` may take and at once release
    the ownership byte lock when `owner.lock` exists; that changes no file bytes."""
    root = Path(root)
    path = database_path(root)
    report = {"mode": "dry-run", "root": str(root), "store": str(path), "refusals": [], "warnings": []}
    missing = missing_primitives()
    if missing:
        report["refusals"].append("this Taskmaster build lacks cutover primitives: " + ", ".join(missing))
        report["ok"] = False
        return report
    if not path.exists():
        report["refusals"].append(f"no store at {path}")
        return report
    with closing(_connect_readonly(path)) as connection:
        connection.execute("BEGIN")
        try:
            state = classify(connection)
            report["store_state"] = state
            report["refusals"] += _store_refusals(state, mode="run")
            counts = reconcile_counts(connection, root)
            report["counts"] = counts
            if not state["native"] and state["migration_state"] == "ready":
                report["refusals"] += [_flush_hint(_blocking(counts))] if _blocking(counts) else []
                report["refusals"] += [r for r in [handover_refusal(connection)] if r]
                try:
                    assert_compatible(connection)
                except UnsupportedStoreError as error:
                    report["refusals"].append(str(error))
                sidecar = sidecar_refusal(root)
                if sidecar:
                    report["refusals"].append(sidecar)
                else:
                    report["carryover"] = _carryover().snapshot_carryover(connection)
            report["domain_digest"] = domain_digest(connection)
        finally:
            connection.rollback()
    quiesced = check_quiesced(root, confirm_stopped=confirm_stopped, probe_writers=False)
    report["quiesce"] = quiesced
    report["refusals"] += quiesced["refusals"]
    report["warnings"] += quiesced["warnings"]
    report["projection_files"] = len(projection_files(root))
    report["planned"] = [] if report["refusals"] else [
        "fence: publish meta.migration_state='migrating' with owner/token under the ownership lock",
        "reconcile: re-check unexported projection work under the fence",
        f"backup: {path.parent / 'backups' / 'pre-native-<UTC ts>.db'} + manifest ({report['projection_files']} projection files)",
        f"backfill: stage {counts['entities']} entities and {counts['changes']} changes into native tables",
        "compare: trial activation, rolled back; verify_carryover must report nothing lost",
        "activate: one transaction: schema_version=2, minimum_client_protocol=2, migration_state=ready, "
        "authority=native, graph repair, ID import, progress reconcile, verify_carryover (any loss rolls it back)",
        "release: record completion, release the ownership lock",
    ]
    report["ok"] = not report["refusals"]
    return report


def cutover(root: Path, *, confirm_stopped: bool = False, resume: bool = False, token: str | None = None,
            log=lambda line: None) -> dict:
    """Run (or `resume`) the cutover to completion. Raises CutoverRefused / CutoverAborted."""
    root = Path(root)
    path = database_path(root)
    _require_primitives()
    if not path.exists():
        raise CutoverRefused(f"no store at {path}")
    mode = "resume" if resume else "run"
    with closing(_connect_readonly(path)) as probe:
        state = classify(probe)
        counts = reconcile_counts(probe, root)
        handovers = handover_refusal(probe)
    refusals = _store_refusals(state, mode=mode)
    activated = state["native"] or "activate" in state["completed_stages"]
    if resume and activated:
        return _finish_release(path, token=token, log=log)
    if not resume and not refusals and _blocking(counts):
        refusals.append(_flush_hint(_blocking(counts)))
    if not resume and not refusals and handovers:
        refusals.append(handovers)
    if not activated:
        refusals += [r for r in [sidecar_refusal(root)] if r]
    quiesced = check_quiesced(root, confirm_stopped=confirm_stopped)
    refusals += quiesced["refusals"]
    if refusals:
        raise CutoverRefused("; ".join(refusals))
    for warning in quiesced["warnings"]:
        log(f"warning: {warning}")
    ownership = _ownership(root)
    try:
        with closing(_connect(path)) as connection:
            entries = journal(connection)
            done = _done(entries)
            if resume:
                if not entries:
                    raise CutoverRefused("no cutover journal to resume; run without --resume")
                fence = next(e for e in entries if e["stage"] == "fence")
                if token is not None and token != fence["token"]:
                    raise CutoverRefused("--token does not match the cutover journal's token")
                meta = _meta(connection)
                activated = "activate" in done
                if not activated and meta.get("migration_token") != fence["token"]:
                    raise CutoverRefused("the store's fence token does not match its journal; refusing to resume")
                owner, run_token = f"cutover:{uuid.uuid4().hex[:8]}@{socket.gethostname()}:{os.getpid()}", fence["token"]
                # Takeover rule: holding the ownership lock proves the fencing process is gone
                # (it holds that lock for its whole run), so its token is inherited, not rotated:
                # rotating it would write `meta` and stale the verified staging snapshot.
                connection.execute("BEGIN IMMEDIATE")
                _record(connection, "takeover", "done", owner, run_token,
                        {"from_owner": fence["owner"], "resume_after": done[-1] if done else None})
                connection.commit()
                log(f"resuming cutover {run_token} after stage {done[-1] if done else '-'}")
            else:
                owner = f"cutover:{uuid.uuid4().hex[:8]}@{socket.gethostname()}:{os.getpid()}"
                run_token = uuid.uuid4().hex
                log(f"cutover token {run_token}")
            run = _Run(root, connection, owner, run_token, log)
            if resume and "backfill" in done and "activate" not in done and \
                    _native_manifest(connection).get("state") != "verified":
                done = [s for s in done if s not in ("backfill", "compare")]  # Stale staging: redo.
            run.run_from(done)
            report = run.report
            report.update(ok=True, token=run_token, mode=mode, completed_stages=_done(journal(connection)))
            return report
    finally:
        ownership.close()


def _finish_release(path: Path, *, token: str | None, log) -> dict:
    """After activation committed, only the `release` row is owed. Native clients may
    already be running (and a native coordinator holds the ownership lock), so neither
    quiesce nor the lock is required: the fence is gone and the row is bookkeeping."""
    with closing(_connect(path)) as connection:
        entries = journal(connection)
        if not entries:
            raise CutoverRefused("the store is native but has no cutover journal; nothing to resume")
        fence = next(e for e in entries if e["stage"] == "fence")
        if token is not None and token != fence["token"]:
            raise CutoverRefused("--token does not match the cutover journal's token")
        owner = f"cutover:{uuid.uuid4().hex[:8]}@{socket.gethostname()}:{os.getpid()}"
        if "release" not in _done(entries):
            connection.execute("BEGIN IMMEDIATE")
            _record(connection, "takeover", "done", owner, fence["token"],
                    {"from_owner": fence["owner"], "resume_after": "activate", "post_activation": True})
            _record(connection, "release", "done", owner, fence["token"], {})
            connection.commit()
            log("[release] done (post-activation resume; quiesce not required)")
        return {"ok": True, "mode": "resume", "token": fence["token"], "stages": {},
                "completed_stages": _done(journal(connection))}


# Tables whose rows are acknowledged user or queue writes: a restore that would drop a
# difference in them needs `--discard-writes-since-backup`. `meta` counts (session changelog,
# Linear receipts, ...) apart from the cutover's own `migration_*`/`cutover_*` keys; `sessions`
# is disposable presence state and does not.
WRITE_TABLES = ("entities", "changes", "linear_queue", "projection", "meta")
KEY_COLUMNS = {"meta": ("key",), "entities": ("kind", "id"), "changes": ("seq",), "linear_queue": ("seq",),
               "sessions": ("session",), "projection": ("file",), "projection_base": ("file",),
               "projection_conflict": ("file",)}
CUTOVER_META_PREFIXES = ("migration_", "cutover_")


def _has(connection, name: str, schema: str = "main") -> bool:
    return connection.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
                              (name,)).fetchone() is not None


def _columns(connection, table: str, schema: str = "main") -> list[str]:
    return [row[1] for row in connection.execute(f'PRAGMA {schema}.table_info("{table}")')]


def _keyed_rows(connection, table, schema="main") -> dict:
    if not _has(connection, table, schema):
        return {}
    columns = _columns(connection, table, schema)
    keys = KEY_COLUMNS.get(table)
    result, seen = {}, {}
    for row in connection.execute(f'SELECT * FROM {schema}."{table}"'):
        values = dict(zip(columns, [r.hex() if isinstance(r, bytes) else r for r in row]))
        if table == "meta" and str(values["key"]).startswith(CUTOVER_META_PREFIXES):
            continue
        if keys:
            key = tuple(values[c] for c in keys)
        else:  # No natural key: a multiset of whole rows.
            whole = _encode(values)
            seen[whole] = seen.get(whole, 0) + 1
            key = (whole, seen[whole])
        result[key] = _encode(values)
    return result


def _fts_rows(connection, schema="main") -> dict:
    if not _has(connection, "entity_fts", schema):
        return {}
    result, seen = {}, {}
    for row in connection.execute(f"SELECT kind,id,title,body FROM {schema}.entity_fts"):
        whole = _encode(list(row))
        seen[whole] = seen.get(whole, 0) + 1
        result[(row[0], row[1], seen[whole], whole)] = whole
    return result


def domain_differences(current, reference, *, current_schema="main", reference_schema="main") -> dict:
    """Per legacy domain table: rows added, removed or changed in `current` vs `reference`
    (two connections, or one connection with the reference ATTACHed as another schema)."""
    out = {}
    for table in (*DOMAIN_TABLES, "entity_fts"):
        if table == "entity_fts":
            now, then = _fts_rows(current, current_schema), _fts_rows(reference, reference_schema)
        else:
            now = _keyed_rows(current, table, current_schema)
            then = _keyed_rows(reference, table, reference_schema)
        added = sorted(set(now) - set(then), key=repr)
        removed = sorted(set(then) - set(now), key=repr)
        changed = sorted((k for k in set(now) & set(then) if now[k] != then[k]), key=repr)
        if added or removed or changed:
            shown = [list(k[:2]) if table == "entity_fts" else list(k) for k in (added + removed + changed)[:10]]
            out[table] = {"added": len(added), "removed": len(removed), "changed": len(changed), "keys": shown}
    return out


def _summarize(differences: dict) -> str:
    return "; ".join(f"{table}: {d['added']} added, {d['removed']} removed, {d['changed']} changed "
                     f"(e.g. {_encode(d['keys'][:5])})" for table, d in differences.items())


def _summarize_files(divergence: dict) -> str:
    return "; ".join(f"{len(paths)} {kind} ({', '.join(paths[:5])}{', ...' if len(paths) > 5 else ''})"
                     for kind, paths in divergence.items() if paths)


# ── Projection-file archive (taken with every backup) ────────────────────────

def archive_projection(root: Path, target: Path) -> dict:
    """Zip the projection files (the manifest's list) beside the backup."""
    base = Path(root) / ".taskmaster"
    files = projection_files(root)
    temp = target.with_name(target.name + ".partial")
    temp.unlink(missing_ok=True)
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in files:
            archive.write(base / entry["path"], entry["path"])
    os.replace(temp, target)
    _fsync(target)
    return {"path": str(target), "sha256": _sha256(target), "size": target.stat().st_size, "files": len(files)}


def _archived(detail: dict) -> dict[str, bytes]:
    path = Path(detail["path"])
    if not path.exists() or _sha256(path) != detail["sha256"]:
        raise CutoverAborted(f"the projection archive {path} is missing or does not match its recorded sha256")
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def projection_divergence(root: Path, archived: dict[str, bytes]) -> dict:
    """Projection files on disk vs the archived generation: changed, added, removed paths."""
    base = Path(root) / ".taskmaster"
    disk = {entry["path"]: entry["sha256"] for entry in projection_files(root)}
    then = {name: hashlib.sha256(content).hexdigest() for name, content in archived.items()}
    return {"changed": sorted(p for p in disk.keys() & then.keys() if disk[p] != then[p]),
            "added": sorted(disk.keys() - then.keys()),
            "removed": sorted(then.keys() - disk.keys())} if base.exists() else {}


def _restore_files(root: Path, archived: dict[str, bytes], divergence: dict, saved_db: Path) -> str:
    """Save the divergent files beside the pre-rollback copy, then put the archived generation back."""
    base = Path(root) / ".taskmaster"
    target = saved_db.with_suffix(".projection.zip")
    temp = target.with_name(target.name + ".partial")
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel in divergence["changed"] + divergence["added"]:
            archive.write(base / rel, rel)
        archive.writestr("_pre_rollback_files.json", _encode(divergence))
    os.replace(temp, target)
    _fsync(target)
    for rel in divergence["changed"] + divergence["removed"]:
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(path.name + ".rollback.partial")
        staged.write_bytes(archived[rel])
        os.replace(staged, path)
    for rel in divergence["added"]:
        (base / rel).unlink(missing_ok=True)
    return str(target)


# ── Rollback ─────────────────────────────────────────────────────────────────

def _copy_committed(root: Path) -> str:
    """The pre-rollback copy, taken by a second, read-only connection while the rollback
    connection holds the write lock (BEGIN IMMEDIATE) and has written nothing yet.

    Why the SQLite backup API is safe here: no other connection can commit while that lock
    is held (a WAL checkpoint needs no commit and changes no content), so the committed
    state this reader copies is exactly the state the rollback transaction reads, diffs and
    replaces. (The backup API cannot run from the locking connection itself: it waits on
    that connection's own lock forever.) A store too damaged for the backup API is copied
    raw: the database file and its WAL, fsynced; never the `-shm` index.
    """
    target = _stamped(root, "pre-rollback")
    temp = target.with_name(target.name + ".partial")
    temp.unlink(missing_ok=True)
    try:
        with closing(_connect_readonly(database_path(root))) as reader, closing(sqlite3.connect(temp)) as destination:
            reader.backup(destination)
    except sqlite3.DatabaseError:
        temp.unlink(missing_ok=True)
        source = database_path(root)
        shutil.copyfile(source, temp)
        wal = source.with_name(source.name + "-wal")
        if wal.exists():
            shutil.copyfile(wal, target.with_name(target.name + "-wal"))
            _fsync(target.with_name(target.name + "-wal"))
    os.replace(temp, target)
    _fsync(target)
    return str(target)


_CORRUPT_CODES = {11, 26}  # SQLITE_CORRUPT, SQLITE_NOTADB (primary result codes)


def _integrity_ok(connection) -> bool:
    """False only for real corruption. Locked, I/O, interrupt and other errors propagate:
    they are not evidence that the store may be overwritten without the discard flag."""
    try:
        return [tuple(r) for r in connection.execute("PRAGMA main.integrity_check").fetchall()] == [("ok",)]
    except sqlite3.DatabaseError as error:
        code = getattr(error, "sqlite_errorcode", None)
        if code is not None and code & 0xFF in _CORRUPT_CODES:
            return False
        if code is None and type(error) is sqlite3.DatabaseError and "malformed" in str(error):
            return False
        raise


def _restore_rows(connection) -> None:
    """Replace every legacy domain table's rows (and the `changes` sequence) with the ATTACHed
    backup's, inside the caller's transaction. The staging-stale triggers fire as they should."""
    for table in (*DOMAIN_TABLES, "entity_fts"):
        if not _has(connection, table):
            continue
        connection.execute(f'DELETE FROM main."{table}"')
        if _has(connection, table, "pre"):
            wanted = set(_columns(connection, table, "pre"))
            common = ",".join(f'"{c}"' for c in _columns(connection, table) if c in wanted)
            connection.execute(f'INSERT INTO main."{table}"({common}) SELECT {common} FROM pre."{table}"')
    if _has(connection, "sqlite_sequence"):
        connection.execute("DELETE FROM main.sqlite_sequence WHERE name='changes'")
        if _has(connection, "sqlite_sequence", "pre"):
            connection.execute("INSERT INTO main.sqlite_sequence(name,seq) "
                               "SELECT name,seq FROM pre.sqlite_sequence WHERE name='changes'")


def rollback(root: Path, *, confirm_stopped: bool = False, token: str | None = None,
             discard_writes_since_backup: bool = False, clear_orphan_fence: bool = False,
             log=lambda line: None) -> dict:
    """Before activation commits: drop the fence and journal, restoring from backup if needed.

    One atomic unit on one connection under BEGIN IMMEDIATE: the diff, the pre-rollback copy
    (see `_copy_committed`), the projection-file restore, the row restore from the ATTACHed
    verified backup, marking staging stale, and clearing the fence all happen before a
    single commit, so no write can land between the copy and the restore. A restore never
    silently discards an acknowledged write: differences in `WRITE_TABLES` or in the
    projection files refuse unless `discard_writes_since_backup`. A store failing
    `integrity_check` restores its rows without the flag (after REINDEX), still saved first.
    """
    root = Path(root)
    path = database_path(root)
    try:
        _quiesce()
    except ImportError as error:
        raise CutoverRefused("this Taskmaster build lacks taskmaster.native.quiesce") from error
    if not path.exists():
        raise CutoverRefused(f"no store at {path}")
    with closing(_connect_readonly(path)) as probe:
        state = classify(probe)
        entries = journal(probe)
    if state["native"] or "activate" in _done(entries):
        raise CutoverRefused(ESCAPE_HATCH)
    if not entries and state["migration_state"] == "ready":
        raise CutoverRefused("no cutover fence or journal to roll back")
    if not entries and not clear_orphan_fence:
        raise CutoverRefused(f"migration_state={state['migration_state']!r} but no cutover journal: this fence was "
                             "not published by a cutover run that reached its first commit. If you are sure no "
                             "migration is in progress, re-run with --rollback --clear-orphan-fence "
                             f"(see {RUNBOOK}, 'A fence with no journal')")
    quiesced = check_quiesced(root, confirm_stopped=confirm_stopped)
    if quiesced["refusals"]:
        raise CutoverRefused("; ".join(quiesced["refusals"]))
    if not entries:
        return _clear_orphan_fence(root, path, log)
    fence = next(e for e in entries if e["stage"] == "fence")
    if token is not None and token != fence["token"]:
        raise CutoverRefused("--token does not match the cutover journal's token")
    ownership = _ownership(root)  # Takeover rule: see `cutover`.
    try:
        return _atomic_rollback(root, path, fence, discard_writes_since_backup, log)
    finally:
        ownership.close()


def _atomic_rollback(root: Path, path: Path, fence: dict, discard: bool, log) -> dict:
    with closing(sqlite3.connect(path.resolve().as_uri(), uri=True, isolation_level=None, timeout=30)) as connection:
        connection.execute("PRAGMA busy_timeout=30000")
        backup = _detail(journal(connection), "backup")
        archived = None
        if backup:
            verify_backup(Path(backup["path"]), backup["domain_digest"])
            if backup.get("projection_archive"):
                archived = _archived(backup["projection_archive"])
            # ATTACH is refused inside a transaction; the journal is re-checked under the lock below.
            connection.execute("ATTACH DATABASE ? AS pre", (Path(backup["path"]).resolve().as_uri() + "?mode=ro",))
        connection.execute("BEGIN IMMEDIATE")
        try:
            entries = journal(connection)
            if _detail(entries, "backup").get("path") != backup.get("path") or \
                    not any(e["stage"] == "fence" and e["token"] == fence["token"] for e in entries):
                raise CutoverAborted("the cutover journal changed while the rollback was starting; run it again")
            meta = _meta(connection)
            if meta.get("migration_token") not in (None, fence["token"]):
                raise CutoverAborted("the store carries a different cutover fence; refusing to clear it")
            fence_detail = _detail(entries, "fence")
            accepted = {fence_detail.get("domain_digest"), _detail(entries, "reconcile").get("domain_digest"),
                        backup.get("domain_digest")} - {None}
            healthy = _integrity_ok(connection)
            current = domain_digest(connection) if healthy else None
            restored, saved, saved_files, warnings, differences, divergence = None, None, None, [], {}, {}
            if not healthy and not backup:
                raise CutoverAborted(f"the store fails integrity_check and no backup was taken; see {RUNBOOK}")
            if current not in accepted and not backup:
                # No backup means no backfill either: the cutover itself wrote nothing but the
                # fence, so the difference came from an unfenced (pre-bridge) writer. The store
                # is still a legacy authority; clearing the fence is all that is owed.
                warnings.append("domain state differs from fence time and no backup exists; "
                                "fence cleared without restore (check for unstopped pre-bridge clients)")
            elif current not in accepted:
                if healthy:
                    differences = domain_differences(connection, connection, reference_schema="pre")
                if archived is not None:
                    divergence = projection_divergence(root, archived)
                    if not any(divergence.values()):
                        divergence = {}
                writes = {t: d for t, d in differences.items() if t in WRITE_TABLES}
                if (writes or divergence) and not discard:
                    parts = ([_summarize(writes)] if writes else []) + \
                            ([f"projection files differ from the backup: {_summarize_files(divergence)}"] if divergence else [])
                    raise CutoverRefused(
                        "the store holds writes made after the backup that a restore would discard: "
                        + "; ".join(parts) + ". An unstopped client wrote through the fence. To keep them, stop "
                        "that client and run --resume: the cutover re-takes its backup and carries them into the "
                        "native store. To discard them, re-run with --rollback --discard-writes-since-backup (the "
                        "current store and files are saved as backups/pre-rollback-<ts>.* first)")
                saved = _copy_committed(root)
                _checkpoint("rollback:saved")
                if divergence:
                    saved_files = _restore_files(root, archived, divergence, Path(saved))
                    _checkpoint("rollback:files-restored")
                if not healthy:  # Rebuild main's indexes from table data (never the read-only backup's).
                    for (table,) in connection.execute(
                            "SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                            "AND sql NOT LIKE 'CREATE VIRTUAL%'").fetchall():
                        connection.execute(f'REINDEX main."{table}"')
                _restore_rows(connection)
                if not healthy and not _integrity_ok(connection):
                    raise CutoverAborted(f"the store is damaged beyond a row-level restore; restore it by hand "
                                         f"from {backup['path']} (see {RUNBOOK})")
                restored = backup["path"]
                reason = "the store failed integrity_check" if not healthy else (_summarize(differences) or "-")
                warnings.append(f"restored from backup {restored}; the store as it was is saved as {saved}"
                                + (f", the divergent projection files as {saved_files}" if saved_files else "")
                                + f"; discarded: {reason}"
                                + (f"; files: {_summarize_files(divergence)}" if divergence else ""))
                _checkpoint("rollback:restored")
            for key, value in (fence_detail.get("previous_meta") or {}).items():
                if value is None:
                    connection.execute("DELETE FROM meta WHERE key=?", (key,))
                else:
                    connection.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) "
                                       "DO UPDATE SET value=excluded.value", (key, value))
            if _has(connection, "native_manifest"):
                connection.execute("UPDATE native_manifest SET value='stale' WHERE key='state'")
            _drop_journal(connection)
            _checkpoint("rollback:before-commit")
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        log("rolled back: fence and journal cleared; authority remains legacy")
        for warning in warnings:
            log(f"warning: {warning}")
        return {"ok": True, "mode": "rollback", "restored_from": restored, "pre_rollback_copy": saved,
                "pre_rollback_files": saved_files, "differences": differences, "file_divergence": divergence,
                "warnings": warnings, "domain_digest": domain_digest(connection)}


def _clear_orphan_fence(root: Path, path: Path, log) -> dict:
    """`--rollback --clear-orphan-fence`: a `migrating` fence with no journal (never this
    command's committed state). Only the fence keys are removed, under the ownership lock."""
    marks = ",".join("?" for _ in FENCE_KEYS)
    ownership = _ownership(root)
    try:
        with closing(_connect(path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if _has_table(connection, JOURNAL):
                    raise CutoverRefused("a cutover journal appeared; use --resume or --rollback")
                removed = dict(connection.execute(f"SELECT key,value FROM meta WHERE key IN ({marks})", FENCE_KEYS))
                connection.execute(f"DELETE FROM meta WHERE key IN ({marks})", FENCE_KEYS)
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        warning = f"cleared an orphan fence with no journal: {_encode(removed)}"
        log(f"warning: {warning}")
        return {"ok": True, "mode": "rollback", "restored_from": None, "warnings": [warning], "cleared": removed}
    finally:
        ownership.close()


# ── CLI ──────────────────────────────────────────────────────────────────────

EXIT_OK, EXIT_FENCED, EXIT_REFUSED, EXIT_FAILED_UNFENCED = 0, 1, 2, 3


def fence_state(root: Path) -> dict:
    """Re-read the store after a failure: is a fence up, or is only the release row owed?"""
    path = database_path(root)
    try:
        with closing(_connect_readonly(path)) as connection:
            state = classify(connection)
    except (sqlite3.Error, OSError) as error:
        return {"readable": False, "error": str(error), "fence_up": None, "release_pending": None}
    return {"readable": True, "migration_state": state["migration_state"], "native": state["native"],
            "fence_up": state["migration_state"] == "migrating",
            "release_pending": "activate" in state["completed_stages"] and "release" not in state["completed_stages"],
            "completed_stages": state["completed_stages"]}


def _failure(root: Path, mode: str, error: BaseException, *, refused: bool) -> tuple[dict, int]:
    fence = fence_state(root)
    if not refused and fence.get("readable"):
        finished = (mode in ("run", "resume") and fence["native"] and "release" in fence["completed_stages"]) or \
                   (mode == "rollback" and not fence["fence_up"] and not fence["native"]
                    and not fence["completed_stages"])
        if finished:
            warning = f"the {mode} completed and committed, but a later step failed: {type(error).__name__}: {error}"
            return {"ok": True, "mode": mode, "fence": fence, "warnings": [warning],
                    "hint": "nothing to do; check the warning"}, EXIT_OK
    report = {"ok": False, "mode": mode, "fence": fence}
    if refused:
        report["refusals"] = [str(error)]
    else:
        report["error"] = f"{type(error).__name__}: {error}"
    if fence["fence_up"] is None:
        report["hint"] = f"the store could not be re-read to tell whether a fence is up; see {RUNBOOK}"
        return report, EXIT_FENCED
    if fence["fence_up"]:
        report["hint"] = f"the cutover fence is up; run --resume or --rollback (see {RUNBOOK})"
        return report, (EXIT_REFUSED if refused else EXIT_FENCED)
    if fence["release_pending"]:
        report["hint"] = "activation committed; run --resume to record the release"
        return report, (EXIT_REFUSED if refused else EXIT_FENCED)
    if refused:
        report["hint"] = "nothing was changed; no cutover fence is up"
        return report, EXIT_REFUSED
    report["hint"] = "no cutover fence is up; this run did not change the store's authority; fix the cause and re-run"
    return report, EXIT_FAILED_UNFENCED


def _text(report: dict) -> str:
    status = "OK" if report.get("ok") else ("REFUSED" if report.get("refusals") else "FAILED")
    lines = [f"cutover {report.get('mode', 'run')}: {status}"]
    for key in ("store_state", "counts", "quiesce"):
        if key in report:
            lines.append(f"{key}: {_encode(report[key])}")
    for refusal in report.get("refusals", []):
        lines.append(f"refused: {refusal}")
    for warning in report.get("warnings", []):
        lines.append(f"warning: {warning}")
    for step in report.get("planned", []):
        lines.append(f"would {step}")
    if report.get("completed_stages"):
        lines.append("completed: " + ", ".join(report["completed_stages"]))
    if report.get("restored_from"):
        lines.append(f"restored from: {report['restored_from']} (previous store saved as {report.get('pre_rollback_copy')})")
    if report.get("error"):
        lines.append(f"error: {report['error']}")
    if report.get("hint"):
        lines.append(f"next: {report['hint']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m taskmaster.native.cutover",
                                     description="Cut a legacy Taskmaster store over to the native authority.")
    parser.add_argument("--root", required=True, type=Path, help="project root (the directory holding .taskmaster)")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="run every check and report; write nothing")
    action.add_argument("--resume", action="store_true", help="continue an interrupted cutover from its journal")
    action.add_argument("--rollback", action="store_true", help="undo a cutover that has not activated")
    parser.add_argument("--confirm-stopped", action="store_true",
                        help="proceed past matching processes you have verified are stopped")
    parser.add_argument("--token", help="the cutover token printed at start (optional ownership proof)")
    parser.add_argument("--discard-writes-since-backup", action="store_true",
                        help="with --rollback: restore the backup even though the store holds newer writes "
                             "(the current store is saved as backups/pre-rollback-<ts>.db first)")
    parser.add_argument("--clear-orphan-fence", action="store_true",
                        help="with --rollback: clear a 'migrating' fence that has no cutover journal")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    args = parser.parse_args(argv)
    if (args.discard_writes_since_backup or args.clear_orphan_fence) and not args.rollback:
        parser.error("--discard-writes-since-backup and --clear-orphan-fence require --rollback")
    log = (lambda line: None) if args.json else print
    mode = "dry-run" if args.dry_run else "rollback" if args.rollback else "resume" if args.resume else "run"
    code = EXIT_OK
    try:
        if args.dry_run:
            report = dry_run(args.root, confirm_stopped=args.confirm_stopped)
            code = EXIT_OK if report["ok"] else EXIT_REFUSED
        elif args.rollback:
            report = rollback(args.root, confirm_stopped=args.confirm_stopped, token=args.token,
                              discard_writes_since_backup=args.discard_writes_since_backup,
                              clear_orphan_fence=args.clear_orphan_fence, log=log)
        else:
            report = cutover(args.root, confirm_stopped=args.confirm_stopped, resume=args.resume,
                             token=args.token, log=log)
    except CutoverRefused as error:
        report, code = _failure(args.root, mode, error, refused=True)
    except Exception as error:  # noqa: BLE001 - every failure gets a report and a truthful exit code
        report, code = _failure(args.root, mode, error, refused=False)
    print(_encode(report) if args.json else _text(report))
    return code


if __name__ == "__main__":
    sys.exit(main())
