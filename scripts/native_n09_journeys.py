# User intent: N09 exit evidence at real scale — run the agent journeys, old path vs new,
# on a copied CodeMaestro backlog (legacy) and its natively activated twin, and time
# scoped `backlog_changes_since` with query plans to decide whether it needs an index.
# Works only on scratchpad copies of cm-base; never on the live CodeMaestro project.
import json
import os
import shutil
import sqlite3
import sys
import time
from contextlib import closing, contextmanager
from pathlib import Path

WT = Path(__file__).resolve().parents[1]
# SCRATCH holds cm-base, the marked scratchpad copy; the run writes its twins beside it.
S = Path(os.environ["SCRATCH"]).resolve()
sys.path[:0] = [str(WT), str(WT / "tests")]

import pytest  # noqa: E402

from taskmaster import backlog_server as bs  # noqa: E402
from taskmaster import store  # noqa: E402
from taskmaster.native import cursors  # noqa: E402
import agent_journeys as aj  # noqa: E402
from native_twins import activate_native, install_clock, point_server_at  # noqa: E402

LIVE = Path("C:/Users/gruku/Files/Work/CodeMaestro").resolve()
base = S / "cm-base"
legacy, native, runs = S / "cm-n09-legacy", S / "cm-n09-native", S / "cm-n09-runs"
for target in (base, legacy, native, runs):
    assert LIVE not in target.resolve().parents and target.resolve() != LIVE
for target in (legacy, native, runs):
    assert not target.exists(), f"{target} exists; move it aside first"

mp = pytest.MonkeyPatch()
install_clock(mp)
started = time.time()
shutil.copytree(base, legacy)
# The store is the authority (6.x); the files are its projection. Keep it.
point_server_at(mp, legacy)
bs.backlog_status()
store.reset_for_tests()
print(f"legacy adopted in {time.time() - started:.1f}s", flush=True)
shutil.copytree(legacy, native)
for leftover in native.rglob("*.tmp.*"):
    os.remove(leftover)
activate_native(native)
store.reset_for_tests()
print(f"native activated at {time.time() - started:.1f}s", flush=True)


def db(root):
    return closing(sqlite3.connect(root / ".taskmaster" / "local" / "store.db"))


# ── Pick real ids: todo tasks in active epics, from the legacy rows ─────────
point_server_at(mp, legacy)
available = bs.backlog_next_available()
store.reset_for_tests()
ready = [line.split("`")[1] for line in available.split("blocked by dependencies")[0].splitlines()
         if line.startswith("- `")]
assert len(ready) >= 3, available[:500]
point_server_at(mp, legacy)
data = bs._load()
epic_of = {str(task.get("id")): str(epic.get("id")) for epic in data["epics"] for task in epic.get("tasks", [])}
statuses = {str(task.get("id")): task.get("status") for epic in data["epics"] for task in epic.get("tasks", [])}
store.reset_for_tests()
pick, resume, close = ready[0], ready[1], ready[2]
busy = {epic_of[pick], epic_of[resume], epic_of[close]}
others = sorted(t for t, st in statuses.items() if st == "todo" and epic_of[t] not in busy)[:6]
with db(legacy) as con:
    events = con.execute("SELECT COUNT(*), MAX(seq) FROM changes").fetchone()
    counts = dict(con.execute("SELECT kind, COUNT(*) FROM entities WHERE deleted=0 GROUP BY kind"))
print("entities:", json.dumps(counts), f"tasks in epics {len(statuses)}", flush=True)
assert counts.get("task", 0) > 2000, "not the CodeMaestro backlog: the store did not come along"
ids = {"pick": pick, "resume": resume, "resume_epic": epic_of[resume],
       "resume_thread": f"n09-journey-{resume}", "close": close, "noise": others}

# What the close must clear: the lane's pending gates and every open bug filed on it.
point_server_at(mp, legacy)
close_context = json.loads(bs.backlog_context(focus=close, scope="task", include=["bugs"]))
store.reset_for_tests()
ids["close_gates"] = [b["id"] for b in close_context["mandatory"]["blockers"] if b["kind"] == "gate"]
ids["close_open_bugs"] = [bug for _kind, bug in sorted(aj.close_bugs(close_context))]
print("ids:", json.dumps(ids), f"legacy change rows {events[0]}, max seq {events[1]}", flush=True)


def fresh_from(source, side):
    @contextmanager
    def fresh(label):
        target = runs / f"{side}-{label}"
        shutil.copytree(source, target)
        for leftover in target.rglob("*.tmp.*"):
            os.remove(leftover)
        point_server_at(mp, target)
        try:
            yield
        finally:
            store.reset_for_tests()
            shutil.rmtree(target, ignore_errors=True)
    return fresh


