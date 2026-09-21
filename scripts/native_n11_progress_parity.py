# User intent: N11 exit evidence at real scale — after a few changelog-carrying completions
# on a copied CodeMaestro backlog (legacy) and its natively activated twin, PROGRESS.md must
# be byte-identical, and the dashboard's full read (D6) is timed. Scratchpad copies only.
import difflib
import json
import os
import shutil
import sqlite3
import statistics
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
from taskmaster.native.queries import Repository  # noqa: E402
from taskmaster.native_routing import progress, reads  # noqa: E402
from native_twins import (Twins, activate_native, install_clock, native_connection,  # noqa: E402
                          point_server_at)

LIVE = Path("C:/Users/gruku/Files/Work/CodeMaestro").resolve()
base = S / "cm-base"
legacy, native, out = S / "cm-n11-legacy", S / "cm-n11-native", S / "cm-n11-out"
for target in (base, legacy, native, out):
    assert LIVE not in target.resolve().parents and target.resolve() != LIVE
for target in (legacy, native, out):
    assert not target.exists(), f"{target} exists; move it aside first"
out.mkdir()


class FakeTime:
    """The native throttle's clock, 10 s per reading, so both twins render every call."""

    def __init__(self):
        self.at = 2_000_000.0

    def clock(self):
        now, self.at = self.at, self.at + 10.0
        return now

    def sleep(self, seconds):
        self.at += seconds


mp = pytest.MonkeyPatch()
install_clock(mp)
fake = FakeTime()
mp.setitem(progress.HOOKS, "clock", fake.clock)
mp.setitem(progress.HOOKS, "sleep", fake.sleep)
started = time.time()
shutil.copytree(base, legacy)
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
twins = Twins(mp, legacy, native)


def progress_bytes(root):
    path = root / ".taskmaster" / "local" / "PROGRESS.md"
    return path.read_bytes() if path.exists() else b""


results, failures = [], []


def compare(label):
    left, right = progress_bytes(legacy), progress_bytes(native)
    same = left == right
    results.append((label, same, len(left), len(right)))
    print(f"PROGRESS {'same' if same else 'DIFF'} after {label}: legacy {len(left)} B, native {len(right)} B",
          flush=True)
    if not same:
        slug = "".join(c if c.isalnum() else "-" for c in label)[:60]
        (out / f"{slug}.legacy.md").write_bytes(left)
        (out / f"{slug}.native.md").write_bytes(right)
        diff = list(difflib.unified_diff(left.decode("utf-8", "replace").splitlines(),
                                         right.decode("utf-8", "replace").splitlines(),
                                         "legacy", "native", lineterm="", n=1))
        (out / f"{slug}.diff").write_text("\n".join(diff), encoding="utf-8")
        print("\n".join(diff[:80]), flush=True)


def step(label, tool, **kwargs):
    t0 = time.time()
    try:
        legacy_answer, native_answer = twins.same(tool, **kwargs)
        print(f"ok   {label} ({time.time() - t0:.1f}s): {str(native_answer)[:160]!r}", flush=True)
    except AssertionError as exc:
        failures.append((label, str(exc)[:3000]))
        print(f"DIFF {label} ({time.time() - t0:.1f}s)", flush=True)
    except Exception:
        failures.append((label, traceback.format_exc()[-3000:]))
        print(f"EXC  {label}", flush=True)
    compare(label)


compare("activation (no call yet)")

# ── Completable tasks, chosen on the native twin (fast): ready, and nothing but lane
#    gates in `mandatory` once the lane is express. ─────────────────────────────────
point_server_at(mp, native)
available = bs.backlog_next_available()
ready = [line.split("`")[1] for line in available.split("blocked by dependencies")[0].splitlines()
         if line.startswith("- `")]
chosen = []
for task in ready:
    blockers = json.loads(bs.backlog_context(focus=task, scope="task"))["mandatory"]["blockers"]
    if all(b["kind"] == "gate" for b in blockers):
        chosen.append(task)
    if len(chosen) == 4:
        break
