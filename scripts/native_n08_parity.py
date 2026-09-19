# User intent: N08 exit evidence — run the routed tools against a copied CodeMaestro
# backlog (legacy) and its natively activated twin, comparing answers, state and files.
# Works only on scratchpad copies of cm-base; never on the live CodeMaestro project.
import json
import os
import shutil
import sqlite3
import sys
import time
import traceback
from contextlib import closing
from pathlib import Path

WT = Path(__file__).resolve().parents[1]
# SCRATCH holds cm-base, the marked scratchpad copy; the run writes its twins beside it.
S = Path(os.environ["SCRATCH"]).resolve()
sys.path[:0] = [str(WT), str(WT / "tests")]

import pytest  # noqa: E402

from taskmaster import backlog_server as bs  # noqa: E402
from taskmaster import store  # noqa: E402
from native_twins import (Twins, activate_native, committed, install_clock,  # noqa: E402
                          point_server_at)

LIVE = Path("C:/Users/gruku/Files/Work/CodeMaestro").resolve()
base = S / "cm-base"
legacy, native = S / "cm-n08-legacy", S / "cm-n08-native"
for target in (legacy, native):
    assert LIVE not in target.resolve().parents and target.resolve() != LIVE
    assert not target.exists(), f"{target} exists; move it aside first"

mp = pytest.MonkeyPatch()
install_clock(mp)
started = time.time()
shutil.copytree(base, legacy)
point_server_at(mp, legacy)
print("adopt:", bs.backlog_status()[:200].replace("\n", " | "), flush=True)
store.reset_for_tests()
print(f"legacy adopted in {time.time() - started:.1f}s", flush=True)
shutil.copytree(legacy, native)
for leftover in native.rglob("*.tmp.*"):
    os.remove(leftover)
activate_native(native)
print(f"native activated at {time.time() - started:.1f}s", flush=True)
twins = Twins(mp, legacy, native)

with closing(sqlite3.connect(legacy / ".taskmaster" / "local" / "store.db")) as con:
    def ids(kind, where="", limit=3):
        return [r[0] for r in con.execute(
            f"SELECT id FROM entities WHERE kind=? AND deleted=0 {where} ORDER BY id LIMIT {limit}", (kind,))]
    tasks = ids("task", "AND archived=0 AND json_extract(doc,'$.status') IN ('todo','in-progress')", 4)
    epics, phases = ids("epic", "AND archived=0", 2), ids("phase", "", 2)
    bugs, issues, ideas = ids("bug"), ids("issue"), ids("idea")
    decisions, handovers, areas, notes = ids("decision"), ids("handover", "", 2), ids("area", "", 2), ids("note")
    anchored = [r[0] for r in con.execute(
        "SELECT path FROM entity_paths WHERE match_kind='exact' AND source IN ('anchors','location') LIMIT 3")]
print("sample:", tasks, epics, phases, bugs, issues, ideas, decisions, handovers, areas, notes, anchored, flush=True)

calls = [("backlog_status", {}), ("backlog_status", {"verbose": True}), ("backlog_list_tasks", {}),
         ("backlog_list_tasks", {"verbose": True, "limit": 0}), ("backlog_next_available", {}),
         ("backlog_phase_status", {}), ("backlog_bug_list", {}), ("backlog_bug_list", {"include_archive": True}),
         ("backlog_issue_list", {}), ("backlog_issue_list", {"verbose": True}), ("backlog_idea_list", {}),
         ("backlog_decision", {"action": "list", "status": "all"}), ("backlog_note", {"action": "list"}),
         ("backlog_handover_list", {}), ("backlog_handover_list", {"verbose": True, "limit": 0}),
         ("backlog_thread_list", {"include_closed": True}), ("backlog_continuity_items", {}),
         ("backlog_last_session", {}), ("backlog_area_list", {}), ("backlog_link", {"action": "query"}),
         ("backlog_link", {"action": "validate"}), ("backlog_project_get", {}),
         ("backlog_project_ship_order", {}), ("backlog_linear", {"action": "list"}),
         ("backlog_linear", {"action": "status"}), ("backlog_search", {"query": "viewer"}),
         ("backlog_search", {"query": "crash", "kinds": ["bug", "issue"]}), ("viewer_prefs_get", {}),
         ("backlog_bug_pattern_scan", {})]
