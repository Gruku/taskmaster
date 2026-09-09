# SQLite performance investigation on CodeMaestro data

Measured 2026-09-09 on Windows, Python 3.12.9, Taskmaster 6.0.2 at `e9ea119`.

The reported handover delay is reproducible. Five sequential handovers took
10.7–11.3 seconds each. Four simultaneous processes produced three successful
writes and one 30-second writer-lock timeout. Every acknowledged write survived;
this experiment reproduced queue exhaustion, not one writer rolling back another.

The main bottleneck is the unconditional global relationship rebuild. A handover
with no task links triggers 7,157,436 candidate path comparisons under the writer
lock. A diagnostic experiment avoiding that rebuild reduced handovers to 2.0–2.8
seconds. This is evidence for an optimization, not an implemented production fix.

## Method and artifacts

- Reusable harness: `scripts/benchmark_store.py`.
- Source: `C:/Users/gruku/Files/Work/CodeMaestro/.taskmaster`.
- Isolated working copy: `test-results/store-benchmark-20260909/project`.
- Raw baseline: `test-results/store-benchmark-20260909/results.json`.
- Authoritative detailed wall timings: `test-results/store-benchmark-20260909/diagnostics-detailed-v2.json`.
- Counterfactual and equivalence checks: `test-results/store-benchmark-20260909/counterfactual.json`.
- Supporting cProfile output: `handover.prof` and `handover-profile.txt` in that directory.

The harness copies projection files, preserves their mtimes, and uses SQLite's
online backup API on a read-only source connection to include committed WAL data.
It copies local PROGRESS.md and ID reservations, excluding local caches, snapshots,
locks, and database sidecars. Test handlers resolve only to the marked isolated
copy. No Taskmaster handler ran against the original CodeMaestro project.

Store/server SHA-256 hashes match the installed Codex plugin's 6.0.2 files. This
does not prove that every already-running agent process has loaded that version.
The fixture has 2,367 tasks and 3,784 anchor/location path rows. Its backed-up
database is approximately 49 MB and PROGRESS.md approximately 979 KB.

Calls use the real public handlers with transaction/registration wrappers. The
test does not include MCP transport, host queuing, model latency, or `uv` startup.
The four process workers each warm up, then start one handover behind a barrier.
Synthetic handovers have a roughly 1.5 KB body and no task IDs or supersession.

## Measurements

| Operation | Samples | Median | Range |
|---|---:|---:|---:|
| Warm status | 5 | 0.550 s | 0.384–1.070 s |
| Warm task list | 5 | 0.322 s | 0.227–0.928 s |
| Warm handover list | 5 | 0.317 s | 0.223–0.911 s |
| Store diagnostic | 5 | 0.102 s | 0.098–0.188 s |
| Sequential handover | 5 | 10.912 s | 10.703–11.344 s |
| Later detailed handover probes | 3 | 8.252 s | 8.168–8.996 s |
| Skip-related counterfactual | 3 | 2.736 s | 1.963–2.818 s |

The initial open of the copied database took 40.823 seconds. This includes
reconciliation of a separately copied projection under a changed Git generation;
it is neither fresh SQLite adoption nor a clean measure of production cold start.
Copying itself took 4.121 seconds.

Concurrent results: successes at 11.435, 22.850, and 32.791 seconds; failure at
30.196 seconds with `RuntimeError('store busy for 30s; probable holders: ...')`.
The 30-second budget is time waiting for the mutex, not an overall tool deadline,
which explains a successful response after 32 seconds.

Nine baseline handovers committed, including the profiled call. All nine had
unique database IDs and projected files. The raw baseline's
`all_writes_preserved: false` includes the failed attempt; it does not mean an
acknowledged write disappeared. Final checks across baseline and diagnostic runs
found 21 unique synthetic handovers with intact bodies and files, SQLite integrity
`ok`, and zero foreign-key violations.

## Root cause and secondary costs

`Store._refresh_derived` (`taskmaster/store.py:4072`) updates touched rows, then
unconditionally calls `_close_reverse_links` and `_rebuild_related` whenever
`tx._derived_keys` is nonempty (`store.py:4148`). Creating or archiving a handover
satisfies that condition even when its relationship inputs are unchanged.

