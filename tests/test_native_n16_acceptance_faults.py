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
def work(tmp_path_factory):
    work = tmp_path_factory.mktemp("n16-faults")
    done, report = run_runner(work, "write.prose")  # prepares the shared dataset; a clean run must pass
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert report["results"][0]["verdict"] == "pass"
    return work


@pytest.mark.real_service_process
@pytest.mark.xdist_group("heavy_processes")
@pytest.mark.parametrize("fault, scenario, gate", [
    ("drop_writes", "write.meta", "no_lost_ack"),           # acked with the high-water seq, nothing written
    ("strip_seq", "write.prose", "no_unexpected_errors"),   # real write, no sequence in the answer
    ("swallow", "write.meta", "no_unexpected_errors"),      # "Could not update: ..." is not a success
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
    ("No change to `t-1` field `priority` — already `low`", {"success": runner.SUCCESS["backlog_update_task"], "noop_ok": True}, True),
    ("No change to `t-1` field `priority` — already `low` [seq 4]", {"success": runner.SUCCESS["backlog_update_task"], "noop_ok": True}, False),
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
