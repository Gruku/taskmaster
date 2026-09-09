# Taskmaster performance audit and optimization roadmap

Date: 2026-09-09. Implementation examined: 6.0.2, `e9ea119`.
This extends `2026-09-09-store-performance.md`. All test mutations used copies
of the earlier isolated CodeMaestro fixture. No production implementation or
installed plugin was changed.

## Findings that should drive the work

Taskmaster has three different forms of unnecessary work:

1. **Writes maintain much more derived state than the edit changes.** Updating a
   next step, creating a note, or writing an unlinked handover rebuilds the global
   relationship graph. The one-writer queue turns that cost into timeouts.
2. **Reads reconstruct much more data than the answer needs.** A 473-byte task
   answer took 0.52 seconds. Direct task lookup was below 1 ms. Indexed search
   took 0.35 seconds around a roughly 2.7 ms FTS query.
3. **The viewer and some agent tools return far too much data by default.** The
   actual viewer backlog response was 16.21 MB, and an unchanged conditional GET
   still returned all of it. The default continuity result was 856 KB.

SQLite is already capable of the small reads this application needs. Keep it;
reduce the amount of application work around it before changing storage engines
or weakening durability.

## Coverage and measurement boundaries

The new harness is `scripts/benchmark_performance_audit.py`. It exercises status,
lists, detail reads, dependencies, search, SQL query, continuity, validation,
viewer data and actual loopback HTTP, normal writes, batching, invalid input,
hooks, cached reads, projection checks, adoption, query plans, equivalent graph
algorithms, and concurrent writers/readers.

Evidence is under ignored `test-results/`:

- `performance-audit-20260909/suite.json`: broad current-implementation baseline.
- `store-benchmark-20260909/algorithm-comparison.json`: graph calculation variants.
- `performance-audit-20260909/experiments.json`: full write prototype, temporary
  index comparisons, and actual HTTP response sizes.
- `performance-audit-20260909/optimized-concurrency.json`: four writers with the
  graph prototype, plus concurrent status reads.
- `performance-audit-20260909/baseline-concurrency-control.json`: unmodified
  two-writer control and fresh-connection integrity checks.
- `performance-audit-20260909/matching-hooks.json`: edit hook on a path with 38
  exact open claims, including a memoized repeat.
- `performance-adoption-20260909-v2/adoption.json`: fresh DB built from v4 files.

Most operations have three samples; SQL microbenchmarks have twenty. Reported
medians are observations, not p95 estimates or latency guarantees. The machine
was doing other work, and adoption overlapped some later experiments on a
different copy. Comparisons across runs are approximate. The fixture includes
synthetic handovers left by the earlier investigation.

Not measured: model inference, MCP host queueing/transport, browser rendering or
heap use, remote Linear requests, network filesystems, crash/power-loss recovery,
and a pre-SQLite release comparison. Full validation ran without CodeMaestro's
source/docs tree, so its timing is useful but its findings are not a project
acceptance result. No remote sync was triggered.

## Baseline across the application

| Operation | Median or single sample | Output bytes, first sample |
|---|---:|---:|
| Status | 0.359 s | 133,104 |
| Task list | 0.267 s | 20,212 |
| Single task | 0.519 s | 473 |
| Dependencies | 0.220 s | 450 |
| Bug / issue / decision / note lists | 0.212–0.227 s | 933–18,849 |
| Threads | 0.220 s | 4,983 |
| Continuity items | 0.259 s | 855,872 |
| Next available task | 0.222 s | 8,299 |
| Phase status | 0.252 s | 65,347 |
| Search | 0.355 s | 2,174 |
| SQL query tool, counts by kind | 0.141 s | 165 |
| Store diagnostic | 0.081 s | 3,275 |
| Derived-index diagnostic | 0.165 s | 287 |
| Linear status, empty queue | 0.064 s | 160 |
| Validation, single run | 0.992 s | 189,054 |
| Viewer task data builder | 0.265 s | 3,246 |
| Viewer related data builder | 0.336 s | 455 |
| Task next-step update | 9.138 s | 87 |
| Three fields in one batch, single run | 8.317 s | 182 |
| Note / bug creation, one each | 8.022 / 7.866 s | 69 / 88 |
| Invalid field update, single run | 1.235 s | 317 |
| Merge gate subprocess, unmatched branch | 0.081 s | 6 |
| Edit hook subprocess, no output path | 0.099 s | 0 |