`_rebuild_related` (`store.py:4188`) deletes all related rows, loads all anchor and
location paths, and compares each path with every later path, including glob
matching. With 3,784 rows, the outer/inner loops visit 3,784 × 3,783 / 2 = 7,157,436
candidate pairs before same-entity exclusions. It also rebuilds handover/task
relationships. This work occurs between acquiring the process-wide writer mutex
and committing (`store.py:1761`); all other writers queue behind it.

Median lightweight spans from three detailed calls:

| Span | Wall time |
|---|---:|
| Writer lock held | 8.086 s |
| Full relationship rebuild | 5.428 s |
| All derived refresh work | 5.709 s |
| Projection scan | 0.613 s |
| Projection export | 0.556 s |
| Schema parsing, five calls per handover | 0.495 s |
| Compatibility dict diff | 0.221 s |
| Progress regeneration | 0.145 s |
| Reverse-link closure | 0.051 s |

Spans overlap and must not be summed. The relationship rebuild is roughly 66% of
the detailed median call. PROGRESS.md is not the primary bottleneck here. Repeated
whole-backlog loading/copying, YAML schema parsing, scans, and exports are secondary
costs. Variation between the baseline and later probes means speedup estimates
are approximate, not a controlled throughput guarantee.

For the counterfactual, only `_rebuild_related` was replaced in the benchmark
process. After each successful write, the original algorithm ran inside a rolled-
back transaction, and all six relationship columns were compared as a sorted
multiset. All three comparisons matched exactly: 39,027 rows. This proves that
these three synthetic handovers owed no relationship change. It does not justify
skipping rebuilds for arbitrary edits, links, anchors, archives, or imports.

The cProfile run increased latency to 17.240 seconds and produced inconsistent
cumulative thread/context-manager attribution. Use the explicit `perf_counter`
spans above for cost attribution, not the apparent heartbeat/join hotspot in that
profile. An earlier diagnostic file had an incorrect relative reporting path;
`diagnostics-detailed-v2.json` supersedes it.

## Recommended implementation order

1. Track changes to relationship inputs separately from general entity changes.
   Skip relationship rebuilding only when normalized anchors, locations, and
   handover task memberships are provably unchanged. Cover import, archive,
   deletion, and rollback paths as well as normal tool writes.
2. Replace full all-pairs matching with grouped exact-path matching and bounded
   glob comparisons, then incremental updates for affected entities. Preserve
   pair weights, duplicate semantics, and handover co-membership; compare against
   the current full rebuild as an oracle on realistic mixed fixtures.
3. Reduce remaining whole-backlog work: targeted row transactions for handovers,
   schema parse reuse with safe invalidation, and fewer projection enumeration
   passes. Preserve Git checkout detection and hand-edit import guarantees.
4. Rerun this fixture with four and eight simultaneous writers and readers. Require
   every acknowledged write to persist and equivalent relationships/projections.
   Raising the 30-second timeout alone would mask the queue, not remove its cost.

No runtime code, installed plugin, or live CodeMaestro project was changed. This
investigation does not establish a speed regression relative to pre-SQLite
Taskmaster: that older implementation was not benchmarked side by side.

Reproduce from the repository with its working Python environment:

```powershell
.venv/Scripts/python.exe scripts/benchmark_store.py --source C:/Users/gruku/Files/Work/CodeMaestro --output test-results/store-benchmark-NEW --repeat 5 --writers 4
.venv/Scripts/python.exe scripts/benchmark_store.py --diagnose-copy test-results/store-benchmark-NEW/project --output test-results/store-benchmark-NEW/diagnostics.json
.venv/Scripts/python.exe scripts/benchmark_store.py --diagnose-copy test-results/store-benchmark-NEW/project --skip-related --output test-results/store-benchmark-NEW/counterfactual.json
```

Use a new output directory for every snapshot. Do not overlap diagnostic and
baseline runs on one copy. The raw fixture stays in ignored `test-results/` and
contains private project content; only the harness and this report are new source
files. Syntax compilation and `git diff --check` passed.
