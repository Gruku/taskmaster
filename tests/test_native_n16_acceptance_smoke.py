# User intent: prove the N16 acceptance runner itself works end to end - a tiny synthetic dataset,
# real coordinator client processes, correctness checks, budgets, the instrumented metrics pass and
# both output files - before Track E spends hours running the full matrix with it.
"""Smoke test of scripts/native_n16_acceptance.py (tiny sample counts; about a minute)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]

# The runner's clients start a real coordinator service; the process boundary is the point.
pytestmark = [pytest.mark.real_service_process, pytest.mark.xdist_group("heavy_processes")]


def test_runner_smoke_on_small_synthetic_dataset(tmp_path):
    results, summary = tmp_path / "results.json", tmp_path / "summary.md"
    environment = {k: v for k, v in os.environ.items() if k not in ("TASKMASTER_ROOT", "TASKMASTER_METRICS")}
    environment["TASKMASTER_SERVICE_IDLE_SECONDS"] = "5"
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "native_n16_acceptance.py"), "--work", str(tmp_path / "work"),
         "--dataset", "small", "--small-scale", "0.04", "--scenarios", "read.details,write.meta,write.create,core.command",
         "--clients", "2", "--modes", "same", "--samples", "5", "--warmup", "1", "--smoke",
         "--instrumented", "--results", str(results), "--summary", str(summary)],
        cwd=REPO, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    report = json.loads(results.read_text(encoding="utf-8"))

    runs = {(r["scenario"], r["clients"], r["mode"]): r for r in report["results"]}
    assert set(runs) == {("read.details", 1, None), ("core.command", 1, None), ("write.meta", 2, "same"),
                         ("write.create", 2, "same")}
    assert all(r["verdict"] == "pass" for r in report["results"]), [
        (r["scenario"], r["clients"], r["mode"], [c for c in r["checks"] if not c["ok"]], r.get("error"))
        for r in report["results"] if r["verdict"] != "pass"]
    # Correctness gates are recorded per scenario, not inferred from latency.
    meta_checks = {c["check"] for c in runs[("write.meta", 2, "same")]["checks"]}
    assert {"no_lost_ack", "acked_value_is_final", "no_unexpected_errors"} <= meta_checks
    assert "no_reused_id" in {c["check"] for c in runs[("write.create", 2, "same")]["checks"]}
    # Distributions carry the p50/p95/p99/max convention and the worst op.
    distribution = runs[("write.meta", 2, "same")]["distributions"]["write.meta"]
    assert {"samples", "p50_ms", "p95_ms", "p99_ms", "max_ms", "worst"} <= set(distribution)
    assert distribution["samples"] >= 5
    # Every applicable §11 budget row is reported, met or missed.
    budgets = {b["budget"] for b in report["budgets"]}
    assert budgets == {"bounded reads p95 < 100 ms", "DB command core p95 < 50 ms", "simple tool writes p95 < 250 ms"}
    # A smoke run is never budget evidence: every row says so instead of met/missed.
    assert all(b["status"] == "smoke (<200 samples)" for b in report["budgets"])
    # The instrumented pass: its own records, the real metrics module, nothing malformed, files cleaned up.
    instrumented = report["instrumented"]
    assert len(instrumented) == len(report["results"])
    for run in instrumented:
        assert run["metrics"]["available"] is True, run["metrics"]
        assert run["metrics"]["skipped"] == 0 and run["metrics"]["records"] > 0
        assert run["metrics"].get("files_deleted", True)
    commands = [k for r in instrumented for k in r["metrics"]["summary"] if k.startswith("command")]
    assert commands
    text = summary.read_text(encoding="utf-8")
    assert "## Correctness" in text and "## Budgets" in text and "## Instrumented pass" in text
    assert "(SMOKE - not acceptance evidence)" in text.splitlines()[0]