The matched edit-hook test used system Python 3.13 rather than the suite's 3.12:
first/fresh sessions took 0.217–0.223 s; the memoized repeat took 0.142 s and
emitted nothing. Hooks are a lower priority than multi-second writes and large
viewer payloads. Their SQL can still become costly on high-degree graph nodes.

## Relationship graph: several optimization levels

The current `_rebuild_related` loads 3,784 structural path rows and compares every
pair: 7,157,436 candidates. This is an implementation choice, not an inherent
requirement of finding work that shares a file. Those rows contain only **1,814
unique path/match groups**, of which **312 are globs**.

The automatic graph consumer found in this checkout is the edit hook's
`+N related` count. The SQL query tool also exposes the graph. The viewer endpoint
named `/related` separately reads handovers/issues and dependencies; it does not
consume this table. That makes it particularly important to avoid taxing every
write for a graph that many workflows never read.

### A. Skip work when its inputs have not changed

Maintain separate fingerprints/dirty sets for:

- FTS input: indexed title/prose/body fields.
- Structural path relationships: normalized anchors, locations, match kinds,
  source and multiplicity, entity identity, and relevant lifecycle changes.
- Handovers: task membership contributions.
- Declared links and their reverse edges.
- Board summaries and progress output.

Compare before/after inputs inside the transaction. A status or next-step edit
should not rebuild path relationships unless its actual inputs changed. A new
unlinked note should not rebuild them either. Preserve current archive behavior
until an explicit semantic change is approved: archived-but-live entities can
still contribute to historical relationships in today's implementation.

This is the best first optimization: no graph calculation is faster and simpler
than any replacement calculation. Every ingress path must participate, including
projection imports, archive/unarchive/delete, batch edits, and recovery.

### B. Group equal paths and restrict glob candidates

For a full rebuild:

1. Group by `(path, match_kind)` and count contributions per entity. Exact matches
   arise from a dictionary lookup, not scanning unrelated paths.
2. Compare glob patterns against unique paths rather than individual claims.
3. Index paths lexically. For `code-maestro-app-desktop/src/*.tsx`, consider only
   paths starting with the literal prefix before the first wildcard.
4. Expand matching groups to entity pairs while preserving counts and excluding
   self-pairs. Batch database insertion with `executemany`.

The benchmark prototypes preserve current case-sensitive `fnmatchcase` behavior,
including the existing behavior of comparing glob strings to other glob strings.
They do not silently substitute filesystem glob expansion.

| Equivalent path-weight calculation | Median | Glob comparisons |
|---|---:|---:|
| All-pairs reference over materialized tuples | 0.792 s | Not separately counted |
| Grouped paths | 0.131 s | 565,968 |
| Grouped paths plus literal-prefix filtering | **0.097 s** | **24,183** |

These calculation timings exclude DB reads/writes and are not the previous
5.4-second production span. All variants matched **36,240 path edges and every
weight**. Both prototypes also matched the reference on 100 seeded fixtures
covering duplicates, self-pairs, exact/glob overlap, bidirectional globs,
character classes and case differences. This is useful equivalence evidence,
not exhaustive production validation.

A full process-local replacement also rebuilt the handover contributions and
matched all **39,027 relationship rows as a multiset**. Complete handovers were
3.236, 3.331 and 6.189 seconds versus one adjacent baseline at 6.729 seconds.
Four prototype writers all committed in 5.392, 9.176, 12.991 and 16.611 seconds;
concurrent status reads took 0.347–0.893 seconds. The earlier four-writer baseline
timed out one writer. These are different runs, not a strict throughput ratio.

### C. Incremental graph maintenance

For an entity with `k` changed paths, remove its old contributions and recompute
only incident relationships. Candidate discovery comes from exact-path and glob-
prefix indexes. Normal work approaches `k × relevant candidates + output`,
instead of `all paths squared`.

