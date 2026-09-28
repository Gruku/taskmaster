# User intent: the N16 runner's correctness gates once passed vacuously (a review proved lies went
# through); each injected lie below must now make the runner FAIL, so acceptance evidence can be trusted.
"""Fault-injection regression tests for scripts/native_n16_acceptance.py, plus pure unit checks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import native_n16_acceptance as runner  # noqa: E402

FAULT_WORKER = Path(__file__).resolve().parent / "n16_fault_worker.py"
DRIVER = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import native_n16_acceptance as m; "
          "m.SELF = Path(sys.argv[2]); sys.exit(m.main(sys.argv[3:]))")
COMMON = ["--dataset", "small", "--small-scale", "0.04", "--seed", "16", "--clients", "1", "--modes", "disjoint",
          "--samples", "5", "--warmup", "1", "--smoke"]


def run_runner(work: Path, scenarios: str, fault: str = "", *, worker: Path | None = None):
    environment = {k: v for k, v in os.environ.items() if k not in ("TASKMASTER_ROOT", "TASKMASTER_METRICS")}
    environment.update(TASKMASTER_SERVICE_IDLE_SECONDS="5", N16_FAULT=fault)
    results = work / f"results-{fault or 'clean'}-{scenarios.replace(',', '_')}.json"
    done = subprocess.run([sys.executable, "-c", DRIVER, str(SCRIPTS), str(worker or runner.SELF), "--work", str(work),
                           "--scenarios", scenarios, "--results", str(results), *COMMON],
                          cwd=REPO, env=environment, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=900)
    report = json.loads(results.read_text(encoding="utf-8")) if results.exists() else None
    return done, report


def failed_checks(report, scenario):
    rows = [r for r in report["results"] if r["scenario"] == scenario]
    assert rows, report["results"]
    return {c["check"] for r in rows for c in r["checks"] if not c["ok"]}, rows




@pytest.fixture(scope="module")
def clean_work(tmp_path_factory):
    work = tmp_path_factory.mktemp("n16-clean")
    done, report = run_runner(work, "write.prose")  # a clean run must pass
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert report["results"][0]["verdict"] == "pass"
    return work


@pytest.fixture
def work(clean_work, tmp_path_factory):
    # A fresh --work per run: the runner refuses one that a prior run already mutated.
    return tmp_path_factory.mktemp("n16-faults")


@pytest.mark.real_service_process
@pytest.mark.xdist_group("heavy_processes")
def test_a_work_dir_a_prior_run_mutated_is_refused(clean_work):
    before = {path: path.stat().st_mtime_ns for path in clean_work.rglob("*") if path.is_file()}
    done, _ = run_runner(clean_work, "write.prose")
    assert done.returncode != 0
    # Refused before anything ran: no file of the prior run was touched and no new one written.
    assert {path: path.stat().st_mtime_ns for path in clean_work.rglob("*") if path.is_file()} == before
    assert "fresh --work" in done.stdout + done.stderr, done.stdout[-2000:] + done.stderr[-2000:]


def test_claim_work_marks_a_fresh_dir_and_refuses_a_used_one(tmp_path):
    runner.claim_work(tmp_path / "new")
    assert (tmp_path / "new" / runner.WORK_MARKER).is_file()
    with pytest.raises(runner.Refused, match="fresh --work"):
        runner.claim_work(tmp_path / "new")
    (tmp_path / "old" / "ds-small").mkdir(parents=True)  # datasets from a run before the marker existed
    with pytest.raises(runner.Refused, match="fresh --work"):
        runner.claim_work(tmp_path / "old")
    assert not (tmp_path / "old" / runner.WORK_MARKER).exists()


@pytest.mark.real_service_process
@pytest.mark.xdist_group("heavy_processes")
@pytest.mark.parametrize("fault, scenario, gate", [
    ("drop_writes", "write.meta", "no_lost_ack"),           # acked with the high-water seq, nothing written
    ("strip_seq", "write.prose", "no_unexpected_errors"),   # real write, no sequence in the answer
    ("swallow", "write.meta", "no_unexpected_errors"),      # "Could not update: ..." is not a success
    ("fake_noop", "write.prose", "no_unexpected_errors"),   # a required commit answered as a no-op, nothing written
    ("fake_noop_half", "read.during_writes", "noop_answers_verified"),  # half the writes dropped as "unchanged"
    ("fail_first", "write.prose", "no_warmup_errors"),      # the warmup call fails: a cold-start defect
    ("fail_first", "read.details", "no_warmup_errors"),
    ("fail_first_any", "read.details", "no_warmup_errors"),  # the prime (first call after start) fails
    ("fail_first_any", "read.during_writes", "no_warmup_errors"),
    ("fail_first_any", "failure.expired_lease", "no_warmup_errors"),
    ("reuse_id", "write.create", "no_reused_id"),           # an existing id reported as created
    ("partial_composite", "write.composite", "no_partial_composite"),  # an invalid composite half-applied
    ("die_before_ready", "write.meta", None),               # a client that dies before the barrier
])
def test_injected_lie_fails_the_runner(work, fault, scenario, gate):
    done, report = run_runner(work, scenario, fault, worker=FAULT_WORKER)
    assert done.returncode == 1, done.stdout[-2000:] + done.stderr[-2000:]
    failed, rows = failed_checks(report, scenario)
    assert all(r["verdict"] == "fail" for r in rows)
    if gate is None:
        assert any(r.get("error") for r in rows)  # the scenario crashed loudly instead of releasing the barrier
    else:
        assert gate in failed, (failed, rows[0]["checks"])
    # A failed scenario's latency is never budget evidence.
    for row in report["budgets"]:
        if row["scenario"] == scenario:
            assert row["status"].startswith("invalid")


# ── pure unit checks (no processes) ─────────────────────────────────────────
@pytest.mark.parametrize("answer, kwargs, ok", [
    ("Updated `t-1` field `priority` → high [seq 7]", {"success": runner.SUCCESS["backlog_update_task"], "commit": "required"}, True),
    ("Updated `t-1` field `priority` → high", {"success": runner.SUCCESS["backlog_update_task"], "commit": "required"}, False),
    ("Could not update: store unavailable", {"success": runner.SUCCESS["backlog_update_task"]}, False),
    ("Updated `t-1` field `priority` → (not persisted)", {"success": runner.SUCCESS["backlog_update_task"]}, False),
    ("Updated `t-1` field `priority` → (not persisted)", {"success": runner.SUCCESS["backlog_update_task"], "noop_ok": True}, False),
    ("No change to `t-1` field `priority` — already `low`", {"success": runner.SUCCESS["backlog_update_task"]}, False),
    ("No change to `t-1` field `priority` — already `low`", {"success": runner.SUCCESS["backlog_update_task"], "noop_ok": True, "requested": "low"}, True),
    ("`t-1`.priority → unchanged (already `low`)", {"noop_ok": True, "requested": "low"}, True),
    # A no-op must name the value this op asked for, never another (e.g. a later peer write's).
    ("No change to `t-1` field `priority` — already `high`", {"success": runner.SUCCESS["backlog_update_task"], "noop_ok": True, "requested": "low"}, False),
    ("No change to `t-1` field `priority` — already `low`", {"success": runner.SUCCESS["backlog_update_task"], "noop_ok": True}, False),
    ("No change to `t-1` field `priority` — already `low` [seq 4]", {"success": runner.SUCCESS["backlog_update_task"], "noop_ok": True, "requested": "low"}, False),
    # A required commit can never pass as a no-op, whatever else the op allows.
    ("No change to `t-1` field `priority` — already `low`", {"success": runner.SUCCESS["backlog_update_task"], "commit": "required", "requested": "low"}, False),
    ("No change to `t-1` field `priority` — already `low`", {"success": runner.SUCCESS["backlog_update_task"], "commit": "required", "noop_ok": True, "requested": "low"}, False),
    ('{"ok": true, "receipt": {"commit_seq": 9}}', {"success": "json-ok", "commit": "required"}, True),
    ('{"ok": false, "error": "stale"}', {"success": "json-ok"}, False),
    ("", {}, False),
])
def test_classify_is_positive(answer, kwargs, ok):
    assert runner.classify(answer, **kwargs)[0] is ok


def test_a_scenario_without_checks_is_skipped_not_passed():
    result = runner.Result("x", "small", cells=["Sync: something"])
    data = result.finish()
    assert data["verdict"] == "skipped" and data["skipped_cells"]


def test_budget_rows_are_not_evidence_when_invalid_or_smoke():
    dist = {"samples": 5, "p50_ms": 1, "p95_ms": 1, "p99_ms": 1, "max_ms": 1}
    base = {"kind": "steady", "dataset": "small", "scenario": "write.meta", "clients": 1, "mode": "disjoint",
            "distributions": {"write.meta": dist}, "errors": {}}
    rows = runner.budget_rows([dict(base, verdict="fail"), dict(base, verdict="pass"),
                               dict(base, verdict="pass", distributions={"write.meta": dict(dist, samples=200)})])
    assert [r["status"] for r in rows] == ["invalid (correctness failed)", "smoke (<200 samples)", "met"]


def test_redact_keeps_no_authored_text():
    text = runner.redact("ValueError: task `secret-epic-001` titled Zorblax is locked")
    assert "Zorblax" not in text and "secret" not in text and text.startswith("ValueError#")


# ── ack judgement against a minimal event log (no processes) ────────────────
def _event_store(tmp_path, events):
    """A store holding only `domain_events`: [(seq, commit_key, kind, id, fields, after)]."""
    import sqlite3
    (tmp_path / ".taskmaster/local").mkdir(parents=True)
    with sqlite3.connect(tmp_path / ".taskmaster/local/store.db") as c:
        c.execute("CREATE TABLE domain_events(seq INTEGER PRIMARY KEY, commit_key TEXT, kind TEXT, id TEXT, "
                  "op TEXT, fields TEXT, after TEXT)")
        for seq, commit_key, kind, ident, fields, after in events:
            c.execute("INSERT INTO domain_events VALUES(?,?,?,?,?,?,?)",
                      (seq, commit_key, kind, ident, "update", json.dumps(fields), json.dumps(after)))
    return tmp_path


def _lost_ack(root, records):
    result = runner.Result("x", "small")
    runner.check_acks(result, root, records)
    return {c["check"]: c for c in result.data["checks"]}["no_lost_ack"]["ok"] is False


def _ack(seq, field, value, ident="t-1"):
    return {"ok": True, "seq": seq, "check": ["task", ident, field, value]}


MINUTE_ROLLOVER = [  # t-1 set to low, then a same-value write after the minute: last_referenced only
    (1, "c1", "task", "t-1", ["priority", "last_referenced"], {"priority": "low", "last_referenced": "12:00"}),
    (2, "c2", "task", "t-2", ["priority"], {"priority": "high"}),
    (3, "c3", "task", "t-1", ["last_referenced"], {"last_referenced": "12:01"}),
]


def test_a_same_value_write_after_the_minute_rolls_over_is_not_a_lost_ack(tmp_path):
    root = _event_store(tmp_path, MINUTE_ROLLOVER)
    assert not _lost_ack(root, [_ack(3, "priority", "low")])


def test_an_ack_whose_value_as_of_its_seq_differs_is_still_lost(tmp_path):
    root = _event_store(tmp_path, MINUTE_ROLLOVER)
    assert _lost_ack(root, [_ack(3, "priority", "high")])


def test_an_ack_naming_a_commit_of_another_entity_is_still_lost(tmp_path):
    # A dropped write acknowledged with a real sequence that belongs to someone else's commit.
    root = _event_store(tmp_path, MINUTE_ROLLOVER)
    assert _lost_ack(root, [_ack(2, "priority", "high")])


def test_a_later_value_does_not_rescue_an_ack(tmp_path):
    events = MINUTE_ROLLOVER + [(4, "c4", "task", "t-1", ["priority"], {"priority": "high"})]
    root = _event_store(tmp_path, events)
    assert _lost_ack(root, [_ack(3, "priority", "high")])


def test_claimed_anchors_are_normalized_as_the_tool_stores_them(tmp_path, monkeypatch):
    root = _event_store(tmp_path, [(1, "c1", "task", "t-1", ["anchors"], {"anchors": ["x", "y"]})])
    assert not _lost_ack(root, [_ack(1, "anchors", ",, x ,y,")])
    assert _lost_ack(root, [_ack(1, "anchors", ",,x")])
    monkeypatch.setattr(runner, "field_values", lambda root, kind, ids, field: {i: ["x", "y"] for i in ids})
    result = runner.Result("x", "small")
    runner.check_last_value(result, root, [_ack(1, "anchors", ",, x ,y,")])
    assert result.data["checks"][-1]["ok"] is True
    runner.check_last_value(result, root, [_ack(1, "anchors", "x")])
    assert result.data["checks"][-1]["ok"] is False


@pytest.mark.parametrize("stored, usable", [
    (["src/a.py", ",", " ", "", "a,b"], ["src/a.py"]),
    ('["docs/a.md", "docs/b.md"]', ["docs/a.md", "docs/b.md"]),   # a legacy stringified list
    (['["docs/a.md"', '"docs/b.md"],'], []),                      # fragments of one: never sampled
    ("src/a.py, src/b.py", ["src/a.py", "src/b.py"]),
    (None, []),
])
def test_inventory_anchors_are_only_values_the_tool_round_trips(stored, usable):
    assert runner.inventory_anchors(stored) == usable


# ── CodeMaestro-copy false positives (no processes) ─────────────────────────
def test_inventory_drops_orphan_tasks_the_tools_cannot_resolve():
    entities = {("task", "ok-001"): ({"epic": "e", "status": "todo"}, None, False),
                ("task", "orphan-006"): ({"title": "file only"}, None, False),       # no backlog row
                ("task", "no-epic-007"): ({"status": "todo"}, None, False),
                ("task", "done-008"): ({"epic": "e", "status": "done"}, None, False)}
    tasks, orphans = runner.inventory_tasks(entities)
    assert [i for i, _ in tasks] == ["ok-001"] and orphans == 2


def test_warmup_errors_are_not_unexpected_errors():
    def checks(records):
        result = runner.Result("x", "small")
        runner.check_unexpected(result, records)
        return {c["check"]: c["ok"] for c in result.data["checks"]}
    assert checks([{"ok": False, "warmup": True, "error": "x"}, {"ok": True}])["no_unexpected_errors"] is True
    assert checks([{"ok": False, "warmup": False, "error": "x"}]) == {"no_unexpected_errors": False,
                                                                      "no_warmup_errors": True}


def test_write_ops_mark_their_warmup():
    inv = {"tasks": ["t-1"], "epics": ["e"], "task_epic": {"t-1": "e"}, "phases": ["p"], "link_sources": [],
           "link_targets": []}
    ops = runner.plan_write("prose", inv, worker=0, workers=1, mode="disjoint", count=2, warmup=1,
                            rng=__import__("random").Random(1), scope="s")
    assert [op.get("warmup") for op in ops] == [True, False, False]


def test_noop_planner_skips_targets_whose_value_is_unknown():
    inv = {"tasks": ["t-1", "t-2"], "epics": ["e"], "task_epic": {"t-1": "e", "t-2": "e"}, "phases": ["p"],
           "link_sources": [], "link_targets": []}
    plan = lambda current: runner.plan_write("noop", inv, worker=0, workers=1, mode="disjoint", count=4, warmup=0,
                                             rng=__import__("random").Random(1), scope="s", current=current)
    ops = plan({"priority": {"t-1": None, "t-2": "high"}})
    assert {(op["kw"]["task_id"], op["kw"]["value"]) for op in ops} == {("t-2", "high")}
    with pytest.raises(runner.Refused):
        plan({"priority": {"t-1": None, "t-2": "<missing>"}})


@pytest.mark.parametrize("notices, text, ok", [
    ([], "body n16 blocked v1", True),
    (["sync pending: handovers/_archive/2025/x.md: quarantined"], "body n16 blocked v1", True),  # unrelated file
    ([], "body without the value", False),                                   # the file never got the value
    (["export pending: tasks/t-1.md: retrying a blocked replace"], "body n16 blocked v1", False),
    (["conflict: tasks/t-1.md differs from its base"], "body n16 blocked v1", False),
])
def test_blocked_replace_judges_the_target_file_not_the_whole_projection(tmp_path, notices, text, ok):
    flushed = {"state": "pending", "notices": notices}
    root = _projection_db(tmp_path)
    assert runner.blocked_replace_settled(root, flushed, "tasks/t-1.md", text, "n16 blocked v1") is ok


def test_a_precondition_failure_is_distinct_and_never_evidence():
    result = runner.Result("sync.checkout", "cm", kind="check")
    result.precondition("projections are not synchronized: 3 pre-existing quarantined files")
    data = result.finish()
    assert data["verdict"] == "precondition"
    assert runner.exit_status([data]) == 2  # not a clean pass, not a correctness failure
    assert runner.exit_status([data, dict(data, verdict="fail")]) == 1
    report = {"meta": {"started": "t", "git_sha": "0" * 10, "python": "3", "sqlite": "3", "machine": "m",
                       "samples_required": 200, "warmup": 20}, "results": [data], "budgets": [], "skipped": []}
    text = runner.markdown(report)
    assert "precondition" in text.lower() and "0 of 1 scenario runs pass" in text


def test_checkout_precondition_only_for_preexisting_quarantine():
    refused = {"state": "refused", "reason": "projections are not synchronized; resolve the listed paths first",
               "sync": {"state": "pending", "unresolved": ["handovers/a.md", "handovers/b.md"]}}
    assert runner.checkout_precondition(refused, {"handovers/a.md", "handovers/b.md"})
    assert runner.checkout_precondition(refused, {"handovers/a.md"}) is None      # a new unresolved file
    assert runner.checkout_precondition({"state": "completed"}, {"handovers/a.md"}) is None
    assert runner.checkout_precondition(dict(refused, reason="unknown checkout target"), {"handovers/a.md"}) is None


class _PendingThenSettles:
    def __init__(self, pending, final):
        self.pending, self.final, self.ids = pending, final, []

    def sync(self, *, caller_scope, request_id, **_):
        self.ids.append(request_id)
        if len(self.ids) <= self.pending:
            return {"state": "pending", "unresolved": ["tasks/t.md"],
                    "notices": ["sync pending: tasks/t.md: time budget exhausted or coordinator stopping; "
                                "retry the same sync id"]}
        return self.final


def test_a_pending_sync_is_retried_with_the_same_id_until_it_settles():
    client = _PendingThenSettles(2, {"state": "synchronized", "imports": [{"file": "tasks/t.md"}]})
    result, seconds, attempts = runner.sync_settled(client, bound=60)
    assert result["state"] == "synchronized" and attempts == 3 and len(set(client.ids)) == 1


def test_a_sync_that_never_settles_stops_at_the_bound():
    client = _PendingThenSettles(10 ** 6, None)
    result, seconds, attempts = runner.sync_settled(client, bound=0.2)
    assert result["state"] == "pending" and attempts >= 1 and len(set(client.ids)) == 1


# ── second review: no-op answers, warmup errors, checkout snapshot, blocked replace ──
def _noop_record(w, n, value, *, before, after, ident="t-1", field="next_step"):
    return {"ok": True, "noop": True, "seq": None, "w": w, "n": n, "check": ["task", ident, field, value],
            "hw_before": before, "hw_after": after}


def _noops_verified(root, records):
    result = runner.Result("x", "small")
    runner.check_noops(result, root, records)
    return {c["check"]: c for c in result.data["checks"]}["noop_answers_verified"]["ok"]


NEXT_STEP = [(1, "c1", "task", "t-1", ["next_step"], {"next_step": "a"}),
             (2, "c2", "task", "t-1", ["next_step"], {"next_step": "b"})]


def test_a_noop_replaying_the_clients_own_ack_is_verified(tmp_path):
    root = _event_store(tmp_path, NEXT_STEP)
    acked = dict(_ack(1, "next_step", "a"), w=0, n=0)
    assert _noops_verified(root, [acked, _noop_record(0, 1, "a", before=2, after=2)])


def test_a_noop_after_the_client_itself_changed_the_value_needs_the_store(tmp_path):
    root = _event_store(tmp_path, NEXT_STEP)
    records = [dict(_ack(1, "next_step", "a"), w=0, n=0), dict(_ack(2, "next_step", "b"), w=0, n=1),
               _noop_record(0, 2, "a", before=2, after=2)]
    assert not _noops_verified(root, records)


def test_a_noop_nobody_acked_must_match_the_store_at_that_time(tmp_path):
    root = _event_store(tmp_path, NEXT_STEP)
    assert _noops_verified(root, [_noop_record(0, 0, "b", before=2, after=2)])
    assert _noops_verified(root, [_noop_record(0, 0, "b", before=1, after=2)])   # set during the call
    assert not _noops_verified(root, [_noop_record(0, 0, "a", before=2, after=2)])  # a dropped write
    assert not _noops_verified(root, [_noop_record(0, 0, "zz", before=2, after=2)])
    assert not _noops_verified(root, [dict(_noop_record(0, 0, "b", before=2, after=2), hw_before=None)])


def test_another_clients_ack_does_not_vouch_for_a_noop(tmp_path):
    root = _event_store(tmp_path, NEXT_STEP)
    records = [dict(_ack(1, "next_step", "a"), w=1, n=0), _noop_record(0, 0, "a", before=2, after=2)]
    assert not _noops_verified(root, records)


def test_warmup_errors_fail_their_own_check_and_stay_out_of_latency():
    result = runner.Result("x", "small")
    records = [{"op": "write.prose", "ok": False, "warmup": True, "measured": False, "error": "Error: x", "ms": 1},
               {"op": "write.prose", "ok": True, "warmup": False, "measured": True, "ms": 2}]
    result.from_records(records, samples_required=0)
    runner.check_unexpected(result, records)
    checks = {c["check"]: c["ok"] for c in result.data["checks"]}
    assert checks == {"no_unexpected_errors": True, "no_warmup_errors": False}
    assert result.data["distributions"]["write.prose"]["samples"] == 1
    assert result.data["warmup_errors"] == 1


def test_checkout_quarantine_is_snapshotted_before_the_coordinator_starts(monkeypatch, tmp_path):
    class Proceeded(Exception):
        pass

    unresolved = ["handovers/a.md", "handovers/b.md"]
    state = {"started": False}

    class Client:
        def flush(self, through):
            return {"state": "exported"}

        def git_run(self, **_):
            return {"state": "refused", "reason": "projections are not synchronized; resolve the listed paths first",
                    "sync": {"state": "pending", "unresolved": unresolved}}

    def start(run, root):
        state["started"] = True
        return Client()

    class Dataset:
        name, root = "cm", tmp_path

        def inventory(self):
            raise Proceeded  # the scenario went on past the precondition: judged as a normal run

    monkeypatch.setattr(runner, "copy_project", lambda run, root, label: tmp_path)
    monkeypatch.setattr(runner, "start_coordinator", start)
    monkeypatch.setattr(runner, "stop_coordinator", lambda root: None)
    monkeypatch.setattr(runner, "high_water", lambda root: 0)
    monkeypatch.setattr(runner, "git", lambda *a: "")
    scenario = runner.SCENARIOS["sync.checkout"]["fn"]
    # Only one file was quarantined before the run; the second appeared after the coordinator started.
    monkeypatch.setattr(runner, "quarantined_files",
                        lambda root: set(unresolved) if state["started"] else {unresolved[0]})
    with pytest.raises(Proceeded):
        scenario(None, Dataset(), None, None)
    state["started"] = False
    monkeypatch.setattr(runner, "quarantined_files", lambda root: set(unresolved))
    assert scenario(None, Dataset(), None, None).finish()["verdict"] == "precondition"


def _projection_db(tmp_path, *, quarantined=0, job_state=None, flagged=False):
    import sqlite3
    (tmp_path / ".taskmaster/local").mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(tmp_path / ".taskmaster/local/store.db") as c:
        c.execute("CREATE TABLE projection(file TEXT PRIMARY KEY, kind TEXT, id TEXT, content_hash TEXT, "
                  "quarantined INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE projection_jobs(job_key INTEGER PRIMARY KEY, file TEXT, state TEXT)")
        c.execute("CREATE TABLE sync_state(key TEXT PRIMARY KEY, value_json TEXT)")
        c.execute("INSERT INTO projection VALUES('tasks/t-1.md','task','t-1','h',?)", (quarantined,))
        c.execute("INSERT INTO projection_jobs(file,state) VALUES('tasks/t-1.md','exported')")
        c.execute("INSERT INTO projection_jobs(file,state) VALUES('handovers/x.md','pending')")  # unrelated
        if job_state:
            c.execute("INSERT INTO projection_jobs(file,state) VALUES('tasks/t-1.md',?)", (job_state,))
        if flagged:
            c.execute("CREATE TABLE projection_conflict(file TEXT PRIMARY KEY, kind TEXT, id TEXT)")
            c.execute("INSERT INTO projection_conflict VALUES('tasks/t-1.md','task','t-1')")
    return tmp_path


@pytest.mark.parametrize("flushed, db, ok", [
    ({"state": "pending", "notices": ["export pending: durable jobs remain"]}, {}, True),
    ({"state": "pending", "notices": ["publisher busy"]}, {"job_state": "pending"}, False),
    ({"state": "pending", "notices": []}, {"job_state": "claimed"}, False),
    ({"state": "pending", "notices": []}, {"job_state": "conflict"}, False),
    ({"state": "pending", "notices": []}, {"quarantined": 1}, False),
    ({"state": "pending", "notices": []}, {"flagged": True}, False),
    (None, {}, False),                                   # no flush answer at all
    ("exported", {}, False),                             # not a dict
])
def test_blocked_replace_judges_the_target_files_own_projection_rows(tmp_path, flushed, db, ok):
    root = _projection_db(tmp_path, **db)
    assert runner.blocked_replace_settled(root, flushed, "tasks/t-1.md", "n16 blocked v1", "n16 blocked v1") is ok
