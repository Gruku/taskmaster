"""User intent: prove the N15 cutover oracle catches every loss of DB-local state. A legacy
store with queued Linear pushes, dirty and quarantined projections, pending changelogs,
reservations and a high-water gap is snapshotted, backfilled and activated; the oracle must
report nothing, then name each category when that category alone is corrupted.
"""
from __future__ import annotations

from contextlib import closing
import json
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster.native import carryover, migrate
from taskmaster.native_routing import progress
from native_twins import activate_native, native_database, point_server_at, scaffold

APPLIED = [{"ts": "2026-09-01T10:00:00", "text": "### applied one"}, {"ts": "", "text": "### applied two"}]
PENDING = [{"ts": "2026-09-02T10:00:00", "text": "### pending never exported"}]


def _connect(root):
    return closing(sqlite3.connect(native_database(root), isolation_level=None, timeout=30))


def _build(root, monkeypatch):
    scaffold(root)
    point_server_at(monkeypatch, root)
    bs.backlog_add_epic(epic_id="test-epic", name="Test Epic", done_when="done")
    bs.backlog_add_phase(phase_id="dev", name="Development")
    for title in ("One", "Two"):
        bs.backlog_add_task(title=title, epic="test-epic", phase="dev")
    for title in ("Bug one", "Bug two", "Bug three"):
        bs.backlog_bug_create(title=title)
    assert bs.backlog_issue_create(title="An issue", severity="P2", evidence="recurs").startswith("Issue created")
    store.reset_for_tests()
    with _connect(root) as connection:
        connection.execute("INSERT OR REPLACE INTO sessions(session,pid,host,started,last_seen,cwd,current_tool) "
                           "VALUES('peer',4242,'host','2026-09-01','2026-09-02','/x','backlog_pick_task')")
        connection.executemany(
            "INSERT INTO linear_queue(seq,op,target_id,tracker_id,payload,state,attempts,last_error,claimed_by,claimed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(1, "task_upsert", "test-epic-001", "TR-1", '{"a":1}', "pending", 0, None, None, None),
             (2, "task_upsert", "test-epic-002", "TR-1", '{"b":2}', "claimed", 1, None, "peer", 1_700_000_000.5),
             (3, "task_upsert", "test-epic-001", "TR-1", "{}", "failed", 5, "boom", None, None)])
        files = [r[0] for r in connection.execute("SELECT file FROM projection ORDER BY file")]
        assert len(files) >= 3
        connection.execute("UPDATE projection SET dirty=1 WHERE file=?", (files[0],))
        connection.execute("UPDATE projection SET quarantined=1,quarantine_mtime=12.5,quarantine_size=7,"
                           "quarantine_hash='abc' WHERE file=?", (files[1],))
        connection.execute("UPDATE projection SET dirty=1,quarantined=1 WHERE file=?", (files[2],))
        connection.execute("INSERT INTO projection_conflict VALUES('tasks/x.md','task','x','2026-09-01','h',?)",
                           (b"conflict\r\nbytes",))
        connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('progress_log',?)", (json.dumps(APPLIED),))
        connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('pending_progress_log',?)", (json.dumps(PENDING),))
        # A tombstone and a high-water gap: B-003 is deleted, B-009 only reserved.
        connection.execute("UPDATE entities SET deleted=1 WHERE kind='bug' AND id='B-003'")
    sidecar = root / ".taskmaster" / "local" / "id-reservations.json"
    reserved = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
    reserved.setdefault("bug", []).append("B-009")
    reserved.setdefault("idea", []).append("IDEA-004")
    reserved.setdefault("task", []).append("test-epic-042")
    sidecar.write_text(json.dumps(reserved), encoding="utf-8")
    return root


@pytest.fixture
def project(tmp_path, monkeypatch):
    return _build(tmp_path / "project", monkeypatch)


@pytest.fixture
def cutover(project):
    """Snapshot, backfill + activate through the twins, reconcile: a clean cutover."""
    with _connect(project) as connection:
        before = carryover.snapshot_carryover(connection)
    seeded = activate_native(project)["seeded_bases"]
    with _connect(project) as connection:
        carryover.reconcile_progress(connection)
    return project, before, seeded


# ── snapshot ────────────────────────────────────────────────────────────────