for t in tasks:
    calls += [("backlog_get_task", {"task_id": t}), ("backlog_get_task", {"task_id": t, "verbose": True, "expand_links": True}),
              ("backlog_dependencies", {"task_id": t}), ("backlog_task_pipeline", {"task_id": t}),
              ("backlog_blast_radius", {"task_id": t})]
calls += [("backlog_epic_status", {"epic_id": e}) for e in epics]
calls += [("backlog_bug_get", {"bug_id": b, "verbose": True}) for b in bugs]
calls += [("backlog_issue_get", {"issue_id": i, "verbose": True, "expand_links": True}) for i in issues]
calls += [("backlog_idea_get", {"idea_id": i, "verbose": True}) for i in ideas]
calls += [("backlog_decision", {"action": "get", "decision_id": d}) for d in decisions]
calls += [("backlog_handover_get", {"handover_id": h, "verbose": True}) for h in handovers]
calls += [("backlog_thread_resume", {"ref": h}) for h in handovers]
calls += [("backlog_area_get", {"area_id": a}) for a in areas]
calls += [("backlog_note", {"action": "get", "note_id": n}) for n in notes]
if tasks:
    calls += [("backlog_note", {"action": "create", "text": "N08 parity note"}),
              ("backlog_update_task", {"task_id": tasks[0], "tldr": "N08 parity tldr"}),
              ("backlog_idea_create", {"title": "N08 parity idea", "body": f"about {tasks[0]}"}),
              ("backlog_bug_create", {"title": "N08 parity bug", "found_in": tasks[0]}),
              ("backlog_pick_task", {"task_id": tasks[1]}),
              ("backlog_status", {})]

failures, timings = [], []
for tool, kwargs in calls:
    label = f"{tool}({json.dumps(kwargs)[:80]})"
    try:
        t0 = time.time()
        legacy_answer, native_answer = twins.same(tool, **kwargs)
        timings.append((label, time.time() - t0))
        if str(native_answer).startswith("Error: `"):
            failures.append((label, "native refused: " + str(native_answer)[:200]))
        print("ok  ", label, flush=True)
    except AssertionError as exc:
        failures.append((label, str(exc)[:1500]))
        print("DIFF", label, flush=True)
    except Exception:
        failures.append((label, traceback.format_exc()[-1500:]))
        print("EXC ", label, flush=True)

for check, name in ((twins.assert_state_matches, "state"), (twins.assert_files_match, "files")):
    try:
        check()
        print("ok   final", name, flush=True)
    except AssertionError as exc:
        failures.append((f"final {name}", str(exc)[:1500]))
        print("DIFF final", name, flush=True)

# Hooks, read-only, against both copies.
import importlib.util  # noqa: E402


def hook(name):
    spec = importlib.util.spec_from_file_location(f"cm_{name}", WT / "hooks" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resurface, gate = hook("edit_resurface"), hook("merge_gate_decide")
for rel in anchored:
    lines = [resurface.format_line(rel, resurface.resolve(root / ".taskmaster" / "local" / "store.db", rel))
             for root in (legacy, native)]
    (print if lines[0] == lines[1] else failures.append)(("hook resurface " + rel, lines))
with closing(sqlite3.connect(legacy / ".taskmaster" / "local" / "store.db")) as con:
    branches = [r[0] for r in con.execute("SELECT DISTINCT json_extract(doc,'$.branch') FROM entities WHERE kind='task' "
                                          "AND archived=0 AND deleted=0 AND json_extract(doc,'$.branch') IS NOT NULL LIMIT 5")]
for branch in branches + ["no/such-branch"]:
    verdicts = [gate.decide_from_store(root / ".taskmaster" / "local" / "store.db", branch, root) for root in (legacy, native)]
    (print if verdicts[0] == verdicts[1] else failures.append)(("hook gate " + branch, verdicts))

mp.undo()
print(f"\n{len(calls)} tool calls, {len(anchored)} resurface paths, {len(branches) + 1} gate branches; "
      f"{len(failures)} failures; total {time.time() - started:.1f}s")
for label, detail in failures:
    print("----", label)
    print(detail)
