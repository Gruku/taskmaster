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


def test_unexported_projection_work_is_flushed_by_the_cutover_itself(project, quiesce):
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        first = connection.execute("SELECT MIN(file) FROM projection").fetchone()[0]
        connection.execute("UPDATE projection SET dirty=1 WHERE file=?", (first,))
    (db(project).parent / "export-intent.peer.json").write_text('{"entries": {"x": {}}}', encoding="utf-8")
    report = cutover.dry_run(project)
    assert report["counts"]["dirty_unexported"] == 1 and report["counts"]["export_intents"] == 1
    assert report["ok"] and any("flushes them itself" in w for w in report["warnings"])
    result = cutover.cutover(project)
    assert result["ok"] and result["stages"]["reconcile"]["flushed"]
    assert result["stages"]["reconcile"]["carried"] == []
    with closing(sqlite3.connect(db(project))) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projection WHERE dirty=1").fetchone()[0] == 0
    assert not list(db(project).parent.glob("export-intent.*.json"))


def test_unexported_work_appearing_under_the_fence_is_flushed_too(project, quiesce, monkeypatch):
    def dirty(name):
        if name == "reconcile:begin":
            with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
                connection.execute("UPDATE projection SET dirty=1 WHERE file=(SELECT MIN(file) FROM projection)")
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", dirty)
    assert cutover.cutover(project)["ok"]
    with closing(sqlite3.connect(db(project))) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projection WHERE dirty=1").fetchone()[0] == 0


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
    with pytest.raises(cutover.CutoverRefused, match="no cutover journal.*--clear-orphan-fence"):
        cutover.rollback(project)
    with pytest.raises(cutover.CutoverRefused, match="no cutover journal"):
        cutover.cutover(project, resume=True)


