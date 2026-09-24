# User intent: the one real, operator-run command that turns a legacy Taskmaster store into
# a native authority (N15): fenced, backed up, crash-safe and resumable, exactly reversible
# before activation commits and roll-forward-only after it (decision M1 = A).
"""Legacy -> native cutover: `python -m taskmaster.native.cutover --root <project>`.

Stages, in order, each idempotent and recorded in `native_cutover_journal`:

    fence      publish meta.migration_state='migrating' + owner/token (BEGIN IMMEDIATE,
               coordinator ownership lock held for the whole run)
    reconcile  reconcile the pending changelog; refuse unexported projection work
    backup     sqlite3 online backup + JSON manifest, verified by reopening it
    backfill   `migrate.backfill` with its checkpoints wired into the journal
    compare    `migrate.verify_carryover` must report nothing lost
    activate   one transaction: markers, authority, graph repair, ID import, ready
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


MIGRATE_PRIMITIVES = ("snapshot_carryover", "verify_carryover", "import_id_state", "reconcile_progress",
                      "backfill", "repair_graph_for_activation")
QUIESCE_PRIMITIVES = ("live_owner", "open_writers", "scan_processes")


def missing_primitives() -> list[str]:
    """Primitives this command needs that the installed code lacks; none may be missing
    before a fence goes up."""
    missing = [f"migrate.{name}" for name in MIGRATE_PRIMITIVES if not hasattr(_migrate(), name)]
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


def journal(connection) -> list[dict]:
    if not _has_table(connection, JOURNAL):
        return []
    return [dict(seq=r[0], stage=r[1], status=r[2], owner=r[3], token=r[4], at=r[5], detail=json.loads(r[6] or "{}"))
            for r in connection.execute(f"SELECT seq,stage,status,owner,token,at,detail_json FROM {JOURNAL} ORDER BY seq")]


def _done(entries) -> list[str]:
    return [e["stage"] for e in entries if e["status"] == "done" and e["stage"] in STAGES]


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


def _flush_hint(reasons) -> str:
    return ("Refusing cutover: " + "; ".join(reasons) + ". Flush them first: run any Taskmaster tool call "
            "(e.g. backlog_status) through a bridge client so the legacy store drains its exports, stop it "
            "again, then retry.")


# ── Preconditions ────────────────────────────────────────────────────────────

def _process_label(process: dict) -> str:
    parts = [f"pid {process.get('pid', '?')}"]
    for key in ("name", "surface", "cmdline", "command"):
        if process.get(key):
            parts.append(str(process[key])[:160])
    return " ".join(parts)


def check_quiesced(root: Path, *, confirm_stopped: bool) -> dict:
    """Refuse live owners and open writers; name matching processes unless confirmed stopped."""
    quiesce = _quiesce()
    owner = quiesce.live_owner(Path(root))
    writers = quiesce.open_writers(database_path(root))
    processes = list(quiesce.scan_processes(Path(root)) or [])
    report = {"live_owner": owner, "open_writers": writers, "processes": processes,
              "confirm_stopped": confirm_stopped, "refusals": [], "warnings": []}
    if owner:
        report["refusals"].append(f"a live coordinator or service owns this project: {_encode(owner)}; stop it first")
    if writers is True:
        report["refusals"].append("the store has another open writer; stop every Taskmaster client first")
    elif writers is None:
        report["warnings"].append("open-writer probe was inconclusive")
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
    return []


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


def _backup_target(root: Path) -> Path:
    directory = database_path(root).parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target, n = directory / f"pre-native-{stamp}.db", 1
    while target.exists() or target.with_suffix(".json").exists():
        target, n = directory / f"pre-native-{stamp}-{n}.db", n + 1
    return target


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
    live = domain_digest(connection)
    backup_digest = verify_backup(target, live)
    sidecar = database_path(root).parent / "id-reservations.json"
    sidecar_copy = None
    if sidecar.exists():
        sidecar_copy = target.with_name(target.stem + ".id-reservations.json")
        shutil.copyfile(sidecar, sidecar_copy)
    manifest = {
        "created_at": _now(), "store": str(database_path(root)), "backup": str(target),
        "backup_sha256": _sha256(target), "domain_digest": backup_digest,
        "projection_files": projection_files(root),
        "id_reservations": None if sidecar_copy is None else {
            "source": str(sidecar), "copy": str(sidecar_copy), "sha256": _sha256(sidecar_copy)},
        "carryover": carryover,
    }
    manifest_path = target.with_suffix(".json")
    manifest_path.write_text(_encode(manifest), encoding="utf-8")
    return {"path": str(target), "manifest": str(manifest_path), "domain_digest": backup_digest,
            "sha256": manifest["backup_sha256"], "projection_files": len(manifest["projection_files"])}


# ── Activation core (production; the twins fixture flips authority through it) ──

def activate(connection, root: Path, *, token: str | None = None, import_ids=None, record=None) -> dict:
    """One transaction: publish native markers and authority, repair the graph, import ID
    state, and (for a fenced cutover) return `migration_state` to `ready`.

    `token=None` is the unfenced fast path for test fixtures; it requires an unfenced store.
    `import_ids(connection, root)` defaults to `migrate.import_id_state`.
    `record(connection)` runs inside the transaction, before commit (the journal row).
    """
    migrate = _migrate()
    from .db import manifest
    if connection.in_transaction:
        raise RuntimeError("activation requires its own transaction")
    import_ids = import_ids or migrate.import_id_state
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
        import_ids(connection, Path(root))
        if record is not None:
            record(connection)
        _checkpoint("activate:before-commit")
        connection.commit()
        return {"graph_repair": repair}
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
        with migration_owner(self.token):
            _migrate().reconcile_progress(self.connection)
        if self.connection.in_transaction:
            raise CutoverAborted("reconcile_progress left a transaction open")
        self._begin_owned()
        counts = reconcile_counts(self.connection, self.root)
        blocking = _blocking(counts)
        if blocking:
            self.connection.rollback()
            raise CutoverAborted(_flush_hint(blocking) + " The fence is up: run --rollback, flush, and start again.")
        self._commit_stage("reconcile", {"counts": counts, "domain_digest": domain_digest(self.connection)})

    def backup(self):
        with migration_owner(self.token):
            carryover = _migrate().snapshot_carryover(self.connection)
        if self.connection.in_transaction:
            raise CutoverAborted("snapshot_carryover left a transaction open")
        result = write_backup(self.connection, self.root, carryover)
        self._begin_owned()
        if domain_digest(self.connection) != result["domain_digest"]:
            self.connection.rollback()
            raise CutoverAborted("the store changed while it was being backed up")
        self._commit_stage("backup", result)

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

    def compare(self):
        entries = journal(self.connection)
        manifest_path = Path(_detail(entries, "backup")["manifest"])
        before = json.loads(manifest_path.read_text(encoding="utf-8"))["carryover"]
        with migration_owner(self.token):
            problems = list(_migrate().verify_carryover(self.connection, before))
        if self.connection.in_transaction:
            raise CutoverAborted("verify_carryover left a transaction open")
        self._begin_owned()
        staging = _native_manifest(self.connection).get("state")
        if staging != "verified":
            problems.append(f"native staging is {staging!r} after backfill, not verified")
        if problems:
            self.connection.rollback()
            raise CutoverAborted("carryover comparison failed; activation aborted: " + "; ".join(problems))
        self._commit_stage("compare", {"problems": []})

    def activate(self):
        detail = {}

        def record(connection):
            _record(connection, "activate", "done", self.owner, self.token, detail)
        result = activate(self.connection, self.root, token=self.token, record=record)
        self.report["stages"]["activate"] = result
        self.log(f"[activate] done: authority=native; graph repair {result['graph_repair']}")
        _checkpoint("activate:after-commit")

    def release(self):
        self.connection.execute("BEGIN IMMEDIATE")
        self._commit_stage("release", {})

    def run_from(self, done):
        for stage in STAGES:
            if stage in done:
                continue
            _checkpoint(f"{stage}:begin")
            try:
                getattr(self, stage)()
            except BaseException as error:
                if stage not in ("fence",) and not isinstance(error, (KeyboardInterrupt, SystemExit)):
                    self._fail(stage, error)
                raise


def _ownership(root: Path):
    from taskmaster.coordinator.ownership import Ownership, OwnershipUnavailable
    try:
        return Ownership(Path(root)).acquire()
    except OwnershipUnavailable as error:
        raise CutoverRefused("the coordinator ownership lock is held by a live process; stop it first") from error


def dry_run(root: Path, *, confirm_stopped: bool = False) -> dict:
    """Every check, the counts and the planned actions; opens the store read-only."""
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
                try:
                    assert_compatible(connection)
                except UnsupportedStoreError as error:
                    report["refusals"].append(str(error))
                report["carryover"] = _migrate().snapshot_carryover(connection)
            report["domain_digest"] = domain_digest(connection)
        finally:
            connection.rollback()
    quiesced = check_quiesced(root, confirm_stopped=confirm_stopped)
    report["quiesce"] = quiesced
    report["refusals"] += quiesced["refusals"]
    report["warnings"] += quiesced["warnings"]
    report["projection_files"] = len(projection_files(root))
    report["planned"] = [] if report["refusals"] else [
        "fence: publish meta.migration_state='migrating' with owner/token under the ownership lock",
        "reconcile: reconcile the pending changelog; re-check unexported projection work",
        f"backup: {path.parent / 'backups' / 'pre-native-<UTC ts>.db'} + manifest ({report['projection_files']} projection files)",
        f"backfill: stage {counts['entities']} entities and {counts['changes']} changes into native tables",
        "compare: verify_carryover must report nothing lost",
        "activate: schema/protocol markers, authority=native, graph repair, ID import, migration_state=ready",
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
    refusals = _store_refusals(state, mode=mode)
    if not resume and not refusals and _blocking(counts):
        refusals.append(_flush_hint(_blocking(counts)))
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


def rollback(root: Path, *, confirm_stopped: bool = False, token: str | None = None, log=lambda line: None) -> dict:
    """Before activation commits: drop the fence and journal, restoring from backup if damaged."""
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
    if not entries:
        raise CutoverRefused(f"migration_state={state['migration_state']!r} but no cutover journal; "
                             f"see {RUNBOOK} before touching this store")
    quiesced = check_quiesced(root, confirm_stopped=confirm_stopped)
    if quiesced["refusals"]:
        raise CutoverRefused("; ".join(quiesced["refusals"]))
    fence = next(e for e in entries if e["stage"] == "fence")
    if token is not None and token != fence["token"]:
        raise CutoverRefused("--token does not match the cutover journal's token")
    ownership = _ownership(root)  # Takeover rule: see `cutover`.
    try:
        with closing(_connect(path)) as connection:
            entries = journal(connection)
            fence_detail, backup = _detail(entries, "fence"), _detail(entries, "backup")
            accepted = {fence_detail.get("domain_digest"), _detail(entries, "reconcile").get("domain_digest"),
                        backup.get("domain_digest")} - {None}
            integrity = [tuple(r) for r in connection.execute("PRAGMA integrity_check").fetchall()]
            current = domain_digest(connection) if integrity == [("ok",)] else None
            restored, warnings = None, []
            if current is None and not backup:
                raise CutoverAborted(f"the store fails integrity_check and no backup was taken; see {RUNBOOK}")
            if current not in accepted and not backup:
                # No backup means no backfill either: the cutover itself wrote nothing but the
                # fence, so the difference came from reconcile or an unfenced (pre-bridge) writer.
                # The store is still a legacy authority; clearing the fence is all that is owed.
                warnings.append("domain state differs from fence time and no backup exists; "
                                "fence cleared without restore (check for unstopped pre-bridge clients)")
            elif current not in accepted:
                source = Path(backup["path"])
                verify_backup(source, backup["domain_digest"])
                with closing(_connect_readonly(source)) as reader:
                    reader.backup(connection)
                restored = str(source)
                log(f"restored domain state from backup {source}")
            _checkpoint("rollback:restored")
            connection.execute("BEGIN IMMEDIATE")
            try:
                meta = _meta(connection)
                if meta.get("migration_token") not in (None, fence["token"]):
                    raise CutoverAborted("the store carries a different cutover fence; refusing to clear it")
                for key, value in (fence_detail.get("previous_meta") or {}).items():
                    if value is None:
                        connection.execute("DELETE FROM meta WHERE key=?", (key,))
                    else:
                        connection.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) "
                                           "DO UPDATE SET value=excluded.value", (key, value))
                if _has_table(connection, "native_manifest"):
                    connection.execute("UPDATE native_manifest SET value='stale' WHERE key='state'")
                connection.execute(f"DROP TABLE {JOURNAL}")
                _checkpoint("rollback:before-commit")
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
            log("rolled back: fence and journal cleared; authority remains legacy")
            for warning in warnings:
                log(f"warning: {warning}")
            return {"ok": True, "mode": "rollback", "restored_from": restored, "warnings": warnings,
                    "domain_digest": domain_digest(connection)}
    finally:
        ownership.close()


# ── CLI ──────────────────────────────────────────────────────────────────────

def _text(report: dict) -> str:
    lines = [f"cutover {report.get('mode', 'run')}: {'OK' if report.get('ok') else 'REFUSED'}"]
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
        lines.append(f"restored from: {report['restored_from']}")
    if report.get("error"):
        lines.append(f"error: {report['error']}")
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
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    args = parser.parse_args(argv)
    lines = []
    log = (lambda line: None) if args.json else (lambda line: (print(line), lines.append(line)))
    code = 0
    try:
        if args.dry_run:
            report = dry_run(args.root, confirm_stopped=args.confirm_stopped)
            code = 0 if report["ok"] else 2
        elif args.rollback:
            report = rollback(args.root, confirm_stopped=args.confirm_stopped, token=args.token, log=log)
        else:
            report = cutover(args.root, confirm_stopped=args.confirm_stopped, resume=args.resume,
                             token=args.token, log=log)
    except CutoverRefused as error:
        report, code = {"ok": False, "mode": "refused", "refusals": [str(error)]}, 2
    except (CutoverAborted, UnsupportedStoreError) as error:
        report, code = {"ok": False, "mode": "aborted", "error": str(error),
                        "hint": f"the fence stays up; run --resume or --rollback (see {RUNBOOK})"}, 1
    print(_encode(report) if args.json else _text(report))
    return code


if __name__ == "__main__":
    sys.exit(main())
