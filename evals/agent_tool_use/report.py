# User intent: turn a test agent's action log into something gradeable at a glance — which tool
# descriptions it read, which calls it made with which arguments, which came back empty or as an
# error — so the orchestrator can compare each run with the ground truth without reading raw JSON.
"""Report on agent tool-use eval runs.

    uv run evals/agent_tool_use/report.py RUN [RUN ...] [--full] [--json] [--home DIR]
    uv run evals/agent_tool_use/report.py --all
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evalpaths  # noqa: E402


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarise(run: str, entries: list[dict]) -> dict:
    describes = [e for e in entries if e["action"] == "describe"]
    calls = [e for e in entries if e["action"] == "call"]
    described = {e["tool"] for e in describes if not e["error"]}
    return {
        "run": run,
        "first_ts": entries[0]["ts"] if entries else "",
        "last_ts": entries[-1]["ts"] if entries else "",
        "names_requests": sum(1 for e in entries if e["action"] == "names"),
        "describes": [{"tool": e["tool"], "unknown": e["error"]} for e in describes],
        "calls": [{
            "n": i,
            "tool": e["tool"],
            "args": e["args"],
            "error": e["error"],
            "empty": e["empty"],
            "harness_error": bool(e.get("harness_error")),
            "not_loaded": bool(e.get("not_loaded")),
            "result_chars": e["result_chars"],
            "result": e["result"],
        } for i, e in enumerate(calls, 1)],
        "counts": {
            "describes": len(describes),
            "distinct_tools_described": len(described),
            "calls": len(calls),
            "distinct_tools_called": len({e["tool"] for e in calls}),
            "error_calls": sum(1 for e in calls if e["error"]),
            "empty_calls": sum(1 for e in calls if e["empty"]),
            "calls_before_describe": sum(1 for e in calls if e.get("not_loaded")),
            "harness_errors": sum(1 for e in entries if e.get("harness_error")),
        },
    }


def render(summary: dict, full: bool) -> str:
    lines = [f"== run {summary['run']}  ({summary['first_ts']} .. {summary['last_ts']})",
             f"names requested: {summary['names_requests']}x"]
    tools = [d["tool"] + (" [UNKNOWN TOOL]" if d["unknown"] else "") for d in summary["describes"]]
    lines.append(f"describes ({len(tools)}): " + (", ".join(tools) or "none"))
    lines.append(f"calls ({len(summary['calls'])}):")
    for c in summary["calls"]:
        flags = "".join(f" [{label}]" for label, on in (
            ("ERROR", c["error"]), ("EMPTY", c["empty"]), ("HARNESS", c["harness_error"]),
            ("REFUSED: NOT DESCRIBED FIRST", c["not_loaded"])) if on)
        lines.append(f"  {c['n']:>2}. {c['tool']} {json.dumps(c['args'], ensure_ascii=False)}{flags}")
        text = c["result"] if full else " ".join(c["result"].split())[:200]
        prefix = "      " if full else "      -> "
        lines.append(prefix + text.replace("\n", "\n      ") + f"  ({c['result_chars']} chars)")
    lines.append("counts: " + ", ".join(f"{k}={v}" for k, v in summary["counts"].items()))
    return "\n".join(lines)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="*", help="run ids")
    parser.add_argument("--all", action="store_true", help="every run that has a log")
    parser.add_argument("--full", action="store_true", help="print each logged result in full (up to the logged 2000 chars)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--home", help="eval home (default: TM_EVAL_HOME or <temp>/tm-agent-tool-use-eval)")
    ns = parser.parse_args()
    home = evalpaths.home(ns.home)
    runs = sorted(p.stem for p in (home / "logs").glob("*.jsonl")) if ns.all else ns.runs
    if not runs:
        parser.error("give run ids or --all")

    manifest = home / "seed_manifest.json"
    seeded_on = json.loads(manifest.read_text(encoding="utf-8")).get("seeded_on") if manifest.exists() else None
    summaries, missing = [], []
    for run in runs:
        path = evalpaths.log_path(home, evalpaths.check_run_id(run))
        if path.exists():
            summaries.append(summarise(run, load(path)))
        else:
            missing.append(run)
    if ns.json:
        print(json.dumps({"seeded_on": seeded_on, "runs": summaries, "missing": missing}, indent=2, ensure_ascii=False))
    else:
        if seeded_on != date.today().isoformat():
            print(f"WARNING: backlog was seeded on {seeded_on}, today is {date.today().isoformat()} - "
                  "relative dates in the scenarios no longer match; reseed with --force.\n")
        print(f"ground truth: {home / 'ground_truth.json'}\n")
        print("\n\n".join(render(s, ns.full) for s in summaries))
        for run in missing:
            print(f"\n== run {run}: no log at {evalpaths.log_path(home, run)}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