def test_orphan_fence_is_cleared_only_on_request(project, quiesce, capsys):
    before = legacy_state(project)
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.executemany("INSERT INTO meta VALUES(?,?)", [("migration_state", "migrating"),
                                                               ("migration_owner", "someone")])
    assert cutover.main(["--root", str(project), "--rollback", "--clear-orphan-fence", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["cleared"] == {"migration_state": "migrating", "migration_owner": "someone"}
    assert legacy_state(project) == before
    assert cutover.cutover(project)["ok"]


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


# ── Review fixes (N15 adversarial review, probes P1-P7) ─────────────────────

def test_p2_rollback_refuses_a_write_acknowledged_after_backup_and_resume_keeps_it(project, quiesce, monkeypatch):
    crash_at(monkeypatch, "compare:begin")
    with pytest.raises(Injected):
        cutover.cutover(project)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:  # A pre-bridge writer.
        connection.execute("UPDATE entities SET body=COALESCE(body,'')||' ACKED' WHERE kind='bug'")
    with pytest.raises(cutover.CutoverRefused, match=r"entities: 0 added, 0 removed, 1 changed.*bug"):
        cutover.rollback(project)
    backups = sorted((db(project).parent / "backups").glob("pre-rollback-*"))
    assert backups == []  # A refusal restores nothing, so nothing needed saving.
    # The write survives, and the operator can keep it by rolling forward.
    report = cutover.cutover(project, resume=True)
    assert report["ok"]
    assert any(body and body.endswith(" ACKED") for (kind, _), (_, body, _) in committed(project).items()
               if kind == "bug")


def _break_index(path, table="entities", index="ix_probe_entities_status", columns=("status", "id")):
    """Index-only damage: rows intact, the index disagrees with them ("row N missing from index")."""
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.execute(f"CREATE INDEX IF NOT EXISTS {index} ON {table}({columns[0]})")
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(f"UPDATE sqlite_schema SET sql='CREATE INDEX {index} ON {table}({columns[1]})' "
                           f"WHERE name='{index}'")
        connection.execute("PRAGMA writable_schema=OFF")
    with closing(sqlite3.connect(path)) as connection:
        assert "missing from index" in connection.execute("PRAGMA integrity_check").fetchone()[0]


def test_p3_resume_survives_a_lost_manifest(project, quiesce, monkeypatch):
    crash_at(monkeypatch, "backfill:begin")
    with pytest.raises(Injected):
        cutover.cutover(project)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    manifest = next(p for p in (db(project).parent / "backups").glob("pre-native-*.json")
                    if not p.name.endswith("id-reservations.json"))
    manifest.write_text("", encoding="utf-8")  # Power loss before the manifest reached disk.
    assert cutover.cutover(project, resume=True)["ok"]


def test_resume_names_an_unreadable_manifest_when_the_journal_lacks_the_snapshot(project, quiesce, monkeypatch):
    crash_at(monkeypatch, "backfill:begin")
    with pytest.raises(Injected):
        cutover.cutover(project)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:  # A pre-fix journal row.
        connection.execute(f"UPDATE {cutover.JOURNAL} SET detail_json=json_remove(detail_json,'$.carryover') "
                           "WHERE stage='backup'")
    manifest = next(p for p in (db(project).parent / "backups").glob("pre-native-*.json")
                    if not p.name.endswith("id-reservations.json"))
    manifest.write_text("", encoding="utf-8")
    with pytest.raises(cutover.CutoverAborted, match=r"not in the journal and the backup manifest .* is unreadable"):
        cutover.cutover(project, resume=True)


def test_p4_lock_timeout_before_the_fence_reports_no_fence(project, quiesce, monkeypatch, capsys):
    monkeypatch.setattr(cutover, "_connect", lambda p: sqlite3.connect(p, isolation_level=None, timeout=0.2))
    holder = sqlite3.connect(db(project), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        code = cutover.main(["--root", str(project), "--json"])
    finally:
        holder.rollback()
        holder.close()
    report = json.loads(capsys.readouterr().out)
    assert code == cutover.EXIT_FAILED_UNFENCED
    assert report["fence"]["fence_up"] is False and "locked" in report["error"]
    assert "no cutover fence is up" in report["hint"]


def test_p5_backfill_failure_reports_the_fence_that_is_up(project, quiesce, capsys):
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("INSERT INTO entity_fts(kind,id,title,body) VALUES('bug','B-404','orphan','x')")
    assert cutover.main(["--root", str(project), "--json"]) == cutover.EXIT_FENCED
    report = json.loads(capsys.readouterr().out)
    assert report["fence"]["fence_up"] is True and "ValueError" in report["error"]
    assert "--resume or --rollback" in report["hint"]
    assert cutover.main(["--root", str(project), "--rollback"]) == 0


def test_p1_dry_run_leaves_a_leftover_wal_untouched(project, monkeypatch):
    import subprocess
    import sys
    from taskmaster.native import quiesce as real
    monkeypatch.setitem(sys.modules, "taskmaster.native.quiesce", real)
    monkeypatch.setattr(real, "scan_processes", lambda root, **k: real.ScanResult([]))
    monkeypatch.setattr(real, "open_writers", lambda p: pytest.fail("the dry run must not probe open writers"))
    script = ("import sqlite3,os,sys; c=sqlite3.connect(sys.argv[1],isolation_level=None);"
              "c.execute('PRAGMA wal_autocheckpoint=0');"
              "c.execute(\"INSERT INTO meta VALUES('probe_crash','1')\"); os._exit(0)")
    subprocess.run([sys.executable, "-c", script, str(db(project))], check=True)
    wal = db(project).with_name("store.db-wal")
    assert wal.stat().st_size > 0
    before = {path: path.read_bytes() for path in (db(project), wal)}
    report = cutover.dry_run(project)
    assert report["quiesce"]["open_writers"] == "not probed in dry run"
    assert {path: path.read_bytes() for path in (db(project), wal)} == before


def test_unbackfilled_handover_status_is_refused_until_the_legacy_latch_runs(project, quiesce, monkeypatch):
    point_server_at(monkeypatch, project)
    bs.backlog_handover_create(tldr="a session", next_action="continue")
    store.reset_for_tests()
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("UPDATE entities SET doc=json_remove(doc,'$.status') WHERE kind='handover'")
        connection.execute("UPDATE entities SET doc=json_remove(doc,'$.handover_status_backfilled') "
                           "WHERE kind='backlog'")
    before = tree_hash(project)
    with pytest.raises(cutover.CutoverRefused, match="handover-status backfill has not run.*1 handover"):
        cutover.cutover(project)
    assert any("handover-status" in r for r in cutover.dry_run(project)["refusals"])
    assert tree_hash(project) == before
    monkeypatch.setattr(bs, "_HANDOVER_STATUS_BACKFILL_RAN", False)
    bs.backlog_handover_list()  # The guidance: one bridge-client handover call runs the latch.
    store.reset_for_tests()
    assert cutover.cutover(project)["ok"]
    handovers = [doc for (kind, _), (doc, _, _) in committed(project).items() if kind == "handover"]
    assert handovers and all(doc.get("status") == "open" for doc in handovers)


def test_post_activation_resume_needs_no_quiesce_or_lock(project, quiesce, monkeypatch):
    from taskmaster.coordinator.ownership import Ownership
    crash_at(monkeypatch, "activate:after-commit")
    with pytest.raises(Injected):
        cutover.cutover(project)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    quiesce.scan_processes = lambda root, **k: [{"pid": 9, "name": "native mcp server"}]
    quiesce.live_owner = lambda root: {"pid": 10, "kind": "native coordinator"}
    with Ownership(project):  # A native coordinator already runs.
        report = cutover.cutover(project, resume=True)
    assert report["ok"] and report["completed_stages"][-1] == "release"


def test_a_normal_run_refuses_a_leftover_journal(project, quiesce, monkeypatch):
    crash_at(monkeypatch, "reconcile:begin")
    with pytest.raises(Injected):
        cutover.cutover(project)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:  # Fence cleared by hand.
        connection.execute("DELETE FROM meta WHERE key LIKE 'migration_%'")
    with pytest.raises(cutover.CutoverRefused, match="cutover journal already exists.*--resume or --rollback"):
        cutover.cutover(project)


def test_pre_native_clients_are_refused_by_an_activated_store(project, quiesce):
    """Code rollback to a bridge (legacy-only) build: its admission and its Store both refuse,
    and refusing writes nothing."""
    from taskmaster import admission
    cutover.cutover(project)
    before = tree_hash(project)
    with closing(sqlite3.connect(db(project))) as connection:
        assert (admission.LEGACY_SCHEMA_VERSION, admission.CLIENT_PROTOCOL) == (1, 1)
        with pytest.raises(UnsupportedStoreError, match="schema_version=2"):
            assert_compatible(connection)
    store.reset_for_tests()
    with pytest.raises(UnsupportedStoreError):
        store.open_store(root=project).get("task", "cut-epic-001")
    store.reset_for_tests()
    assert tree_hash(project) == before


def test_twins_verify_opt_in_runs_the_carryover_oracle(project, monkeypatch):
    from taskmaster.native import carryover
    from tests.native_twins import activate_native
    calls = []
    real = carryover.verify_carryover
    monkeypatch.setattr(carryover, "verify_carryover", lambda c, before: calls.append(before) or real(c, before))
    monkeypatch.setenv("TASKMASTER_TWINS_VERIFY", "1")
    activate_native(project)
    assert len(calls) == 1 and calls[0]["version"] == carryover.DIGEST_VERSION


# Keys a fresh legacy adoption adds that the native store never held: `projection_schema`
# is stamped into the backlog meta by the legacy importer (documented in the runbook).
ADOPTION_ONLY = {("backlog", "__backlog__"): {"meta": {"projection_schema"}}}


def _without_adoption_only(key, doc):
    from taskmaster.native_routing.derived import KEYS as DERIVED_BACKLOG_KEYS
    doc = json.loads(json.dumps(doc))
    if key == ("backlog", "__backlog__"):  # Derived on native stores (the twins compare them the same way).
        doc = {k: v for k, v in doc.items() if k not in DERIVED_BACKLOG_KEYS}
    for field, subkeys in ADOPTION_ONLY.get(key, {}).items():
        if isinstance(doc.get(field), dict):
            for sub in subkeys:
                doc[field].pop(sub, None)
    return doc


def test_escape_hatch_recovers_every_authored_document_into_a_fresh_legacy_store(project, quiesce, monkeypatch,
                                                                                    tmp_path):
    """M1 = A: after activation, the projection files re-adopted by a fresh legacy store carry
    every authored document of every kind (archived ones, prose bodies and unknown fields
    included), and the documents written natively after activation."""
    from tests.native_twins import Twins
    point_server_at(monkeypatch, project)
    bs.backlog_handover_create(tldr="legacy handover", next_action="go",
                               body="Long prose body\n\nwith paragraphs\n\n- and a list\n\n"
                                    "Summary\n=========\n\nsetext heading above")
    bs.backlog_idea_create(title="An idea", body="idea prose\n\nsecond paragraph")
    bs.backlog_decision_create(title="A decision", options=["a", "b"], recommendation=1)
    bs.backlog_issue_create(title="An issue", severity="P2", evidence="ev")
    bs.backlog_note(action="create", text="a note")
    bs.backlog_bug_create(title="To archive")
    bs.backlog_bug_update(bug_id="B-010", field="fix_commit", value="abc123")
    bs.backlog_bug_update(bug_id="B-010", field="status", value="fixed")
    bs.backlog_bug_archive(bug_id="B-010")
    # An unknown field and a prose body, hand-edited into a task file and adopted by legacy.
    task_file = project / ".taskmaster" / "tasks" / "cut-epic-002.md"
    text = task_file.read_text(encoding="utf-8")
    head, _, _ = text.partition("\n---\n")
    task_file.write_text(head + "\nx_custom_field:\n  nested:\n  - 1\n  - null\n---\n\n## Notes\n\nHand prose.\n",
                         encoding="utf-8")
    bs.backlog_status()
    store.reset_for_tests()
    assert committed(project)[("task", "cut-epic-002")][0]["x_custom_field"] == {"nested": [1, None]}
    cutover.cutover(project)
    twins = Twins(monkeypatch, project, project)
    with twins.at(project):
        bs.backlog_add_task(title="Written natively", epic="cut-epic", phase="dev")
        bs.backlog_bug_create(title="Native bug")
        bs.backlog_complete_task(task_id="cut-epic-001", session_title="s", done="d")
        bs.backlog_update_task(task_id="cut-epic-002", tldr="updated natively")
    native = committed(project)
    kinds = {kind for kind, _ in native}
    assert {"task", "bug", "handover", "idea", "decision", "issue", "note", "epic", "phase"} <= kinds
    assert any(archived for (_, _, archived) in native.values())
    assert native[("task", "cut-epic-002")][0]["x_custom_field"] == {"nested": [1, None]}
    with closing(sqlite3.connect(db(project))) as connection:  # Step 1: every export drained.
        assert connection.execute("SELECT COUNT(*) FROM projection_jobs "
                                  "WHERE state IN ('pending','claimed','conflict')").fetchone()[0] == 0
    store.reset_for_tests()
    # Steps 4-5: the files, without the native store, adopted by a fresh legacy store.
    fresh = tmp_path / "fresh"
    shutil.copytree(project, fresh, ignore=shutil.ignore_patterns("store.db*", "backups", "coordinator"))
    point_server_at(monkeypatch, fresh)
    bs.backlog_status()
    store.reset_for_tests()
    adopted = committed(fresh)
    missing = sorted(set(native) - set(adopted))
    assert not missing, f"documents lost by the escape hatch: {missing}"
    assert {("task", "cut-epic-003"), ("bug", "B-011")} <= set(native)  # Written after activation.
    for key in sorted(native):
        (n_doc, n_body, n_arch), (a_doc, a_body, a_arch) = native[key], adopted[key]
        assert _without_adoption_only(key, a_doc) == _without_adoption_only(key, n_doc), key
        assert (a_body or "").strip() == (n_body or "").strip() and a_arch == n_arch, key


# ── Re-review round 3 (probes A-G) ──────────────────────────────────────────

def _token(root):
    with closing(sqlite3.connect(db(root))) as connection:
        return connection.execute("SELECT value FROM meta WHERE key='migration_token'").fetchone()[0]


def _stop_after_backup(project, monkeypatch, point="compare:begin"):
    crash_at(monkeypatch, point)
    with pytest.raises(Injected):
        cutover.cutover(project)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)


def _leak_bug(project, monkeypatch, title="Leaked acknowledged bug"):
    """A pre-bridge client ignoring the fence acknowledges a real write through the legacy path."""
    point_server_at(monkeypatch, project)
    with migration_owner(_token(project)):
        bs.backlog_bug_create(title=title)
    store.reset_for_tests()


def test_probe_a_resume_keeps_a_real_leaked_write(project, quiesce, monkeypatch):
    _stop_after_backup(project, monkeypatch)
    _leak_bug(project, monkeypatch)
    with pytest.raises(cutover.CutoverRefused, match=r"B-010.*--resume to keep them"):
        cutover.rollback(project)
    report = cutover.cutover(project, resume=True)
    assert report["ok"] and report["drift_absorbed"] == 1 and "absorbed" in report["warnings"][0]
    assert committed(project)[("bug", "B-010")][0]["title"] == "Leaked acknowledged bug"
    with closing(sqlite3.connect(db(project))) as connection:
        assert_native(connection)
        stages = [r[0] for r in connection.execute(f"SELECT stage FROM {cutover.JOURNAL} WHERE status='done' ORDER BY seq")]
    assert stages.count("backup") == 2 and "drift" in stages


def test_probe_d_a_meta_user_key_refuses_rollback_and_resume_keeps_it(project, quiesce, monkeypatch):
    _stop_after_backup(project, monkeypatch)
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("INSERT INTO meta VALUES('pending_progress_log',?) ON CONFLICT(key) DO UPDATE SET "
                           "value=excluded.value", (json.dumps([{"ts": "t", "text": "## Only here"}]),))
    with pytest.raises(cutover.CutoverRefused, match=r"meta: .*pending_progress_log"):
        cutover.rollback(project)
    assert cutover.cutover(project, resume=True)["ok"]


def test_probe_e_a_failure_after_release_committed_is_success_with_a_warning(project, quiesce, monkeypatch, capsys):
    def hook(name):
        if name == "release:after-commit":
            raise OSError("ownership unlock failed")
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", hook)
    assert cutover.main(["--root", str(project), "--json"]) == cutover.EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and "ownership unlock failed" in report["warnings"][0]


def test_integrity_errors_that_are_not_corruption_refuse():
    class Locked:
        def execute(self, sql):
            raise sqlite3.OperationalError("database is locked")

    class Corrupt:
        def execute(self, sql):
            error = sqlite3.DatabaseError("database disk image is malformed")
            error.sqlite_errorcode = 11
            raise error
    with pytest.raises(sqlite3.OperationalError):
        cutover._integrity_ok(Locked())
    assert cutover._integrity_ok(Corrupt()) is False


# ── Round 4 (probes T1-T8, suspicions a-c) ──────────────────────────────────

def test_t1_a_leak_with_a_pending_export_is_flushed_on_resume_not_deadlocked(project, quiesce, monkeypatch):
    _stop_after_backup(project, monkeypatch)
    _leak_bug(project, monkeypatch)
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("UPDATE projection SET dirty=1 WHERE file LIKE 'bugs/B-010%'")
    report = cutover.cutover(project, resume=True)
    assert report["ok"] and report["drift_absorbed"] == 1
    assert committed(project)[("bug", "B-010")][0]["title"] == "Leaked acknowledged bug"
    assert "Leaked acknowledged bug" in (project / ".taskmaster" / "bugs" / "B-010.md").read_text(encoding="utf-8")
    with closing(sqlite3.connect(db(project))) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projection WHERE dirty=1").fetchone()[0] == 0


def test_t2_a_write_racing_the_backup_cannot_wedge_compare(project, quiesce, monkeypatch):
    real, fired = cutover.write_backup, []

    def racing(connection, root, carryover):
        if not fired:
            fired.append(1)
            with closing(sqlite3.connect(db(root), isolation_level=None, timeout=0.2)) as writer:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    writer.execute("INSERT INTO meta VALUES('linear_receipt_x','raced')")
        return real(connection, root, carryover)
    monkeypatch.setattr(cutover, "write_backup", racing)
    assert cutover.cutover(project)["ok"]


def test_t6_pre_1980_mtimes_do_not_abort_the_archive(project, quiesce):
    import os
    os.utime(project / ".taskmaster" / "backlog.yaml", (0, 0))
    assert cutover.cutover(project)["ok"]


def test_the_drift_cap_holds_within_an_invocation_and_a_later_resume_can_still_absorb(project, quiesce, monkeypatch):
    """A writer that never stops is capped per invocation (the count is journaled, so a crash
    inside the invocation does not reset it). Once it is stopped, the operator's next --resume
    absorbs the last writes: a per-cutover cap would trap them behind a permanent refusal."""
    def writer(name):
        if name == "backfill:begin":
            with closing(sqlite3.connect(db(project), isolation_level=None)) as w:
                w.execute("INSERT INTO sessions(session,pid) VALUES(hex(randomblob(8)),1)")
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", writer)
    with pytest.raises(cutover.CutoverAborted, match="keeps changing"):
        cutover.cutover(project)
    with closing(sqlite3.connect(db(project))) as connection:
        drifts = connection.execute(f"SELECT COUNT(*) FROM {cutover.JOURNAL} WHERE stage='drift'").fetchone()[0]
    assert drifts == cutover.MAX_DRIFT_ABSORPTIONS
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)  # The writer is stopped.
    report = cutover.cutover(project, resume=True)
    assert report["ok"] and report["drift_absorbed"] == 1


def test_only_the_projection_set_is_archived_and_compared(project, quiesce, monkeypatch):
    notes = project / ".taskmaster" / "my-notes.txt"
    notes.write_text("the user's own file", encoding="utf-8")
    _stop_after_backup(project, monkeypatch)
    import zipfile
    with closing(sqlite3.connect(db(project))) as connection:
        archive = cutover._detail(cutover.journal(connection), "backup")["projection_archive"]["path"]
    with zipfile.ZipFile(archive) as zipped:
        assert "my-notes.txt" not in zipped.namelist() and "backlog.yaml" in zipped.namelist()
    notes.write_text("edited during the fence", encoding="utf-8")
    assert cutover.rollback(project)["ok"]  # A user file is neither a divergence nor restored.
    assert notes.read_text(encoding="utf-8") == "edited during the fence"




# ── Rollback: succeeds only when nothing leaked, restores nothing ───────────

def _projection_tree(root):
    base = root / ".taskmaster"
    return {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*")
            if p.is_file() and "local" not in p.relative_to(base).parts}


def _digest(root):
    with closing(sqlite3.connect(db(root))) as connection:
        return cutover.domain_digest(connection)


def test_rollback_with_nothing_leaked_is_exact_and_restores_nothing(project, quiesce, monkeypatch):
    before, files, digest = legacy_state(project), _projection_tree(project), _digest(project)
    _stop_after_backup(project, monkeypatch)
    report = cutover.rollback(project)
    assert report["ok"] and report["restored_from"] is None and report["staging_dropped"] > 0
    assert legacy_state(project) == before and _projection_tree(project) == files and _digest(project) == digest
    assert not list((db(project).parent / "backups").glob("pre-rollback-*"))
    assert cutover.cutover(project)["ok"]


def test_rollback_refuses_changed_rows_and_names_them(project, quiesce, monkeypatch):
    _stop_after_backup(project, monkeypatch)
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("DELETE FROM entities WHERE kind='bug'")
    with pytest.raises(cutover.CutoverRefused,
                       match=r"entities: 0 added, 1 removed.*B-001.*--resume to keep them.*pre-native-.*Manual restore"):
        cutover.rollback(project)
    with closing(sqlite3.connect(db(project))) as connection:
        assert connection.execute("SELECT value FROM meta WHERE key='migration_state'").fetchone()[0] == "migrating"


def test_rollback_refuses_a_session_touch_and_resume_keeps_it(project, quiesce, monkeypatch):
    _stop_after_backup(project, monkeypatch)
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("INSERT INTO sessions(session,pid) VALUES('reader',1)")
    with pytest.raises(cutover.CutoverRefused, match=r"sessions: 1 added"):
        cutover.rollback(project)
    assert cutover.cutover(project, resume=True)["ok"]


def test_rollback_refuses_divergent_projection_files_and_names_them(project, quiesce, monkeypatch):
    _stop_after_backup(project, monkeypatch)
    backlog = project / ".taskmaster" / "backlog.yaml"
    backlog.write_bytes(backlog.read_bytes() + b"# written through the fence\n")
    with pytest.raises(cutover.CutoverRefused, match=r"projection files differ .*1 changed \(backlog.yaml\)"):
        cutover.rollback(project)
    assert backlog.read_bytes().endswith(b"# written through the fence\n")  # Nothing restored.


def test_rollback_refuses_a_changed_reservation_sidecar(project, quiesce, monkeypatch):
    _stop_after_backup(project, monkeypatch)
    (db(project).parent / "id-reservations.json").write_text('{"bug": ["B-9", "B-12"]}\n', encoding="utf-8")
    with pytest.raises(cutover.CutoverRefused, match="id-reservations.json changed"):
        cutover.rollback(project)


def test_rollback_refuses_a_damaged_store_and_names_the_manual_restore(project, quiesce, monkeypatch):
    _stop_after_backup(project, monkeypatch)
    _break_index(db(project))
    with pytest.raises(cutover.CutoverRefused, match=r"fails integrity_check.*pre-native-.*Manual restore from a backup"):
        cutover.rollback(project)
    with closing(sqlite3.connect(db(project))) as connection:  # No REINDEX, no restore: untouched.
        assert "missing from index" in connection.execute("PRAGMA integrity_check").fetchone()[0]
        assert connection.execute("SELECT value FROM meta WHERE key='migration_state'").fetchone()[0] == "migrating"


def test_a_writer_cannot_land_inside_the_rollback_transaction(project, quiesce, monkeypatch):
    _stop_after_backup(project, monkeypatch)
    raced = []

    def racing_writer(name):
        if name == "rollback:before-commit":
            with closing(sqlite3.connect(db(project), isolation_level=None, timeout=0.2)) as writer:
                try:
                    writer.execute("UPDATE entities SET body='RACED' WHERE kind='bug'")
                except sqlite3.OperationalError as error:
                    raced.append(str(error))
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", racing_writer)
    assert cutover.rollback(project)["ok"]
    assert raced == ["database is locked"]


def test_t5_any_fence_state_other_than_ready_is_up(project, quiesce, monkeypatch, capsys):
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("INSERT INTO meta VALUES('migration_state','paused')")

    def boom(connection, root, fence, *, verify):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(cutover, "_clear", boom)
    assert cutover.main(["--root", str(project), "--rollback", "--clear-orphan-fence", "--json"]) == cutover.EXIT_FENCED
    report = json.loads(capsys.readouterr().out)
    assert not report["ok"] and report["fence"]["fence_up"] is True


def test_a_failure_after_the_rollback_committed_is_success_with_a_warning(project, quiesce, monkeypatch, capsys):
    _stop_after_backup(project, monkeypatch)
    real = cutover._ownership

    class Unlocking:
        def __init__(self, held):
            self.held = held

        def close(self):
            self.held.close()
            raise OSError("ownership unlock failed")
    monkeypatch.setattr(cutover, "_ownership", lambda root: Unlocking(real(root)))
    assert cutover.main(["--root", str(project), "--rollback", "--json"]) == cutover.EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and "ownership unlock failed" in report["warnings"][0]


def test_t8_after_the_drift_cap_resume_keeps_the_writes_or_clear_orphan_fence_keeps_them_legacy(
        project, quiesce, monkeypatch, tmp_path):
    n = [0]

    def writer(name):
        if name == "backfill:begin":
            n[0] += 1
            with closing(sqlite3.connect(db(project), isolation_level=None)) as w:
                w.execute("INSERT INTO entities(kind,id,epic,status,archived,deleted,doc,body,rev,updated_seq) "
                          f"SELECT kind,'B-{700 + n[0]}',epic,status,0,0,json_set(doc,'$.id','B-{700 + n[0]}'),"
                          "body,1,99 FROM entities WHERE kind='bug' AND id='B-001'")
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", writer)
    with pytest.raises(cutover.CutoverAborted, match="keeps changing"):
        cutover.cutover(project)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    twin = tmp_path / "twin"
    shutil.copytree(project, twin)
    with pytest.raises(cutover.CutoverRefused, match="--resume to keep them"):
        cutover.rollback(project)
    assert cutover.cutover(project, resume=True)["ok"]  # The writer stopped: the writes go native.
    assert ("bug", "B-704") in committed(project)
    report = cutover.rollback(twin, clear_orphan_fence=True)  # Or stay legacy, keeping every row.
    assert report["ok"] and report["staging_dropped"] > 0
    assert ("bug", "B-704") in committed(twin)
    assert cutover.cutover(twin)["ok"]


def _variant(name, project, monkeypatch):
    if name == "clean":
        _stop_after_backup(project, monkeypatch)
        return cutover.rollback(project)
    if name == "before-backup":
        _stop_after_backup(project, monkeypatch, point="backup:begin")
        return cutover.rollback(project)
    if name == "orphan-fence":
        with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
            connection.execute("INSERT INTO meta VALUES('migration_state','migrating')")
        return cutover.rollback(project, clear_orphan_fence=True)
    if name == "leak-kept-legacy":
        _stop_after_backup(project, monkeypatch)
        _leak_bug(project, monkeypatch)
        return cutover.rollback(project, clear_orphan_fence=True)
    raise AssertionError(name)


@pytest.mark.parametrize("variant", ["clean", "before-backup", "orphan-fence", "leak-kept-legacy"])
def test_a_fresh_cutover_succeeds_after_every_rollback_path(project, quiesce, monkeypatch, variant):
    assert _variant(variant, project, monkeypatch)["ok"]
    assert cutover.cutover(project)["ok"]
    with closing(sqlite3.connect(db(project))) as connection:
        assert_native(connection)


def _runbook_restore_script() -> str:
    import re
    text = (Path(__file__).resolve().parents[1] / "docs" / "runbooks" / "native-cutover.md").read_text(encoding="utf-8")
    return re.search(r"```python manual-restore\n(.*?)```", text, re.S).group(1)


@pytest.mark.allow_projection_bypass  # The documented procedure restores projection files by hand.
def test_the_documented_manual_restore_returns_the_pre_cutover_state(project, quiesce, monkeypatch, tmp_path):
    """Follows the runbook literally: a leak made rollback refuse; the operator stops every
    client and runs the documented script, then `--rollback --clear-orphan-fence`."""
    import subprocess
    import sys
    before, files, digest = legacy_state(project), _projection_tree(project), _digest(project)
    _stop_after_backup(project, monkeypatch)
    _leak_bug(project, monkeypatch)
    with pytest.raises(cutover.CutoverRefused, match="Manual restore from a backup"):
        cutover.rollback(project)
    script = tmp_path / "restore_backup.py"
    script.write_text(_runbook_restore_script(), encoding="utf-8")
    result = subprocess.run([sys.executable, str(script), str(project)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert cutover.main(["--root", str(project), "--rollback", "--clear-orphan-fence"]) == cutover.EXIT_OK
    assert legacy_state(project) == before
    assert _digest(project) == digest
    assert _projection_tree(project) == files
    aside = next((db(project).parent / "backups").glob("aside-*"))
    assert (aside / "store.db").exists() and (aside / "files" / "bugs" / "B-010.md").exists()
    report = cutover.dry_run(project)
    assert report["store_state"]["authority"] == "legacy" and report["store_state"]["migration_state"] == "ready"
    point_server_at(monkeypatch, project)  # Legacy restarts and re-imports nothing.
    bs.backlog_status()
    store.reset_for_tests()
    assert ("bug", "B-010") not in committed(project)
    assert cutover.cutover(project)["ok"]


def test_carried_work_is_exported_by_the_native_exporter_after_activation(project, quiesce, monkeypatch):
    """Round-4 #4: work the flush could not export becomes native projection jobs at activation."""
    _stop_after_backup(project, monkeypatch)
    _leak_bug(project, monkeypatch)
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("UPDATE entities SET doc=json_set(doc,'$.title','Edited, never exported') "
                           "WHERE kind='bug' AND id='B-010'")
        connection.execute("UPDATE projection SET dirty=1 WHERE file='bugs/B-010.md'")
    monkeypatch.setattr(cutover, "legacy_flush", lambda root, token: {"flushed": False})  # e.g. a locked file
    report = cutover.cutover(project, resume=True)
    assert report["ok"] and any("carried unexported" in w for w in report["warnings"])
    assert report["stages"]["activate"]["carried_exports_queued"] == 1
    bug_file = project / ".taskmaster" / "bugs" / "B-010.md"
    assert "Edited, never exported" not in bug_file.read_text(encoding="utf-8")
    from taskmaster.native_routing import projection as native_projection
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        native_projection.drain(connection, project / ".taskmaster", session="test", progress_wait=False)
    assert "Edited, never exported" in bug_file.read_text(encoding="utf-8")
    with closing(sqlite3.connect(db(project))) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projection WHERE dirty=1").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM projection_jobs WHERE state IN "
                                  "('pending','claimed','conflict')").fetchone()[0] == 0
