# User intent: prove the N15 cutover command fences, checks, backs up, activates through the
# production core, refuses what it must, and rolls back exactly before activation (M1 = A).
"""Behaviour of `taskmaster.native.cutover` on disposable legacy projects."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster.admission import UnsupportedStoreError, assert_compatible, migration_owner
from taskmaster.native import cutover
from taskmaster.native.db import assert_native
from tests import cutover_stubs
from tests.native_twins import activate_native, committed, point_server_at, scaffold


def build_project(root: Path, monkeypatch) -> Path:
    scaffold(root)
    point_server_at(monkeypatch, root)
    bs.backlog_add_epic(epic_id="cut-epic", name="Cutover Epic", done_when="done")
    bs.backlog_add_phase(phase_id="dev", name="Development")
    bs.backlog_add_task(title="First task", epic="cut-epic", phase="dev")
    bs.backlog_add_task(title="Second task", epic="cut-epic", phase="dev", depends_on="cut-epic-001")
    bs.backlog_bug_create(title="A bug")
    store.reset_for_tests()
    (root / ".taskmaster" / "local" / "id-reservations.json").write_text('{"bug": ["B-9"]}\n', encoding="utf-8")
    return root


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = build_project(tmp_path / "proj", monkeypatch)
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def quiesce(monkeypatch):
    module = cutover_stubs.install(monkeypatch)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    return module


def db(root):
    return cutover.database_path(root)


def legacy_state(root):
    with closing(sqlite3.connect(db(root))) as connection:
        tables = ("entities", "changes", "projection", "projection_base", "linear_queue", "sessions")
        state = {t: sorted(tuple(r) for r in connection.execute(f"SELECT * FROM {t}")) for t in tables}
        state["meta"] = sorted(tuple(r) for r in connection.execute("SELECT * FROM meta"))
        return state


def tree_hash(root):
    """Every file's bytes. A read-only SQLite open of a WAL database materializes an empty
    `-wal` and the `-shm` shared-memory index; neither holds data, so they are skipped."""
    digest = hashlib.sha256()
    for path in sorted((root / ".taskmaster").rglob("*")):
        if path.name.endswith("-shm") or (path.name.endswith("-wal") and path.stat().st_size == 0):
            continue
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


class Injected(RuntimeError):
    pass


def crash_at(monkeypatch, point):
    def hook(name):
        if name == point:
            raise Injected(point)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", hook)


# ── Happy path ───────────────────────────────────────────────────────────────

def test_cutover_activates_like_the_fixture_activation(project, quiesce, tmp_path):
    twin = tmp_path / "twin"
    shutil.copytree(project, twin)
    before = committed(project)
    report = cutover.cutover(project)
    assert report["ok"] and report["completed_stages"] == list(cutover.STAGES)
    activate_native(twin)
    assert committed(project) == committed(twin) == before
    with closing(sqlite3.connect(db(project))) as connection, closing(sqlite3.connect(db(twin))) as other:
        assert_native(connection)
        meta = dict(connection.execute("SELECT key,value FROM meta"))
        assert meta["migration_state"] == "ready"
        assert "migration_token" not in meta and "migration_owner" not in meta
        counters = "SELECT * FROM id_counters ORDER BY kind"
        assert connection.execute(counters).fetchall() == other.execute(counters).fetchall()
        assert ("bug", "B-9") in connection.execute("SELECT kind,public_id FROM id_reservations").fetchall()
        stages = [s for s, st in connection.execute(f"SELECT stage,status FROM {cutover.JOURNAL} ORDER BY seq")
                  if st == "done" and s in cutover.STAGES]
        assert stages == list(cutover.STAGES)
        with pytest.raises(UnsupportedStoreError):
            assert_compatible(connection)  # Bridge (legacy-only) clients are refused after activation.


def test_backup_is_consistent_verified_and_manifested(project, quiesce):
    report = cutover.cutover(project)
    backup = report["stages"]["backup"]
    path = Path(backup["path"])
    assert path.parent == db(project).parent / "backups" and path.name.startswith("pre-native-")
    manifest = json.loads(Path(backup["manifest"]).read_text(encoding="utf-8"))
    assert manifest["domain_digest"] == backup["domain_digest"] == cutover.verify_backup(path)
    assert {"path", "sha256", "size"} <= set(manifest["projection_files"][0])
    assert any(f["path"] == "backlog.yaml" for f in manifest["projection_files"])
    assert Path(manifest["id_reservations"]["copy"]).read_text(encoding="utf-8") == '{"bug": ["B-9"]}\n'
    assert set(manifest["carryover"]) >= {"linear_queue", "projection"}
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
        assert connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0] > 0


# ── Fence ────────────────────────────────────────────────────────────────────

def test_fence_refuses_bridge_clients_but_admits_its_owner(project, quiesce, monkeypatch):
    crash_at(monkeypatch, "backup:begin")
    with pytest.raises(Injected):
        cutover.cutover(project)
    with closing(sqlite3.connect(db(project))) as connection:
        meta = dict(connection.execute("SELECT key,value FROM meta"))
        assert meta["migration_state"] == "migrating" and meta["migration_owner"].startswith("cutover:")
        with pytest.raises(UnsupportedStoreError, match="migrating"):
            assert_compatible(connection)
        with migration_owner("not-the-token"), pytest.raises(UnsupportedStoreError):
            assert_compatible(connection)
        with migration_owner(meta["migration_token"]):
            assert_compatible(connection)
    # An ordinary store client is refused too, and does not clear the fence.
    point_server_at(monkeypatch, project)
    with pytest.raises(Exception):
        bs.backlog_add_task(title="during fence", epic="cut-epic", phase="dev")
    with closing(sqlite3.connect(db(project))) as connection:
        assert connection.execute("SELECT value FROM meta WHERE key='migration_state'").fetchone()[0] == "migrating"
    with pytest.raises(cutover.CutoverRefused, match="--resume or --rollback"):
        cutover.cutover(project)


# ── Preconditions: refusals write nothing ────────────────────────────────────

@pytest.mark.parametrize("setup,match", [
    (lambda q, root: setattr(q, "live_owner", lambda r: {"pid": 4242, "kind": "service"}), "live coordinator"),
    (lambda q, root: setattr(q, "open_writers", lambda p: True), "open writer"),
    (lambda q, root: setattr(q, "scan_processes", lambda r, **k: [{"pid": 77, "name": "python.exe backlog_server.py"}]),
     r"pid 77 python.exe backlog_server.py"),
])
def test_quiesce_refusals_write_nothing(project, quiesce, setup, match):
    setup(quiesce, project)
    before = tree_hash(project)
    with pytest.raises(cutover.CutoverRefused, match=match):
        cutover.cutover(project)
    assert tree_hash(project) == before


def test_confirm_stopped_proceeds_past_named_processes(project, quiesce):
    quiesce.scan_processes = lambda r, **k: [{"pid": 77, "name": "viewer"}, {"pid": 78, "name": "hook"}]
    with pytest.raises(cutover.CutoverRefused) as refused:
        cutover.cutover(project)
    assert "pid 77 viewer" in str(refused.value) and "pid 78 hook" in str(refused.value)
    lines = []
    assert cutover.cutover(project, confirm_stopped=True, log=lines.append)["ok"]
    assert any("pid 77 viewer" in line for line in lines)


def test_held_ownership_lock_refuses(project, quiesce):
    from taskmaster.coordinator.ownership import Ownership
    with Ownership(project):
        with pytest.raises(cutover.CutoverRefused, match="ownership lock"):
            cutover.cutover(project)
    with closing(sqlite3.connect(db(project))) as connection:
        assert connection.execute("SELECT value FROM meta WHERE key='migration_state'").fetchone() is None


def test_already_native_and_newer_schema_are_refused(project, quiesce, tmp_path):
    newer = tmp_path / "newer"
    shutil.copytree(project, newer)
    cutover.cutover(project)
    with pytest.raises(cutover.CutoverRefused, match="already a native authority"):
        cutover.cutover(project)
    with closing(sqlite3.connect(db(newer), isolation_level=None)) as connection:
        connection.execute("UPDATE meta SET value='7' WHERE key='schema_version'")
    before = tree_hash(newer)
    with pytest.raises(cutover.CutoverRefused, match="newer"):
        cutover.cutover(newer)
    assert tree_hash(newer) == before


def test_unexported_projection_work_is_refused_with_counts(project, quiesce):
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("UPDATE projection SET dirty=1 WHERE file=(SELECT MIN(file) FROM projection)")
    (db(project).parent / "export-intent.peer.json").write_text('{"entries": {"x": {}}}', encoding="utf-8")
    report = cutover.dry_run(project)
    assert report["counts"]["dirty_unexported"] == 1 and report["counts"]["export_intents"] == 1
    assert any("1 dirty projection row" in r and "export-intent" in r for r in report["refusals"])
    before = tree_hash(project)
    with pytest.raises(cutover.CutoverRefused, match="1 dirty projection row"):
        cutover.cutover(project)
    assert tree_hash(project) == before


def test_unexported_work_appearing_under_the_fence_aborts_reconcile(project, quiesce, monkeypatch):
    before = legacy_state(project)

    def dirty(name):
        if name == "reconcile:begin":
            with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
                connection.execute("UPDATE projection SET dirty=1 WHERE file=(SELECT MIN(file) FROM projection)")
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", dirty)
    with pytest.raises(cutover.CutoverAborted, match="dirty projection"):
        cutover.cutover(project)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    report = cutover.rollback(project)  # No backup yet: the fence is cleared, the change is reported.
    assert report["ok"] and report["restored_from"] is None and report["warnings"]
    after = legacy_state(project)
    assert after["meta"] == before["meta"] and after["entities"] == before["entities"]


# ── Compare ──────────────────────────────────────────────────────────────────

def test_carryover_loss_aborts_before_activation(project, quiesce, monkeypatch):
    from taskmaster.native import carryover
    monkeypatch.setattr(carryover, "verify_carryover", lambda connection, before: ["linear_queue row 9 lost"])
    before = legacy_state(project)
    with pytest.raises(cutover.CutoverAborted, match="linear_queue row 9 lost"):
        cutover.cutover(project)
    with closing(sqlite3.connect(db(project))) as connection:
        assert connection.execute("SELECT value FROM native_manifest WHERE key='authority'").fetchone()[0] == "legacy"
        assert connection.execute(f"SELECT stage,status FROM {cutover.JOURNAL} ORDER BY seq DESC LIMIT 1").fetchone() \
            == ("compare", "failed")
    assert cutover.rollback(project)["ok"]
    assert legacy_state(project) == before


# ── Rollback ─────────────────────────────────────────────────────────────────

def test_rollback_after_activation_names_the_escape_hatch(project, quiesce):
    cutover.cutover(project)
    with pytest.raises(cutover.CutoverRefused) as refused:
        cutover.rollback(project)
    assert "escape hatch" in str(refused.value).lower() and cutover.RUNBOOK in str(refused.value)


def test_rollback_restores_damaged_staging_from_backup(project, quiesce, monkeypatch):
    before = legacy_state(project)
    crash_at(monkeypatch, "compare:begin")
    with pytest.raises(Injected):
        cutover.cutover(project)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("DELETE FROM entities WHERE kind='bug'")  # Damage after the backup.
    report = cutover.rollback(project)
    assert report["restored_from"] and Path(report["restored_from"]).name.startswith("pre-native-")
    assert legacy_state(project) == before
    with closing(sqlite3.connect(db(project))) as connection:
        assert_compatible(connection)
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name=?", (cutover.JOURNAL,)).fetchone()


def test_rollback_with_nothing_to_undo_is_refused(project, quiesce):
    with pytest.raises(cutover.CutoverRefused, match="nothing|no cutover"):
        cutover.rollback(project)


# ── Stale fence ownership ────────────────────────────────────────────────────

def test_stale_fence_needs_ownership_and_matching_token(project, quiesce, monkeypatch):
    from taskmaster.coordinator.ownership import Ownership
    crash_at(monkeypatch, "backfill:begin")
    with pytest.raises(Injected):
        cutover.cutover(project)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    with Ownership(project):  # A live holder: neither resume nor rollback may take over.
        with pytest.raises(cutover.CutoverRefused, match="ownership lock"):
            cutover.cutover(project, resume=True)
        with pytest.raises(cutover.CutoverRefused, match="ownership lock"):
            cutover.rollback(project)
    with pytest.raises(cutover.CutoverRefused, match="token"):
        cutover.cutover(project, resume=True, token="wrong")
    with pytest.raises(cutover.CutoverRefused, match="token"):
        cutover.rollback(project, token="wrong")
    with closing(sqlite3.connect(db(project))) as connection:
        token = connection.execute("SELECT value FROM meta WHERE key='migration_token'").fetchone()[0]
    report = cutover.cutover(project, resume=True, token=token)
    assert report["ok"] and report["token"] == token
    with closing(sqlite3.connect(db(project))) as connection:
        takeovers = connection.execute(f"SELECT detail_json FROM {cutover.JOURNAL} WHERE stage='takeover'").fetchall()
        assert len(takeovers) == 1 and json.loads(takeovers[0][0])["resume_after"] == "backup"


def test_forged_fence_without_journal_is_not_cleared(project, quiesce):
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("INSERT INTO meta VALUES('migration_state','migrating')")
    with pytest.raises(cutover.CutoverRefused, match="no cutover journal"):
        cutover.rollback(project)
    with pytest.raises(cutover.CutoverRefused, match="no cutover journal"):
        cutover.cutover(project, resume=True)


# ── Dry run ──────────────────────────────────────────────────────────────────

def test_dry_run_reports_and_writes_nothing(project, quiesce, capsys):
    before = tree_hash(project)
    assert cutover.main(["--root", str(project), "--dry-run", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["counts"]["entities"] >= 5 and len(report["planned"]) == len(cutover.STAGES)
    assert report["store_state"]["authority"] == "legacy" and "carryover" in report
    assert cutover.main(["--root", str(project), "--dry-run"]) == 0
    text = capsys.readouterr().out
    assert "would fence" in text and "would activate" in text
    quiesce.scan_processes = lambda root, **k: [{"pid": 5, "name": "codex"}]
    assert cutover.main(["--root", str(project), "--dry-run", "--json"]) == 2
    assert "pid 5 codex" in capsys.readouterr().out
    assert tree_hash(project) == before


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_cli_runs_and_refuses_with_exit_codes(project, quiesce, capsys):
    assert cutover.main(["--root", str(project), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["completed_stages"] == list(cutover.STAGES)
    assert cutover.main(["--root", str(project), "--rollback"]) == 2
    assert "escape hatch" in capsys.readouterr().out.lower()
    assert cutover.main(["--root", str(project)]) == 2


def test_missing_primitives_refuse_before_any_write(project, quiesce, monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "taskmaster.native.quiesce")
    monkeypatch.setattr(cutover, "_quiesce", lambda: (_ for _ in ()).throw(ImportError("absent")))
    before = tree_hash(project)
    with pytest.raises(cutover.CutoverRefused, match="lacks cutover primitives: .*taskmaster.native.quiesce"):
        cutover.cutover(project)
    report = cutover.dry_run(project)
    assert not report["ok"] and "taskmaster.native.quiesce" in report["refusals"][0]
    assert tree_hash(project) == before


def test_carryover_loss_found_only_at_activation_rolls_the_switch_back(project, quiesce, monkeypatch):
    from taskmaster.native import carryover
    real, calls = carryover.verify_carryover, []

    def second_call_fails(connection, before):
        calls.append(1)
        return real(connection, before) if len(calls) == 1 else ["sessions row lost"]
    monkeypatch.setattr(carryover, "verify_carryover", second_call_fails)
    with pytest.raises(cutover.CutoverAborted, match="sessions row lost"):
        cutover.cutover(project)
    with closing(sqlite3.connect(db(project))) as connection:
        assert connection.execute("SELECT value FROM native_manifest WHERE key='authority'").fetchone()[0] == "legacy"
        assert connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
        assert connection.execute("SELECT COUNT(*) FROM id_counters").fetchone()[0] == 0
    assert cutover.rollback(project)["ok"]


def test_malformed_reservation_sidecar_is_refused_before_any_write(project, quiesce):
    (db(project).parent / "id-reservations.json").write_text('{"bug": "B-9"}', encoding="utf-8")
    before = tree_hash(project)
    with pytest.raises(cutover.CutoverRefused, match="malformed ID reservation sidecar"):
        cutover.cutover(project)
    report = cutover.dry_run(project)
    assert any("malformed ID reservation sidecar" in r for r in report["refusals"])
    assert tree_hash(project) == before


def test_scan_note_is_reported_as_a_warning(project, quiesce):
    quiesce.scan_processes = lambda root, **k: quiesce.ScanResult([], "PowerShell not found; process scan skipped")
    report = cutover.dry_run(project)
    assert report["ok"] and any("PowerShell not found" in w for w in report["warnings"])


def test_real_owner_and_writer_probes_refuse(project, monkeypatch):
    """The real `live_owner`/`open_writers`; only the machine-wide process scan is quiet."""
    import sys
    from taskmaster.coordinator.ownership import Ownership
    from taskmaster.native import quiesce as real
    monkeypatch.setitem(sys.modules, "taskmaster.native.quiesce", real)
    monkeypatch.setattr(real, "scan_processes", lambda root, **k: real.ScanResult([]))
    with closing(sqlite3.connect(db(project))) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with Ownership(project):
        with pytest.raises(cutover.CutoverRefused, match="live coordinator"):
            cutover.cutover(project)
    holder = sqlite3.connect(db(project))
    try:
        holder.execute("SELECT COUNT(*) FROM entities").fetchone()
        with pytest.raises(cutover.CutoverRefused, match="open writer"):
            cutover.cutover(project)
    finally:
        holder.close()
    assert cutover.cutover(project)["ok"]  # Our own probe connections are closed before probing.