def test_snapshot_is_deterministic_json_and_counts_local_state(project):
    with _connect(project) as connection:
        first = carryover.snapshot_carryover(connection)
        second = carryover.snapshot_carryover(connection)
    assert first == second
    assert json.loads(json.dumps(first)) == first
    assert first["retained"]["linear_queue"]["rows"] == 3
    assert first["linear_queue"] == {"claimed": 1, "failed": 1, "pending": 1}
    assert first["projection"]["dirty"] == 2 and first["projection"]["quarantined"] == 2
    assert first["projection"]["dirty_quarantined"] == 1
    assert first["ids"]["high_water"]["bug"] == {"prefix": "B-", "high": 9}
    assert first["ids"]["high_water"]["idea"] == {"prefix": "IDEA-", "high": 4}
    assert first["ids"]["reservations"]["present"] is True
    assert first["tombstones"]["count"] == 1
    assert first["progress"]["meta_applied"]["count"] == 2
    assert first["progress"]["meta_pending"]["count"] == 1
    assert first["changes"]["max_seq"] > 0
    # Large values are digests, never raw content.
    assert "B-009" not in json.dumps(first)


def test_snapshot_refuses_an_activated_store(cutover):
    root, _, _ = cutover
    with _connect(root) as connection, pytest.raises(Exception, match="before activation"):
        carryover.snapshot_carryover(connection)


def test_snapshot_refuses_a_malformed_reservation_sidecar(project):
    (project / ".taskmaster" / "local" / "id-reservations.json").write_text("{not json", encoding="utf-8")
    with _connect(project) as connection, pytest.raises(ValueError, match="id-reservations"):
        carryover.snapshot_carryover(connection)


# ── round trip ──────────────────────────────────────────────────────────────

def test_a_clean_cutover_preserves_everything(cutover):
    root, before, seeded = cutover
    with _connect(root) as connection:
        assert carryover.verify_carryover(connection, before, seeded_bases=seeded) == []
        assert connection.execute("SELECT high_water FROM id_counters WHERE kind='bug'").fetchone()[0] == 9
        assert connection.execute("SELECT 1 FROM id_reservations WHERE kind='task' AND public_id='test-epic-042'").fetchone()


CORRUPTIONS = {
    "linear_queue": ["UPDATE linear_queue SET state='failed' WHERE seq=1"],
    "projection": ["UPDATE projection SET quarantined=0,quarantine_hash=NULL"],
    "projection_base": ["INSERT INTO projection_base VALUES('extra.md',x'00ff')"],
    "projection_conflict": ["DELETE FROM projection_conflict"],
    "sessions": ["DELETE FROM sessions WHERE session='peer'"],
    "meta": ["UPDATE meta SET value='[]' WHERE key='pending_progress_log'"],
    "id counter": ["UPDATE id_counters SET high_water=3 WHERE kind='bug'"],
    "id counter missing": ["DELETE FROM id_counters WHERE kind='idea'"],
    "reservation": ["DELETE FROM id_reservations WHERE public_id='test-epic-042'"],
    "tombstone": ["UPDATE entity_core SET deleted=0 WHERE deleted=1"],
    "domain_events": ["DELETE FROM domain_events WHERE seq=(SELECT MAX(seq) FROM domain_events)"],
    "event high water": ["UPDATE sqlite_sequence SET seq=seq-1 WHERE name='domain_events'"],
    "changelog": [f"DELETE FROM sync_state WHERE key>='{progress.PENDING_PREFIX}' AND key<'progress.pending/'"],
    "seed marker": [f"DELETE FROM sync_state WHERE key='{progress.SEEDED_KEY}'"],
    "pre-N11 list": ["INSERT INTO sync_state VALUES('pending_progress_log','[]')"],
    "projection_jobs": ["INSERT INTO projection_jobs(revision,commit_seq,file,effect,input_json) VALUES(1,1,'f','write','{}')"],
    "command_receipts": ["INSERT INTO command_receipts VALUES('s','c','r','h','{}',1,NULL)"],
}
EXPECT = {"id counter": "bug", "id counter missing": "idea", "reservation": "test-epic-042",
          "event high water": "domain_events", "changelog": "changelog", "seed marker": "progress.seeded",
          "pre-N11 list": "pending_progress_log"}


@pytest.mark.parametrize("category", sorted(CORRUPTIONS))
def test_each_corrupted_category_is_reported(cutover, category):
    root, before, seeded = cutover
    with _connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in CORRUPTIONS[category]:
                connection.execute(statement)
            differences = carryover.verify_carryover(connection, before, seeded_bases=seeded)
        finally:
            connection.rollback()
        assert differences, category
        assert any(EXPECT.get(category, category) in line for line in differences), differences
        assert carryover.verify_carryover(connection, before, seeded_bases=seeded) == []


def test_a_changed_reservation_sidecar_is_reported(cutover):
    root, before, _ = cutover
    (root / ".taskmaster" / "local" / "id-reservations.json").write_text('{"bug":["B-001"]}', encoding="utf-8")
    with _connect(root) as connection:
        assert any("id-reservations" in line for line in carryover.verify_carryover(connection, before))


def test_a_digest_of_another_version_is_refused(cutover):
    root, before, _ = cutover
    with _connect(root) as connection:
        assert carryover.verify_carryover(connection, dict(before, version=0))