store.reset_for_tests()
print("chosen:", chosen, flush=True)
assert len(chosen) == 4, (ready[:20], available[:600])

for task in chosen:
    step(f"lane express {task}", "backlog_update_task", task_id=task, field="lane", value="express")
    step(f"review-gate {task}", "backlog_record_gate", task_id=task, gate="review-gate", verdict="pass",
         commit_sha="0" * 40)

one, two, three, four = chosen
# CodeMaestro's lifecycle refuses to complete a `todo` task: each is picked first.
step(f"pick {one}", "backlog_pick_task", task_id=one)
step(f"complete {one} structured", "backlog_complete_task", task_id=one, session_title="N11 parity session",
     done="shipped one\n- shipped two", decisions="kept both", issues="", tasks_touched=one)
step(f"pick {two}", "backlog_pick_task", task_id=two)
step(f"complete {two} auto_summary", "backlog_complete_task", task_id=two, done="- stats: 3 files",
     auto_summary=True, tasks_touched=two)
step(f"pick {three}", "backlog_pick_task", task_id=three)
step(f"complete {three} done only", "backlog_complete_task", task_id=three, done="only done")
step(f"pick {four}", "backlog_pick_task", task_id=four)
step(f"complete {four} no changelog", "backlog_complete_task", task_id=four)

for check, name in ((twins.assert_state_matches, "state"), (twins.assert_files_match, "files")):
    try:
        check()
        print("ok   final", name, flush=True)
    except AssertionError as exc:
        failures.append((f"final {name}", str(exc)[:3000]))
        print("DIFF final", name, flush=True)

with closing(sqlite3.connect(native / ".taskmaster" / "local" / "store.db")) as con:
    rows = dict(con.execute("SELECT key, value_json FROM sync_state WHERE key LIKE 'progress.%' "
                            "AND key NOT LIKE 'progress.pending.%'"))
    pending = con.execute("SELECT COUNT(*) FROM sync_state WHERE key LIKE 'progress.pending.%'").fetchone()[0]
applied = json.loads(rows.get("progress.applied", "[]"))
print(f"native: progress.applied {len(applied)} entries, pending rows {pending}, "
      f"seeded {rows.get('progress.seeded')}", flush=True)


# ── D6: the dashboard's full read, timed on each store (no file written) ──────────
def timed(call, repeat=5):
    samples, answer = [], None
    for _ in range(repeat):
        t0 = time.perf_counter()
        answer = call()
        samples.append(time.perf_counter() - t0)
    return samples, answer


existing = progress_bytes(native).decode("utf-8")
with native_connection(native) as connection:
    def native_tree():
        with Repository(connection).snapshot() as snapshot:
            return reads.tree(snapshot)
    tree_s, data = timed(native_tree)
    render_s, _ = timed(lambda: bs._render_progress_dashboard(data, existing, applied))
print(f"native reads.tree: min {min(tree_s) * 1000:.0f} ms, median {statistics.median(tree_s) * 1000:.0f} ms, "
      f"max {max(tree_s) * 1000:.0f} ms; render: median {statistics.median(render_s) * 1000:.0f} ms", flush=True)

point_server_at(mp, legacy)
try:
    opened = bs._store() if hasattr(bs, "_store") else store.open_store(bs._backlog_path())
    legacy_s, legacy_data = timed(lambda: opened._load_dict_from_connection(opened.connection))
    print(f"legacy _load_dict_from_connection: min {min(legacy_s) * 1000:.0f} ms, "
          f"median {statistics.median(legacy_s) * 1000:.0f} ms", flush=True)
except Exception:
    print("legacy timing failed:", traceback.format_exc()[-800:], flush=True)
store.reset_for_tests()

mp.undo()
print(f"\nPROGRESS comparisons: {sum(1 for r in results if r[1])}/{len(results)} identical; "
      f"{len(failures)} call/state failures; total {time.time() - started:.1f}s")
for label, detail in failures:
    print("----", label)
    print(detail)
