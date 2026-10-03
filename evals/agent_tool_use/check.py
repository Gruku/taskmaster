# User intent: grade the mutating (and must-not-mutate) scenarios mechanically — did the run's
# private backlog copy end up in the state the request asked for, and did the agent call or avoid
# the tools that matter — so the orchestrator does not have to read stores by hand.
"""Run a scenario's automatic checks against a run's private backlog copy and its action log.

    uv run evals/agent_tool_use/check.py RUN [RUN ...] [--scenario ID] [--home DIR]

The scenario is taken from the run id's `-sNN` suffix (e.g. `b2-s18` -> `s18-...`) unless
--scenario names it. Checks come from the rendered ground truth (`<home>/ground_truth.json`):

    sql + expect        first column of the first row equals `expect`
    sql + expect_min    first column of the first row is >= `expect_min`
    sql + expect_rows   all rows equal `expect_rows`
    log_called          a tool that must have been called without error (optional `args` subset)
    log_not_called      a tool that must not have been called successfully
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evalpaths  # noqa: E402


def successful_calls(home: Path, run: str) -> list[dict]:
    path = evalpaths.log_path(home, run)
    if not path.exists():
        return []
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [e for e in entries if e["action"] == "call" and not e["error"]]


def run_check(check: dict, con: sqlite3.Connection | None, calls: list[dict]) -> tuple[bool, str]:
    if "sql" in check:
        if con is None:
            return False, "no backlog copy for this run"
        try:
            rows = [list(row) for row in con.execute(check["sql"])]
        except sqlite3.Error as exc:
            return False, f"sql error: {exc}"
        if "expect_rows" in check:
            return rows == check["expect_rows"], f"rows={json.dumps(rows, ensure_ascii=False)}"
        value = rows[0][0] if rows and rows[0] else None
        if "expect_min" in check:
            return value is not None and value >= check["expect_min"], f"got {value!r}, want >= {check['expect_min']!r}"
        return value == check["expect"], f"got {value!r}, want {check['expect']!r}"
    if "log_called" in check:
        wanted = check.get("args") or {}
        hits = [c for c in calls if c["tool"] == check["log_called"]
                and all((c.get("args") or {}).get(k) == v for k, v in wanted.items())]
        return bool(hits), f"{len(hits)} matching successful call(s)"
    if "log_not_called" in check:
        hits = [c for c in calls if c["tool"] == check["log_not_called"]]
        return not hits, f"{len(hits)} successful call(s)"
    return False, "unknown check type"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--scenario", help="scenario id (or its sNN prefix); default: from the run id's -sNN suffix")
    parser.add_argument("--home")
    ns = parser.parse_args()
    home = evalpaths.home(ns.home)
    truth = {g["id"]: g for g in json.loads((home / "ground_truth.json").read_text(encoding="utf-8"))["ground_truth"]}
    failed = 0
    for run in ns.runs:
        evalpaths.check_run_id(run)
        wanted = ns.scenario or (re.search(r"(s\d+)$", run) or [None, ""])[1]
        scenario = next((g for sid, g in truth.items() if wanted and (sid == wanted or sid.startswith(wanted + "-"))), None)
        if scenario is None:
            print(f"== {run}: no scenario matches {wanted!r}; pass --scenario")
            failed += 1
            continue
        checks = scenario.get("checks") or []
        print(f"== {run}  [{scenario['id']}]" + ("" if checks else "  no automatic checks - grade the answer against the ground truth"))
        db = evalpaths.run_dir(home, run) / ".taskmaster" / "local" / "store.db"
        con = sqlite3.connect(db) if db.exists() else None
        calls = successful_calls(home, run)
        for check in checks:
            ok, detail = run_check(check, con, calls)
            failed += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {check.get('desc', '')}  ({detail})")
        if con is not None:
            con.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