def test_a_repeat_backfill_leaves_native_local_state_alone(project):
    with _connect(project) as connection:
        migrate.backfill(connection)
        connection.execute("INSERT INTO projection_jobs(revision,commit_seq,file,effect,input_json) VALUES(1,1,'f','write','{}')")
        connection.execute("INSERT INTO command_receipts VALUES('s','c','r','h','{}',1,NULL)")
        connection.execute("INSERT INTO sync_state VALUES('checkout.x','{\"k\":1}')")
        connection.execute("INSERT INTO sync_state VALUES('pending_progress_log',?)",
                           (json.dumps([{"ts": "", "text": "pre-N11 native"}]),))
        before = carryover.snapshot_carryover(connection)
    assert before["native_local"]["projection_jobs"]["rows"] == 1
    seeded = activate_native(project)["seeded_bases"]  # backfills again
    with _connect(project) as connection:
        carryover.reconcile_progress(connection)
        assert carryover.verify_carryover(connection, before, seeded_bases=seeded) == []
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DELETE FROM command_receipts")
            connection.execute("DELETE FROM sync_state WHERE key='checkout.x'")
            differences = carryover.verify_carryover(connection, before, seeded_bases=seeded)
        finally:
            connection.rollback()
    assert any("command_receipts" in line for line in differences)
    assert any("sync_state" in line for line in differences)


# ── ID import ───────────────────────────────────────────────────────────────

def _id_state(connection):
    return (sorted(connection.execute("SELECT * FROM id_counters")),
            sorted(connection.execute("SELECT * FROM id_reservations")))


def test_import_id_state_is_idempotent_and_never_lowers(cutover):
    root, _, _ = cutover
    with _connect(root) as connection:
        first = _id_state(connection)
        carryover.import_id_state(connection, root)
        assert _id_state(connection) == first
        connection.execute("UPDATE id_counters SET high_water=50 WHERE kind='bug'")
        report = carryover.import_id_state(connection, root)
        assert connection.execute("SELECT high_water FROM id_counters WHERE kind='bug'").fetchone()[0] == 50
        assert report["high_water"]["bug"] == 50
        assert report["reservations"] >= 3


def test_import_id_state_matches_the_twins_rule(project):
    """Every allocated prefix gets a counter, even with no rows: zero, as the twins did."""
    (project / ".taskmaster" / "local" / "id-reservations.json").unlink()
    with _connect(project) as connection:
        migrate.backfill(connection)
        report = carryover.import_id_state(connection, project)
        counters = dict(((k, p), h) for k, p, h in connection.execute("SELECT * FROM id_counters"))
    assert counters == {("bug", "B-"): 3, ("issue", "ISS-"): 1, ("decision", "DEC-"): 0,
                        ("idea", "IDEA-"): 0, ("note", "NOTE-"): 0}
    assert report["reservations"] == 0


def test_import_id_state_refuses_a_malformed_sidecar(project):
    with _connect(project) as connection:
        migrate.backfill(connection)
        (project / ".taskmaster" / "local" / "id-reservations.json").write_text('{"bug": "B-1"}', encoding="utf-8")
        with pytest.raises(ValueError, match="id-reservations"):
            carryover.import_id_state(connection, project)
        assert connection.execute("SELECT COUNT(*) FROM id_counters").fetchone()[0] == 0


def test_import_id_state_joins_a_caller_transaction(project):
    with _connect(project) as connection:
        migrate.backfill(connection)
        connection.execute("BEGIN IMMEDIATE")
        carryover.import_id_state(connection, project)
        assert connection.in_transaction
        connection.rollback()
        assert connection.execute("SELECT COUNT(*) FROM id_counters").fetchone()[0] == 0


# ── progress reconciliation ─────────────────────────────────────────────────

def _sync_state(connection):
    return sorted(connection.execute("SELECT key,value_json FROM sync_state"))


def test_reconcile_progress_seeds_once_and_a_later_seed_is_a_no_op(project):
    activate_native(project)
    with _connect(project) as connection:
        connection.execute("INSERT INTO sync_state VALUES('pending_progress_log',?)",
                           (json.dumps([{"ts": "", "text": "native list"}]),))
        report = carryover.reconcile_progress(connection)
        assert report["pending"] == 2 and report["applied"] == 2
        assert report["seeded"]["pending"] == 1 and report["seeded"]["applied"] == 2
        state = _sync_state(connection)
        pending = [json.loads(v)["text"] for k, v in state if k.startswith(progress.PENDING_PREFIX)]
        assert pending == [PENDING[0]["text"], "native list"]
        assert carryover.reconcile_progress(connection)["pending"] == 2
        assert _sync_state(connection) == state
        connection.execute("BEGIN IMMEDIATE")
        progress.seed(connection)
        connection.commit()
        assert _sync_state(connection) == state