Keep contributions separately from aggregate weights so removing one anchor or
one shared handover does not erase a relationship supported by another. Reverse
glob lookup matters: an unchanged pattern elsewhere can match the edited entity's
new path. Retain the full grouped rebuild as a repair path and test oracle.

This has higher implementation risk than A+B. Implement it only after the input
contracts and equivalence tests are explicit.

### D. Compute neighborhoods on demand

Query neighbors only for the edited file's entities or an explicitly requested
entity. Cache by a durable relationship revision. This is attractive if graph
reads are uncommon relative to writes and mostly local.

It changes the storage/query contract if arbitrary SQL can no longer read a
complete `related` table. Either retain a materialization path or introduce a
dedicated neighborhood API before removing the table. Measure hook tail latency
for very broad globs; do not move an expensive global scan onto every edit.

### E. Coalesce background rebuilding

If related suggestions may lag, commit entity changes plus a durable dirty
revision, then build once for a burst of edits. Publish a graph only if its input
revision is still current. Expose stale/ready state, and let callers request a
fresh result when necessary.

This requires an explicit consistency decision. It is suitable for advisory
recommendations, not gates or dependency checks requiring current authoritative
state. It is not needed to get the first A+B gains.

### F. Reduce what “related” means

Broad directory globs can form large cliques. A product-level alternative is
explicit links plus shared exact files, with broad globs used only for ownership
and a bounded top-K recommendation list. Degree caps, weighting by specificity,
or separating archived history can improve both relevance and performance.

These change answers. Do not present them as transparent implementation fixes.
Likewise, replacing Python with native code would retain the all-pairs design;
it is a later micro-optimization after eliminating unnecessary comparisons.

## Read path and caches

`backlog_get_task` loads the compatibility tree, then its open-handover helper
calls `_load()` again. `_load_cached_dict_from_connection` deep-copies the tree
and reconstructs all non-task `_rows`, including bodies, even at an unchanged
revision. A cached load took 0.260 seconds. Serializing that returned structure
for the benchmark produced roughly 10.4 MB of text; that is not a heap/RSS measure.

Use targeted repository methods: task-by-ID plus its epic, handovers-by-task,
links-by-source, filtered lists with SQL limits, and aggregate status queries.
Reuse a request's snapshot instead of calling `_load()` recursively. Search
should open/check the store and query FTS directly; only build the fallback tree
if the index path cannot serve the request.

Cache small, purpose-specific immutable results by `(creation_token, data_seq)`
or a relevant per-kind revision. Never cache shared mutable dictionaries that a
tool can alter. Preserve snapshot/ETag coherence and the network projection-only
fallback. A process-local cache alone duplicates work and memory across agents;
prefer efficient queries before adding another shared caching service.

## SQL access paths and FTS

`EXPLAIN QUERY PLAN` confirmed scans for incremental entity refresh, ID-only kind
lookup, reverse handover membership, and FTS kind/ID lookup. Temporary indexes on
the copy changed the first three to indexed searches:

| Query | Median before | Median after | Candidate index |
|---|---:|---:|---|
| Changed entities, recent seq | 30.80 ms | 0.004 ms | `entities(updated_seq)` |
| Kind lookup by ID | 20.77 ms | 0.007 ms | `entities(id, deleted, kind)` |
| Handovers for a task | 0.113 ms | 0.004 ms | `handover_tasks(task_id, handover_id)` |