results = {}
for journey in aj.JOURNEYS:
    for side, root in (("legacy", legacy), ("native", native)):
        measured = aj.measure(journey, ids, fresh_from(root, side))
        results[(journey.name, side)] = measured
        print(f"[{side}] {measured.row()}; old {sum(measured.old.seconds):.2f}s, "
              f"new {sum(measured.new.seconds):.2f}s", flush=True)
        for label, trace in (("old", measured.old), ("new", measured.new)):
            print(f"    {label}: " + ", ".join(f"{tool.removeprefix('backlog_')} {size}B {sec:.2f}s"
                                             for (tool, size), sec in zip(trace.calls, trace.seconds)),
                  flush=True)
        if measured.required:
            missing_old = sorted(measured.required - measured.covered_old)
            print(f"    required {sorted(measured.required)}; old path does not name {missing_old}",
                  flush=True)
            unnamed = measured.required - measured.named_new
            assert not unnamed, f"new path does not name {unnamed}"
    legacy_m, native_m = results[(journey.name, "legacy")], results[(journey.name, "native")]
    if journey.focus and journey.name != "resume":
        assert legacy_m.required == native_m.required, (journey.name, legacy_m.required, native_m.required)


# ── Scoped change feed: timings and query plans ─────────────────────────────
def timed(call, repeat=5):
    best, answer = None, None
    for _ in range(repeat):
        t0 = time.perf_counter()
        answer = call()
        elapsed = time.perf_counter() - t0
        best = elapsed if best is None else min(best, elapsed)
    return best, answer


with db(native) as con:
    high = con.execute("SELECT MAX(seq), COUNT(*) FROM domain_events").fetchone()
print(f"native domain_events: {high[1]} rows, max seq {high[0]}", flush=True)
busy_epic = ids["resume_epic"]
tail = max(0, high[0] - 200)
cases = [
    ("unscoped, from 0", dict(since_seq=0)),
    ("unscoped, last 200", dict(since_seq=tail)),
    ("kinds=[bug], from 0", dict(since_seq=0, kinds=["bug"])),
    ("ids=[pick], from 0", dict(since_seq=0, ids=[pick])),
    (f"epic={busy_epic}, from 0", dict(since_seq=0, epic=busy_epic)),
    (f"epic={busy_epic}, last 200", dict(since_seq=tail, epic=busy_epic)),
    ("ids=[pick], last 200", dict(since_seq=tail, ids=[pick])),
    ("kinds=[bug], limit 500, from 0", dict(since_seq=0, kinds=["bug"], limit=500)),
]
for side, root in (("legacy", legacy), ("native", native)):
    point_server_at(mp, root)
    for label, kwargs in cases:
        seconds, answer = timed(lambda: bs.backlog_changes_since(**kwargs))
        parsed = json.loads(answer)
        count = len(parsed.get("commits", parsed.get("changes", [])))
        print(f"[{side}] changes_since {label}: {seconds * 1000:.1f} ms, {count} items, "
              f"{len(answer.encode())}B, more={parsed.get('more')}", flush=True)
    store.reset_for_tests()

# The plans, for the native query shapes the tool issues.
with db(native) as con:
    from taskmaster.native.queries import Snapshot  # noqa: F401  (documents the source)
    scopes = {
        "kinds": (["e.seq>?", "e.kind IN (?)"], [0, "bug"]),
        "ids": (["e.seq>?", "e.id IN (?)"], [0, pick]),
        "epic": (["e.seq>?", cursors.epic_condition(
            "domain_events", "e", "SELECT json_extract(t.epic_json,'$') FROM entity_core c2 "
            "JOIN task_operational t USING(entity_key) WHERE c2.kind='task' AND c2.public_id=e.id")],
            [0] + [busy_epic] * 4),
    }
    for name, (conditions, args) in scopes.items():
        source = ("FROM domain_events e LEFT JOIN command_commits c USING(commit_key) WHERE "
                  + " AND ".join(conditions))
        sql = ("SELECT DISTINCT COALESCE(c.first_seq,e.seq) first_seq, COALESCE(c.final_seq,e.seq) final_seq "
               + source + " ORDER BY first_seq LIMIT 101")
        plan = [row[-1] for row in con.execute("EXPLAIN QUERY PLAN " + sql, args)]
        print(f"plan native {name}: " + " | ".join(plan), flush=True)
with db(legacy) as con:
    for name, where, args in (("kinds", "seq>? AND kind IN (?)", [0, "bug"]),
                              ("ids", "seq>? AND id IN (?)", [0, pick])):
        plan = [row[-1] for row in con.execute(
            f"EXPLAIN QUERY PLAN SELECT * FROM changes WHERE {where} ORDER BY seq LIMIT 101", args)]
        print(f"plan legacy {name}: " + " | ".join(plan), flush=True)

mp.undo()
print(f"\ntotal {time.time() - started:.1f}s")
