# User intent: the N16 runner's correctness gates once passed vacuously (a review proved lies went
# through); each injected lie below must now make the runner FAIL, so acceptance evidence can be trusted.
"""Fault-injection regression tests for scripts/native_n16_acceptance.py, plus pure unit checks."""
from __future__ import annotations

from contextlib import closing
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


def run_runner(work: Path, scenarios: str, fault: str = "", *, worker: Path | None = None, extra=()):
    environment = {k: v for k, v in os.environ.items() if k not in ("TASKMASTER_ROOT", "TASKMASTER_METRICS")}
    environment.update(TASKMASTER_SERVICE_IDLE_SECONDS="5", N16_FAULT=fault)
    results = work / f"results-{fault or 'clean'}-{scenarios.replace(',', '_')}.json"
    done = subprocess.run([sys.executable, "-c", DRIVER, str(SCRIPTS), str(worker or runner.SELF), "--work", str(work),
                           "--scenarios", scenarios, "--results", str(results), *COMMON, *extra],
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



# ── link planning: disjoint means each client owns its pairs ─────────────────
# A small-dataset run once "disjointly" linked from the same handovers as a peer (12 clients over 10
# sources) and from ISS-001 -> ISS-002 while a peer linked ISS-002 -> ISS-001 (the tool writes the
# inverse), so correct no-op answers failed as "acknowledged without a commit sequence".
LINK_INV = {"tasks": [f"t-{i}" for i in range(40)], "epics": ["e"], "task_epic": {}, "phases": ["p"],
            "link_sources": ["2026-01-06-h00004", "2026-01-07-h00007", "2026-01-11-h00023", "2026-01-14-h00033",
                             "ISS-001", "ISS-002", "ISS-003", "ISS-004", "ISS-005", "ISS-006"],
            "link_targets": ["IDEA-001", "IDEA-002", "IDEA-003", "ISS-001", "ISS-002", "ISS-003", "ISS-004",
                             "ISS-005", "ISS-006"],
            "entity_kind": {}}


def _link_plans(inv, workers, mode="disjoint", current=None, count=40):
    import random
    return [runner.plan_write("link", inv, worker=w, workers=workers, mode=mode, count=count, warmup=4,
                              rng=random.Random(w), scope="s", current=current) for w in range(workers)]


def test_disjoint_link_plans_for_12_clients_share_no_pair():
    plans = _link_plans(LINK_INV, 12)
    owned = [{frozenset(op["link"][:2]) for op in plan} for plan in plans]
    for a in range(12):
        assert owned[a], a
        for b in range(a + 1, 12):
            assert not owned[a] & owned[b], (a, b, owned[a] & owned[b])  # unordered: the inverse counts
    for plan in plans:
        assert all(op["commit"] == "required" and not op.get("noop_ok") for op in plan)
        state = {}
        for op in plan:  # each create finds the pair unlinked and each remove finds it linked
            source, target, action = op["link"][:3]
            assert state.get(frozenset((source, target)), False) is (action == "remove"), op
            state[frozenset((source, target))] = action == "create"
        assert not any(state.values())  # the run ends with every pair unlinked again


def test_disjoint_link_plans_skip_pairs_already_linked_either_way():
    current = {"links": {"ISS-001": ["ISS-002", "IDEA-001"]}}
    pairs = {frozenset(op["link"][:2]) for plan in _link_plans(LINK_INV, 10, current=current) for op in plan}
    assert frozenset(("ISS-001", "ISS-002")) not in pairs and frozenset(("ISS-001", "IDEA-001")) not in pairs


def test_disjoint_link_refuses_when_the_dataset_has_too_few_pairs():
    inv = dict(LINK_INV, link_sources=["ISS-001"], link_targets=["IDEA-001", "IDEA-002"])
    assert len(_link_plans(inv, 2)) == 2
    with pytest.raises(runner.Refused, match="pair"):
        _link_plans(inv, 3)


def test_same_link_plans_contend_and_accept_only_verified_noops():
    plans = _link_plans(LINK_INV, 4, mode="same")
    assert {op["link"][0] for plan in plans for op in plan} == {"2026-01-06-h00004"}
    assert all(op["commit"] == "optional" and op["noop_ok"] for plan in plans for op in plan)


@pytest.mark.parametrize("kind", ["meta", "prose", "path", "membership", "composite", "noop", "invalid", "retry"])
def test_disjoint_task_writes_refuse_fewer_tasks_than_clients(kind):
    inv = dict(LINK_INV, tasks=["t-1", "t-2"], task_epic={"t-1": "e", "t-2": "e"}, anchors=["a", "b"])
    current = {"priority": {"t-1": "low", "t-2": "low"}}
    with pytest.raises(runner.Refused, match="disjoint"):
        runner.plan_write(kind, inv, worker=2, workers=3, mode="disjoint", count=2, warmup=0,
                          rng=__import__("random").Random(1), scope="s", current=current)


def test_disjoint_composites_never_borrow_a_peers_task():
    import random
    inv = dict(LINK_INV, tasks=[f"t-{i}" for i in range(4)])
    plans = [runner.plan_write("composite", inv, worker=w, workers=2, mode="disjoint", count=6, warmup=0,
                               rng=random.Random(w), scope="s") for w in range(2)]
    touched = [{c["arguments"]["id"] for op in plan for c in op["kw"]["commands"]
                if not c["arguments"]["id"].startswith("n16-missing")} for plan in plans]
    assert not touched[0] & touched[1], touched


REL = "relates_to"


def _links(*targets):
    return {"links": [{"target": t, "type": REL} for t in targets]} if targets else {}


# ISS-001 and IDEA-001 imported unlinked; c3 links them (both ends, one commit); c5 unlinks them.
LINKS = [(1, "c1", "issue", "ISS-001", ["id", "title"], {"id": "ISS-001"}),
         (2, "c2", "idea", "IDEA-001", ["id", "title"], {"id": "IDEA-001"}),
         (3, "c3", "issue", "ISS-001", ["links"], _links("IDEA-001")),
         (4, "c3", "idea", "IDEA-001", ["links"], _links("ISS-001")),
         (5, "c5", "issue", "ISS-001", ["links"], {}),
         (6, "c5", "idea", "IDEA-001", ["links"], {})]


def _link_noop(action, *, before, after, target="IDEA-001", w=0, n=0):
    return {"ok": True, "noop": True, "seq": None, "w": w, "n": n, "link": ["ISS-001", target, action],
            "hw_before": before, "hw_after": after}


def test_a_link_noop_must_match_the_store_on_both_ends_at_that_time(tmp_path):
    root = _event_store(tmp_path, LINKS)
    assert _noops_verified(root, [_link_noop("create", before=4, after=4)])     # already linked
    assert _noops_verified(root, [_link_noop("create", before=2, after=4)])     # linked during the call
    assert _noops_verified(root, [_link_noop("remove", before=2, after=2)])     # never linked
    assert _noops_verified(root, [_link_noop("remove", before=6, after=6)])     # already removed
    assert not _noops_verified(root, [_link_noop("create", before=2, after=2)])  # a dropped create
    assert not _noops_verified(root, [_link_noop("create", before=3, after=3)])  # the inverse not there yet
    assert not _noops_verified(root, [_link_noop("create", before=6, after=6)])
    assert not _noops_verified(root, [_link_noop("remove", before=4, after=4)])  # a dropped remove
    assert not _noops_verified(root, [_link_noop("create", before=4, after=4, target="IDEA-002")])
    assert not _noops_verified(root, [dict(_link_noop("create", before=4, after=4), hw_before=None)])
    unknown = dict(_link_noop("remove", before=6, after=6), link=["ISS-009", "IDEA-001", "remove"])
    assert not _noops_verified(root, [unknown])  # an entity the store never held vouches for nothing


def test_a_link_noop_needs_the_inverse_too(tmp_path):
    root = _event_store(tmp_path, LINKS[:3])  # the forward link only
    assert not _noops_verified(root, [_link_noop("create", before=3, after=3)])


def _link_ack(seq, action, w=0, n=0, source="ISS-001", target="IDEA-001"):
    return {"ok": True, "seq": seq, "w": w, "n": n, "link": [source, target, action]}


def _link_checks(root, records):
    result = runner.Result("x", "small")
    runner.check_link_writes(result, root, records, ids=["ISS-001", "IDEA-001"], before=2)
    return {c["check"]: c["ok"] for c in result.data["checks"]}


def test_link_acks_explained_and_final_on_both_ends(tmp_path):
    root = _event_store(tmp_path, LINKS)
    assert _link_checks(root, [_link_ack(3, "create"), _link_ack(5, "remove", n=1)]) == {
        "link_acks_hold": True, "link_changes_explained": True, "final_link_state": True}


def test_an_unacked_commit_that_drops_a_link_is_a_lost_update(tmp_path):
    # The create was acked truthfully, then a commit nobody acked removed it (clobber_link).
    root = _event_store(tmp_path, LINKS)
    checks = _link_checks(root, [_link_ack(3, "create")])
    assert checks["link_changes_explained"] is False


def test_an_ack_the_store_does_not_hold_as_of_its_commit_is_lost(tmp_path):
    root = _event_store(tmp_path, LINKS)
    assert _link_checks(root, [_link_ack(5, "create")])["link_acks_hold"] is False  # c5 removed it
    assert _link_checks(root, [_link_ack(3, "remove")])["link_acks_hold"] is False


def test_a_create_whose_inverse_was_skipped_fails(tmp_path):
    events = LINKS[:3] + [(5, "c5", "issue", "ISS-001", ["links"], {})]
    root = _event_store(tmp_path, events)
    checks = _link_checks(root, [_link_ack(3, "create"), _link_ack(5, "remove", n=1)])
    assert checks["link_acks_hold"] is False


def test_final_link_state_reads_the_inverse(tmp_path):
    events = LINKS[:4] + [(5, "c5", "issue", "ISS-001", ["links"], {})]  # the inverse left behind
    root = _event_store(tmp_path, events)
    checks = _link_checks(root, [_link_ack(3, "create"), _link_ack(5, "remove", n=1)])
    assert checks["final_link_state"] is False


def test_a_create_that_only_repairs_the_inverse_is_explained(tmp_path):
    # backlog_link create of a link the source already holds still syncs a missing inverse: a
    # seq-bearing answer whose commit touches only the target.
    events = [(1, "c1", "issue", "ISS-001", ["id", "links"], dict(_links("IDEA-001"), id="ISS-001")),
              (2, "c2", "idea", "IDEA-001", ["id"], {"id": "IDEA-001"}),
              (3, "c3", "idea", "IDEA-001", ["links"], _links("ISS-001")),
              (4, "c4", "issue", "ISS-001", ["links"], {}),
              (5, "c4", "idea", "IDEA-001", ["links"], {})]
    root = _event_store(tmp_path, events)
    assert _link_checks(root, [_link_ack(3, "create"), _link_ack(4, "remove", n=1)]) == {
        "link_acks_hold": True, "link_changes_explained": True, "final_link_state": True}


def test_a_link_change_by_more_than_its_pair_is_unexplained(tmp_path):
    events = LINKS[:2] + [(3, "c3", "issue", "ISS-001", ["links"], _links("IDEA-001", "IDEA-002")),
                          (4, "c3", "idea", "IDEA-001", ["links"], _links("ISS-001"))]
    root = _event_store(tmp_path, events)
    assert _link_checks(root, [_link_ack(3, "create")])["link_changes_explained"] is False


def test_legacy_links_the_tool_synthesizes_are_not_a_change(tmp_path):
    # An idea without `links` reads its legacy `related_issues` as links; the first write stores them.
    events = [(1, "c1", "issue", "ISS-001", ["id"], {"id": "ISS-001"}),
              (2, "c2", "idea", "IDEA-001", ["id", "related_issues"], {"id": "IDEA-001", "related_issues": ["ISS-002"]}),
              (3, "c3", "issue", "ISS-001", ["links"], _links("IDEA-001")),
              (4, "c3", "idea", "IDEA-001", ["links"], {"links": runner.effective_links(
                  {"related_issues": ["ISS-002"]}, "idea") + [{"type": REL, "target": "ISS-001"}]})]
    root = _event_store(tmp_path, events)
    assert _link_checks(root, [_link_ack(3, "create")])["link_changes_explained"] is True


def test_the_rebuilt_links_must_match_the_store_on_every_entity(tmp_path):
    # keep_last: the store keeps a link its event log says was removed, so the ledger alone would pass.
    ledger = runner.LinkLedger(_event_store(tmp_path, LINKS), ["ISS-001", "IDEA-001"])
    empty = frozenset()
    assert runner.link_store_divergence(ledger, {"ISS-001": empty, "IDEA-001": empty}) == []
    kept = runner.link_store_divergence(ledger, {"ISS-001": frozenset({(REL, "IDEA-001")}), "IDEA-001": empty})
    assert [d["id"] for d in kept] == ["ISS-001"]
    assert [d["id"] for d in runner.link_store_divergence(ledger, {"ISS-001": empty, "IDEA-001": None})] == ["IDEA-001"]
    unknown = runner.link_store_divergence(ledger, {"ISS-001": empty, "IDEA-001": empty, "ISS-009": empty})
    assert [d["id"] for d in unknown] == ["ISS-009"]  # no event rebuilds it: not a match


@pytest.mark.parametrize("mode, workers", [("disjoint", 3), ("same", 2)])
def test_composites_have_at_least_two_members_or_are_refused(mode, workers):
    import random
    inv = dict(LINK_INV, tasks=[f"t-{i}" for i in range(4)])
    for w in range(workers if mode == "same" else 2):
        plan = runner.plan_write("composite", inv, worker=w, workers=workers if mode == "same" else 2, mode=mode,
                                 count=6, warmup=0, rng=random.Random(w), scope="s")
        assert all(len([c for c in op["kw"]["commands"] if not c["arguments"]["id"].startswith("n16-missing")]) >= 2
                   for op in plan)
    with pytest.raises(runner.Refused, match="composite"):
        runner.plan_write("composite", dict(inv, tasks=["t-1"] if mode == "same" else inv["tasks"]), worker=0,
                          workers=workers, mode=mode, count=2, warmup=0, rng=random.Random(1), scope="s")


def test_a_same_mode_link_answer_without_a_sequence_is_a_noop_to_verify(monkeypatch, tmp_path):
    answers = iter(["ok: linked ISS-001 -[relates_to]-> IDEA-001 (no-op, link already present)",
                    "ok: removed 1 link(s) between ISS-001 and IDEA-001 [seq 9]"])
    fake = type("bs", (), {"backlog_link": staticmethod(lambda **_: next(answers))})
    worker = runner.Worker({"root": str(tmp_path)})
    monkeypatch.setattr(worker, "bs", lambda: fake)
    monkeypatch.setattr(worker, "high_water", lambda: 5)
    op = {"t": "tool", "tool": "backlog_link", "label": "write.link", "commit": "optional", "noop_ok": True,
          "kw": {"action": "create", "source": "ISS-001", "target": "IDEA-001", "type": "relates_to"},
          "link": ["ISS-001", "IDEA-001", "create"]}
    first = worker.run(op)
    assert first["ok"] and first["noop"] and first["hw_before"] == 5 and first["hw_after"] == 5
    second = worker.run(dict(op, link=["ISS-001", "IDEA-001", "remove"]))
    assert second["ok"] and not second.get("noop") and second["seq"] == 9


@pytest.mark.real_service_process
@pytest.mark.xdist_group("heavy_processes")
@pytest.mark.parametrize("fault, mode, gate", [
    ("drop_link", "disjoint", "no_lost_ack"),                  # acked with the high-water seq, nothing linked
    ("fake_link_noop", "disjoint", "no_unexpected_errors"),    # an owned pair answered as a no-op, nothing linked
    ("fake_link_noop", "same", "noop_answers_verified"),       # a contended no-op the store never held
    ("clobber_link", "disjoint", "link_changes_explained"),    # acked create, then an unacked commit drops it
    ("clobber_link", "same", "link_changes_explained"),
    ("skip_inverse", "disjoint", "link_acks_hold"),            # the create commits the source side only
    ("skip_inverse", "same", "link_acks_hold"),
    ("keep_last", "disjoint", "links_match_store"),         # the store keeps a link its events say was removed
    ("keep_last", "same", "links_match_store"),
])
def test_a_lost_link_write_fails_the_runner(work, fault, mode, gate):
    done, report = run_runner(work, "write.link", fault, worker=FAULT_WORKER,
                              extra=["--clients", "4", "--modes", mode])
    assert done.returncode == 1, done.stdout[-2000:] + done.stderr[-2000:]
    failed, rows = failed_checks(report, "write.link")
    assert rows[0]["clients"] == 4 and all(r["verdict"] == "fail" for r in rows)
    assert gate in failed, (failed, rows[0]["checks"])


# ── sync judging on the CodeMaestro run: a sync-wide refusal, a grown dataset, the summary ──
from types import SimpleNamespace  # noqa: E402

WIDE = "sync pending: more than 10000 projection files; bounded scan refused"


class _FixedSync:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def sync(self, *, caller_scope, request_id=None, **_):
        self.calls += 1
        return dict(self.answer)


class _SyncDataset:
    name = "cm"

    def __init__(self, root):
        self.root = root

    def inventory(self):
        return {"tasks": ["t-1", "t-2"]}


def _sync_run(samples=3):
    return SimpleNamespace(args=SimpleNamespace(sync_samples=samples, warmup=0), sync_required=0, instrumented=False)


def _sync_harness(monkeypatch, tmp_path, answer, *, files=100, imported=False):
    client = _FixedSync(answer)
    monkeypatch.setattr(runner, "start_coordinator", lambda run, root: client)
    monkeypatch.setattr(runner, "quarantined_files", lambda root: set())
    monkeypatch.setattr(runner, "sync_file_count", lambda root: files)
    markers = []
    monkeypatch.setattr(runner, "edit_task_file", lambda root, task, marker: markers.append(marker))
    monkeypatch.setattr(runner, "body_of", lambda root, task: markers[-1] if imported else "")
    return client, _SyncDataset(tmp_path)


def _checks(data):
    return {c["check"]: c for c in data["checks"]}


def test_a_sync_wide_refusal_is_refused_not_lost(monkeypatch, tmp_path):
    answer = {"state": "pending", "unresolved": [], "notices": [WIDE], "imports": []}
    client, ds = _sync_harness(monkeypatch, tmp_path, answer)
    data = runner.SCENARIOS["sync.dirty"]["fn"](_sync_run(), ds, None, None).finish()
    check = _checks(data)["external_edit_imported"]
    assert data["verdict"] == "fail" and not check["ok"]
    assert check.get("lost", 0) == 0
    assert check["refused"] == 3 and "bounded scan refused" in check["reason"]


def test_lost_means_the_sync_reported_success_and_the_edit_is_missing(monkeypatch, tmp_path):
    for state in ("synchronized", "accepted"):
        client, ds = _sync_harness(monkeypatch, tmp_path, {"state": state, "imports": [], "notices": []})
        check = _checks(runner.SCENARIOS["sync.dirty"]["fn"](_sync_run(), ds, None, None).finish())["external_edit_imported"]
        assert not check["ok"] and check["lost"] == 3
    client, ds = _sync_harness(monkeypatch, tmp_path, {"state": "synchronized", "imports": [], "notices": []},
                               imported=True)
    assert _checks(runner.SCENARIOS["sync.dirty"]["fn"](_sync_run(), ds, None, None).finish())["external_edit_imported"]["ok"]


def test_judge_sync_edit_classifies_every_outcome():
    judge = runner.judge_sync_edit
    assert judge({"state": "pending", "notices": [WIDE]}, True)[0] == "imported"
    assert judge({"state": "synchronized"}, False)[0] == "lost"
    assert judge({"state": "pending", "unresolved": [], "notices": [WIDE]}, False) == ("refused", WIDE)
    assert judge({"state": "pending", "unresolved": ["tasks/t-1.md"],
                  "notices": ["sync pending: tasks/t-1.md: time budget exhausted or coordinator stopping; "
                              "retry the same sync id"]}, False)[0] == "unsettled"
    kind, reason = judge({"state": "pending", "unresolved": ["tasks/t-1.md"],
                          "notices": ["sync pending: tasks/t-1.md: quarantined"]}, False)
    assert kind == "unsettled" and "quarantined" in reason
    # A per-file notice is never sync-wide, even when its reason holds a colon.
    assert runner.sync_wide_refusals({"unresolved": ["tasks/t-1.md"],
                                      "notices": ["sync pending: tasks/t-1.md: a: b"]}) == []


def test_a_no_edit_sync_held_by_a_sync_wide_refusal_does_not_settle(monkeypatch, tmp_path):
    answer = {"state": "pending", "unresolved": [], "notices": [WIDE], "imports": []}
    client, ds = _sync_harness(monkeypatch, tmp_path, answer)
    data = runner.SCENARIOS["sync.no_edits"]["fn"](_sync_run(), ds, None, None).finish()
    check = _checks(data)["no_edit_sync_settles"]
    assert data["verdict"] == "fail" and not check["ok"] and "bounded scan refused" in check["reason"]


@pytest.mark.parametrize("name", ["sync.no_edits", "sync.dirty", "sync.conflict", "sync.checkout",
                                  "sync.missing_files"])
def test_an_over_limit_dataset_is_a_precondition_not_a_verdict(monkeypatch, tmp_path, name):
    def no_coordinator(run, root):
        raise AssertionError("a sync scenario over the file limit must not run")

    monkeypatch.setattr(runner, "copy_project", lambda run, root, label: tmp_path)
    monkeypatch.setattr(runner, "stop_coordinator", lambda root: None)
    monkeypatch.setattr(runner, "start_coordinator", no_coordinator)
    monkeypatch.setattr(runner, "quarantined_files", lambda root: set())
    monkeypatch.setattr(runner, "sync_file_count", lambda root: runner.sync_limit() + 1)
    data = runner.SCENARIOS[name]["fn"](_sync_run(), _SyncDataset(tmp_path), None, None).finish()
    assert data["verdict"] == "precondition", data
    assert str(runner.sync_limit()) in data["precondition"]
    assert runner.exit_status([data]) == 2


def test_sync_file_count_counts_projection_files_on_disk_and_in_the_store(tmp_path):
    import sqlite3
    backlog = tmp_path / ".taskmaster"
    (backlog / "tasks").mkdir(parents=True)
    (backlog / "local").mkdir()
    for ident in ("t-1", "t-2"):
        (backlog / "tasks" / f"{ident}.md").write_text("x", encoding="utf-8")
    with closing(sqlite3.connect(backlog / "local/store.db")) as c:
        c.execute("CREATE TABLE projection(file TEXT PRIMARY KEY)")
        c.executemany("INSERT INTO projection VALUES(?)", [("tasks/t-1.md",), ("tasks/t-3.md",), ("local/x.db",)])
        c.commit()
    assert runner.sync_file_count(tmp_path) == 3  # t-1, t-2 on disk; t-3 known to the store; local/ is not authored
    assert runner.sync_file_count(tmp_path / "missing") == 0


def _meta():
    return {"started": "t", "git_sha": "0" * 10, "python": "3", "sqlite": "3", "machine": "m",
            "samples_required": 200, "warmup": 20}


def _row(scenario, verdict, failed=()):
    return {"scenario": scenario, "dataset": "cm", "clients": 1, "mode": None, "verdict": verdict,
            "checks": [{"check": c, "ok": False} for c in failed] + [{"check": "ok_check", "ok": True}],
            "distributions": {}, "errors": {}}


def test_the_summary_reports_an_instrumented_failure():
    report = {"meta": _meta(), "results": [_row("sync.dirty", "pass")],
              "instrumented": [_row("sync.dirty", "fail", ["external_edit_imported"])], "budgets": [], "skipped": []}
    text = runner.markdown(report)
    correctness = text.split("## Correctness", 1)[1].split("## Latency", 1)[0]
    assert "Uninstrumented pass: 1 of 1 scenario runs pass" in correctness
    assert "Instrumented pass: 0 of 1 scenario runs pass their checks; 1 fail" in correctness
    assert "| instrumented | cm | sync.dirty |" in correctness and "external_edit_imported" in correctness
    instrumented = text.split("## Instrumented pass", 1)[1]
    assert "external_edit_imported" in instrumented
    counts = runner.verdict_counts(report)
    assert counts["instrumented"]["fail"] == 1 and counts["uninstrumented"]["pass"] == 1
    assert counts["fail"] == 1 and runner.exit_status(report["results"] + report["instrumented"]) == 1


def test_each_pass_runs_on_a_fresh_copy_of_the_prepared_dataset(monkeypatch, tmp_path):
    root = tmp_path / "ds-cm" / "native"
    (root / ".taskmaster/tasks").mkdir(parents=True)
    (root / ".taskmaster/tasks/t-1.md").write_text("prepared", encoding="utf-8")
    run = SimpleNamespace(work=tmp_path, guard=lambda r: r)
    monkeypatch.setattr(runner, "stop_coordinator", lambda r: None)
    ds = runner.Dataset("cm", root, None, {"adoption": 1})
    ds._inventory = {"tasks": ["t-1"]}
    datasets = runner.pass_datasets(run, ds, ("uninstrumented", "instrumented"))
    first, second = datasets["uninstrumented"], datasets["instrumented"]
    assert first.root != second.root and root not in (second.root, *second.root.parents)
    (first.root / ".taskmaster/tasks/t-1.md").write_text("grown by the first pass", encoding="utf-8")
    assert (second.root / ".taskmaster/tasks/t-1.md").read_text(encoding="utf-8") == "prepared"
    assert second.inventory() == ds.inventory() and second.name == "cm" and second.info == ds.info
    assert runner.pass_datasets(run, ds, ("uninstrumented",)) == {"uninstrumented": ds}