These are microbenchmarks on this fixture, not whole-tool improvements. Indexes
add write/storage cost; use real query plans and distributions to choose them.
The existing `(kind,id)` key does not efficiently answer ID-only lookups, and
`(handover_id,task_id)` is ordered in the wrong direction for task-first access.
See SQLite's [query planner](https://www.sqlite.org/queryplanner.html) and
[EXPLAIN QUERY PLAN](https://www.sqlite.org/eqp.html) documentation.

FTS `kind` and `id` are UNINDEXED columns. Each touched entity currently deletes
FTS rows by those fields. Introduce a stable numeric FTS row identity/mapping so
replace/delete is by rowid, and update only when indexed prose changes. This
should especially help bulk imports and derived rebuilds; no full import speedup
was measured for this proposal. Do not rely on an implicit rowid that a rebuild
or VACUUM could remap without updating the association.

## Projection checks, YAML, and exports

An unchanged projection check took 0.428 seconds; a forced read check plus load
took 0.767 seconds. The two-second read throttle is process-local, so many agents
repeat the same filesystem enumeration. Writes always perform a scan.

Start with cheap improvements: reuse listings and stat results within one scan,
avoid repeated schema parsing in one request, and cache parsed root YAML against
a safe file identity/digest and Git generation. Do not assume unchanged mtime
alone proves unchanged bytes across Git operations.

A larger option is one watcher/import coordinator per repository with a durable
dirty-file journal. Keep periodic full reconciliation and a watcher-overflow
fallback; filesystem events can be missed. A shared daemon can coalesce work,
but adds lifecycle, version negotiation, crash recovery and worktree-root risks.

Progress export was only about 0.15 seconds in earlier probes. Optimize it after
graph/read issues: use relevant-field dirty flags and a shared durable throttle,
not one timer per server. YAML export can reuse projections and consider a C
dumper only after byte/ordering/type/line-ending round-trip tests.

Moving Git-facing exports after commit would require an explicit durable export
queue, monotonic ordering and well-defined `export pending` receipts. Preserve
the current recovery guarantees; do not simply remove fsync or verification.

## Viewer and agent output volume

Actual HTTP results on the copy:

- `/api/backlog`: **16,209,089 bytes**, first request 2.279 seconds.
- Same ETag in `If-None-Match`: **200**, same 16,209,089 bytes, 0.932 seconds.
- Task endpoint: 3,049 bytes, 0.245 seconds.
- Related endpoint: 455 bytes, 0.372 seconds.

The backlog payload contains 5.82 MB under `epics` (including tasks), 5.67 MB under
top-level `tasks`, and 4.36 MB under internal `_rows`. `viewer/js/main.js` sleeps
three seconds between completed polls. ETags exist for editing preconditions,
but unchanged GETs are not short-circuited and the client does not send them.

Implement both sides of conditional GET: retain/send ETags in the client and
return 304 from a cheap coherent revision check before building the payload.
Then introduce a board summary DTO: tasks once, required board/filter fields,
epic/phase summaries, no internal `_rows`. Load body sections on detail demand.
Adapt clients before dropping fields. Later, use revision-based deltas or SSE
with full-refresh recovery. Compressing today's duplicated response is a useful
transport aid, not a substitute for reducing construction and client allocation.

The related endpoint separately scans/parses handover and issue Markdown after
loading the DB tree. Replace that with one committed store snapshot and indexed
membership queries, removing duplicate work and mixed row/file freshness.

Agent-visible output needs the same discipline: default status was 133 KB and
continuity 856 KB. Add scopes, limits/cursors, compact summaries, count/overflow
receipts and explicit detail retrieval. This reduces context consumption and
host rendering/parsing, although model/transport savings were not benchmarked.
Do not silently truncate authoritative documents or change an existing viewer
consumer's full-data contract without updating that consumer.

## Transactions, concurrency, and operational behavior

Keep the transactional writer gate. WAL permits reader/writer overlap but still
has one writer at a time; it does not make a multi-second global rebuild parallel.
See [SQLite WAL](https://www.sqlite.org/wal.html). Increasing busy timeouts merely
makes the queue longer. Removing the mutex or spawning more writers is not a fix.

Use the existing batch tool immediately for related edits. Validate cheap syntax,
enums and required argument combinations before opening the writer transaction:
an invalid field currently took 1.235 seconds. Keep state-dependent validation
inside the transaction so it cannot race a peer.

Longer term, a per-repository writer coordinator could provide bounded/fair
queueing, cancellation before execution, coalescing, and queue position/progress.
Idempotency tokens are needed before automatically retrying non-idempotent
creates after ambiguous transport failures. Timeouts should distinguish waiting
from executing and already committed/export-pending responses.

The prototype concurrency run acknowledged all four writes and matched the full
relationship rebuild, but its long-lived connection reported
`malformed inverted index for FTS5 table main.entity_fts` during final integrity
validation. Fresh connections reported `ok` under both SQLite 3.47.1 (benchmark
Python) and 3.45.3 (system Python); no persistent corruption was established.
An unmodified two-writer control reproduced the same long-lived-connection
diagnostic, while its fresh connection passed integrity and foreign-key checks.
Its writes committed in 9.522 and 18.488 seconds. The anomaly is therefore not
specific to the optimized graph algorithm. Distinguish a runtime/connection or
validation-sequence problem from persistent corruption in a follow-up reproducer;
do not silently waive the diagnostic when validating a production change. It is
not evidence that live CodeMaestro is corrupt.

## Startup, adoption, maintenance, and Linear

Import/bind took 2.75–4.26 seconds in these runs. First status with an existing
populated copy took 1.303 seconds. Fresh SQLite adoption from the existing v4
projection took **178.791 seconds**, followed by warm reads at 0.217–1.119 seconds.
That is already-v4 adoption, not a measured legacy v3 migration. Integrity passed.

Adoption repeatedly parses/imports files and builds every derived entry; source
inspection also shows task/epic/phase reads in bootstrap and again in import.
Measure those phases separately before assigning the 179 seconds to one cause.
Candidate changes: parse-once import records, batched SQL/FTS ingestion, the new
indexes, grouped graph construction, and visible progress with durable recovery.
Keep normal servers long-lived rather than paying imports/adoption per command.

This copy had 3,565 entity rows, 4,209 changes, 309 session rows and an empty
Linear queue at the experiment snapshot. Its DB had 12,530 4-KB pages, only 56
free pages. VACUUM is not the first response to these latency problems; detailed
per-table page accounting was unavailable because this SQLite build lacks dbstat.

Treat growth separately: stale session cleanup, rotation/retention for logs and
completed queue rows, and explicit history compaction if needed. Preserve
non-recycled IDs, tombstones/reservations and revision monotonicity. Cache refresh
uses change sequence identity, so deleting history without a watermark/rebuild
protocol can invalidate cache correctness.

Linear status was fast but the queue was empty. The worker already performs
remote calls outside store transactions and claims rows with leases. Retain that.
Potential scale improvements include queue-state indexes, bounded/concurrent
draining within rate limits, target row reads, and cheap tracker outcome writes
that do not rebuild unrelated graphs. Remote latency, retries and large queues
need a dedicated fake-client/load test; no remote requests were made here.

## Delivery order and acceptance criteria

| Stage | Scope | Why first |
|---|---|---|
| 1 | Derived input dirty sets; grouped/prefix rebuild; FTS/relationship equivalence tests | Removes the dominant write tax without changing answers |
| 2 | Missing SQL indexes; targeted task/search/list reads; request snapshot reuse | Small responses should cost small queries |
| 3 | Conditional viewer GET and board DTO; bounded agent summaries | Removes repeated multi-MB transfers and context load |
| 4 | Projection scan/schema reuse; targeted row writes; progress invalidation | Removes the remaining filesystem/copying tax |
| 5 | Parse-once/bulk adoption; lifecycle metrics and maintenance | Improves setup and long-running reliability |
| Later | Incremental/on-demand graphs, watchers, shared writer service, async exports | Larger contracts and correctness risk; justify with new measurements |

Suggested targets, not achieved promises: warm single-entity and search p95 under
200 ms; simple metadata writes under 1 second; unchanged viewer GET with no body;
four/eight writers without timeout or lost acknowledgements; and a visible,
measured adoption progress stream. Establish proper distributions with many more
samples before making service-level claims.

For each optimization, retain the existing full algorithm as an oracle where
possible. Cover duplicate contributions, glob semantics, archives/deletes,
hand-edited projections, checkout generation changes, worker failure and
rollback. Verify FTS results and all graph weights, ID uniqueness, files/rows,
sequence receipts and ETags, not merely successful return strings. Track SQL
time, lock wait/hold time, files parsed/stat-ed, graph candidates, rows rewritten,
bytes returned, cache hits, queue depth and adoption phase timings.

The benchmark scripts and this report are the deliverables. The process-local
prototypes and temporary indexes are experiments; none were installed or merged
into Taskmaster's runtime.
