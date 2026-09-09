<!-- User intent: create executable plans for database-native Taskmaster, based on
     the measured CodeMaestro-copy performance investigation and architecture discussion. -->

# Database-native Taskmaster implementation plan

**Status:** implementation started on `feat/database-native-foundation`.
**Design:** [database-native design](../specs/2026-09-09-database-native-design.md).
**Baseline:** 6.0.2, `e9ea119`. Recheck before implementation; the current audit
reports and benchmark scripts are uncommitted task-owned artifacts.
**Tracking:** this repository has no project backlog; use this plan's ledger and
`docs/handoffs/` rather than modifying CodeMaestro's backlog or initializing one.

## Execution rules

- Implement a reviewable step in an isolated branch/worktree. Preserve unrelated
  changes and the audit artifacts. No live CodeMaestro mutations, installation,
  publishing or project migration are part of this plan's local execution.
- Before each step, read its spec sections, inspect the current callers and
  tests, and update the scope if the code has changed. Do not implement from
  historical line numbers alone.
- Add behavioral tests before production changes; include failures for the
  contracts being changed, not tests that merely duplicate implementation.
- Run focused tests while iterating. At a step boundary run the affected
  integration suites; run the full suite at each milestone and before release.
  Use the working repository interpreter; verify it rather than assuming `.venv`
  is accessible in a sandbox. Do not regenerate dependency files unnecessarily.
- Benchmark only marked isolated copies. Pin source SHA, Python/SQLite versions,
  fixture digest, process count, warmup and storage conditions. Keep private raw
  data in ignored `test-results/`; commit only sanitized metrics and scripts.
- Keep the old full algorithms as test oracles until equivalence is proven.
  Never run shadow production mutations twice. Neither compilation nor a faster
  benchmark counts as a migration/correctness gate.
- Record completion evidence and remaining limitations in the ledger. A pending
  release/migration gate is not completed by a local merge.

## Milestones and dependency graph

| Milestone | Steps | Deliverable |
|---|---|---|
| M0: safe foundation | N00–N02 | Reproducible measurements; forward-schema refusal; compatibility inventory |
| M1: native core | N03–N07 | Relational schema, targeted queries/commands, selective derived state |
| M2: agent and viewer usage | N08–N10 | Core-backed adapters, bounded context, compact HTTP/change contracts |
| M3: native synchronization | N11–N14 | Durable outbox, service ownership, explicit sync and managed Git barriers |
| M4: cutover and acceptance | N15–N17 | Migration rehearsal, scale/crash validation, release-ready package |

```mermaid
flowchart TD
    N00 --> N01
    N00 --> N02
    N01 --> N03
    N02 --> N03
    N03 --> N04
    N03 --> N05
    N05 --> N06
    N04 --> N07
    N06 --> N07
    N07 --> N08
    N08 --> N09
    N08 --> N10
    N08 --> N11
    N11 --> N12
    N12 --> N13
    N13 --> N14
    N09 --> N15
    N10 --> N15
    N14 --> N15
    N15 --> N16
    N16 --> N17
```

The graph expresses dependencies, not an instruction to launch parallel agents.

## N00 — Establish test oracles and isolate the FTS diagnostic

**Depends on:** none. **Scope:** harnesses and focused regression tests.
**Files:** audit scripts; new `tests/test_native_baseline.py` and a focused FTS
reproducer; existing store concurrency/recovery tests.

1. Reproduce the long-lived-connection FTS integrity result with unchanged runtime
   code. Compare fresh/retained connections, explicit snapshots, rollback,
   checkpoint, concurrent writes and supported SQLite versions independently.
2. Determine whether this is persistent corruption, stale virtual-table state,
   diagnostic sequencing or a runtime defect. Fix the demonstrated cause or pin
   a verified supported runtime policy with a regression test. Never auto-rebuild
   authoritative state merely to hide an unexplained diagnostic.
3. Add canonical read/write outcome captures and graph/FTS/artifact comparison
   helpers. Correctness comparisons run outside timed regions.
4. Record original and optimized algorithm counters, tool result sizes and
   projection overhead as separate metrics.

**Exit:** reproducible baseline; FTS cause and supported behavior documented.
Investigation may continue alongside inventory, but schema/recovery acceptance
cannot waive it. No claimed tail percentiles from three-sample runs.

## N01 — Ship-safe forward-schema and client fencing

**Depends on:** N00. **Files:** `store.py`, `root.py`, startup/hooks/version tests.

Replace newer-schema rebuild behavior with read-only refusal. Ensure cold and
already-open store paths refuse unsupported schema/protocol transitions before
mutating tables, reservations, projection files or queues. Add bridge capability
metadata and a migration/ownership admission check with a documented lifecycle.

Test 6.0.2-era behavior in an isolated fixture to demonstrate the hazard, then
test the bridge across MCP, viewer, hooks, scripts and Linear. Inventory installed
launch paths and how they will be stopped/upgraded at real cutover. Protect
existing open handles; a tracked projection marker alone is not sufficient.

**Exit:** an unknown newer DB is unchanged after every supported entry point.
Bridge installation is a later authorized rollout prerequisite, not performed
automatically by this step.

## N02 — Freeze the public compatibility and field-ownership inventory

**Depends on:** N00. **Files:** new native contracts/inventory fixture; docs matrix.

Enumerate every registered tool and route, hook, script and Linear operation.
Classify it as targeted query, simple command, composite command, synchronization,
maintenance or legacy export. Record expected defaults, field types, result
shape, error behavior, limits, gate rules and side effects.

For each kind, map every field to canonical column/document/membership/extension
storage. Include all kinds, project/backlog configuration, typed dates/scalars,
unknown fields, archive state, invalid references and ID allocation behavior.
Inventory all consumers of legacy SQL names, FTS shadow access and the `related`
table. Machine-check inventory against tool registration and allowed query names.

**Exit:** no unclassified mutating caller or field; concrete compatibility matrix
and schema ownership inputs for N03. New APIs are explicit additions.

## N03 — Relational schema and transactional additive backfill

**Depends on:** N01, N02. **Files:** new `native/db.py`, `schema.py`, `migrate.py`;
`tests/test_native_schema.py`, `test_native_migration.py`.

Specify and implement DDL for core rows, documents/extensions, stable integer
keys, memberships, events/receipts and projection metadata. Add indexes based on
query plans, including changed-entity sequence and reverse memberships. Retain
opaque extension data, explicit unresolved references and legacy event sequence.

Backfill from a consistent existing DB snapshot, not only projected files. Build
within a migration transaction or a checkpointed staging process with an explicit
atomic activation step; test interruption at each stage. New reads remain gated
until backfill equivalence passes. No independently writable old/new stores.

Probe required SQLite capabilities. JSON text is the baseline; JSONB and a new
SQLite dependency are not silently introduced. Emit schema/protocol manifests
that N01 clients understand and older clients refuse.

**Exit:** all fields/counts/IDs/revisions/local-only state preserved; repeatable
backfill; rejection/rollback leaves old authority intact.

## N04 — Targeted query repository

**Depends on:** N03. **Files:** `native/queries.py`, `documents.py`, `search.py`;
`tests/test_native_queries.py`, `test_native_read_snapshots.py`.

Implement get/list/filter/page for all entity kinds; task/epic/phase summaries;
dependencies, memberships, direct search and explicit document section retrieval.
Use stable pagination ordering with an explicit snapshot/cursor consistency rule.
Expose a snapshot context so composite reads reuse it.

Introduce read-only compatibility views or adapters for public SQL contracts.
Update query authorizer tests for permitted view expansion without exposing
private tables. Keep FTS ranking/tokenization and query deadlines intact.

**Exit:** semantic parity with N02 fixtures; no full-tree or file reads for native
bounded queries; query-plan and scanned/materialized-row budgets pass.

## N05 — Command transactions, revisions and durable retry receipts

**Depends on:** N03. **Files:** `native/commands.py`, `contracts.py`, `events.py`,
`receipts.py`; command/idempotency/concurrency tests.

Build one transaction owner with pre-admission argument checks, current-state
domain validation, compare-and-swap revisions, events and commit receipts.
Implement scope/key/payload-hash deduplication before checking stale revisions.
Keep no-op semantics and distinguish cancellation-before-execution from unknown
post-admission outcome. Add all-or-nothing structured batches.

Start with task metadata and note creation as vertical slices. Verify outcomes
from committed snapshots/receipts, not a post-commit read that a peer may change.

**Exit:** same-key retries create once, mismatched reuse conflicts, stale revisions
never overwrite peers, and injected failures leave no partial events/state.

## N06 — Selective derived maintenance

**Depends on:** N05. **Files:** `native/relations.py`, `search.py`; graph/FTS tests.

Track input changes independently for FTS, paths, memberships, declared links,
reverse access and summaries. Use stable FTS document keys. Port the grouped/
prefix graph algorithm as the exact rebuild oracle; preserve duplicate weights
and every current lifecycle/glob rule.

Native metadata-only edits must not rebuild path graphs or unrelated search
documents. Implement current graph correctness before moving to neighborhood
queries. Test fields entering/leaving indexes, imports, archive/unarchive,
tombstones, duplicate claims and explicit inverse links.

**Exit:** exact FTS and graph parity under randomized/domain fixtures; counters
prove zero global rebuild for edits with unchanged relationship inputs.

## N07 — Complete lifecycle and composite commands

**Depends on:** N04, N06. **Files:** command handlers and focused domain tests.

Port every N02 command family: create/edit/archive, handover create/supersede/
status, decision resolution, bug/issue promotion, task pick/complete/gates,
epic/phase transitions, typed links, project settings and Linear link/outbox.
Preserve task claim eligibility, reservations and non-recycled IDs.

Maintain one transaction for composites and one domain rules layer for tool and
HTTP callers. No remote API call occurs inside it. Queue/tracker maintenance
must not rebuild an unrelated graph. Document intentional changes separately.

**Exit:** N02 mutation inventory fully mapped; existing completion/gate/claim,
handover, batch, auto-link and Linear tests pass through the new core.

## N08 — Route existing clients through the native core

**Depends on:** N07. **Files:** `backlog_server.py`, `store.py`, viewer adapters,
hooks/scripts/Linear adapters, bypass and compatibility tests.

Switch tool families incrementally behind project/runtime capability checks.
At this stage preserve synchronous projection behavior via a compatibility
adapter, so query/command correctness is isolated from export semantics.
Keep one authority for both legacy-named and new commands.

Add a bypass gate: native normal reads/commands cannot call `_load()`,
`load_dict()`, `transaction_dict()` or scan projection files. Allowlist only
explicit sync, adoption, compatibility snapshot and maintenance operations.
Track remaining fallback calls until the normal-path count is zero.

**Exit:** all legacy tools/routes use the core with compatible receipts/errors;
full suite and copied-CodeMaestro parity pass. This is M1/M2 local compatibility,
not asynchronous/native-mode activation.

## N09 — Agent context, change queries and atomic claims

**Depends on:** N08. **Files:** context/query contracts, events, tool adapters;
context/cursor/claim tests and playbook updates.

Add bounded `context`, scoped `changes_since`, document detail and structured
mutation interfaces. Prioritize mandatory blockers/gates, then selected context;
return encoded-byte budgets, omission counts, provenance and continuation tokens.
Add atomic eligible-task claim/renew/release with conflict and expiry behavior.

Cursor tests cover store rebuild, history expiration, scope changes, filtered
removal and multi-entity commit grouping. Preserve existing tool names; do not
replace them with an untyped all-purpose dispatcher. Update examples to avoid
repeated full status/list calls.

**Exit:** representative agent journeys require fewer calls/bytes while yielding
the same required context; missing mandatory context is never reported as clear.

## N10 — Viewer DTO, conditional GET and scoped deltas

**Depends on:** N08. **Files:** viewer HTTP routes; `viewer/js/api.js`, `main.js`,
state/detail consumers; HTTP/client tests.

Define board DTO fields from the actual consumer inventory. Remove duplicated
tasks and private `_rows`; lazy-load prose. Implement client ETag retention and
cheap coherent 304 responses. Add scoped delta/removal application and full
resync fallback; combine detail-related reads from DB snapshots.

Measure wire bytes, server construction, client parse/render and heap behavior.
Do not infer UI responsiveness from HTTP timings. Update consumers before
removing fields; preserve edit preconditions and unknown task handling.

**Exit:** unchanged board GET returns 304/no body; no duplicate task payloads;
detail editing/navigation and filter membership updates pass browser checks.

## N11 — Durable projection outbox with synchronous compatibility drain

**Depends on:** N08. **Files:** `native/projection.py`, events/schema additions;
outbox/recovery/property tests.

Persist projection jobs with commands and immutable revision inputs. Implement
lease recovery, expected-base verification, coalescing, archive moves, tombstones
and quarantine. Initially drain synchronously through the new outbox adapter so
existing visibility remains unchanged while the recovery protocol is tested.

Inject failure before/after DB commit, temp write, replace, manifest update and
job acknowledgement. Test a newer DB edit and an external file edit while an old
job is rendering. Compare lossless v4 artifacts and unchanged-byte behavior.

**Exit:** no committed effect loses its export intent; no stale job overwrites
newer content; retry/rollback preserves authored data and local-only state.

## N12 — Repository coordinator and asynchronous exporter

**Depends on:** N11. **Files:** `native/service.py`, `client.py`, protocol/lifecycle
tests, launch adapters.

Implement exclusive repository ownership, authenticated local discovery,
root/schema/protocol handshake, write queue admission and exporter/importer
coordination. Thin MCP clients forward validated envelopes. Hooks may use
versioned read-only views without importing the service runtime.

Native mode commits before export and returns pending projection receipts.
Legacy visibility mode uses the same service with a barrier before returning.
Test start races, stale PID/nonce, restart, cancellation, client disconnect,
protocol mismatch and Windows process cleanup. No silent fallback writer.

**Exit:** multiple clients share one coordinator; worker failure is recoverable;
normal native commands contain no projection rendering or filesystem scanning.

## N13 — Explicit sync/import and Git generation barriers

**Depends on:** N12. **Files:** `native/sync.py`, CLI/adapters, root/Git hooks;
sync/worktree/merge/crash tests.

Implement the design's finite `flush_through` and import barrier. Parse external
changes outside the writer transaction, revalidate file/base/revision identities
before apply, and route accepted changes through native commands. Preserve
quarantine, three-way merges and absence-not-deletion.

Integrate pre-commit publication ownership and pre/post-checkout synchronization.
Provide explicit operations for hosts that bypass hooks. Exercise linked
worktrees, branch changes restoring an older projection marker, conflicts,
partial file publication and a writer racing Git staging.

**Exit:** managed Git operations capture coherent projections; bypassed operations
are detected/reconciled with honest pending/conflict status, never silent loss.

## N14 — Consumer graph migration and compatibility completion

**Depends on:** N13. **Files:** relations, query guard/views, edit hook, graph
consumer tests and API docs.

Move hook neighborhoods and typed graph queries to canonical claims/edges and
indexed reverse access. Retain the exact full materializer as repair/oracle and
for any supported SQL workflow that requires it. Decide its public freshness
mechanism in the compatibility matrix before disabling eager materialization.

Add bounded recursive dependency traversal and verify weighted/current/archive
behavior. No unannounced top-K or relevance changes. If direct `related` SQL must
remain always current for this release, retain selectively maintained storage
and document on-demand replacement as a later capability, not unfinished native
command work.

**Exit:** native commands pay only for changed relation inputs; supported graph
consumers retain documented freshness and answer semantics.

## N15 — Migration and downgrade rehearsal

**Depends on:** N09, N10, N14. **Files:** migration command, dry-run/report tooling,
backup/restore tests, operator runbook.

Rehearse the complete bridge → quiesce → reconcile → consistent backup → backfill
→ compare → activate sequence on copies. Preserve historical seq/IDs, queues,
reservations, quarantine, unknown fields and in-flight export jobs. Refuse active
old clients and incompatible launchers; test both warm and cold clients.

Crash at each durable migration stage. Prove pre-activation rollback and post-
activation roll-forward. Test code rollback only to a schema-capable version.
Specify a separate reverse migration if old-format downgrade is supported;
restoring a pre-migration snapshot must not discard newer acknowledged writes.

**Exit:** executable dry-run and recovery runbook with evidence; zero live project
migrations during development. Native activation remains a release decision.

## N16 — Scale, durability and end-to-end acceptance

**Depends on:** N15. **Files:** perf/stress fixtures, crash harness, final report.

Run the matrix below using uninstrumented latency samples and separately enabled
work counters. Include full suite, native process/IPC and actual viewer workflows.
Resolve correctness failures before accepting performance numbers. The FTS
diagnostic must have a supported, explained outcome.

| Axis | Required cases |
|---|---|
| Dataset | Small synthetic, copied CodeMaestro, 10× synthetic with controlled path/edge distribution |
| Reads | Warm/cold, scoped details/search/context, unchanged/delta viewer, reads during writes |
| Writes | Metadata, prose, path/link/membership, create/archive/composite, no-op/invalid input |
| Clients | 1, 4, 8, 12; same entity and disjoint entities; duplicate request retries |
| Sync | No external edits, dirty projections, conflict, checkout/worktree, missing files |
| Failure | Process kill, expired lease, blocked file replacement, disk/commit errors, client disconnect |
| History | Cursor scope change/expiration, store rebuild, filtered removals, multi-entity commit |
| Migration | Old bridge/new clients, stopped/active old runtime, interrupted cutover, recovery |

For steady-state distributions use at least 200 measured operations per selected
scenario after warmup; report p50/p95/p99, errors and worst observed latency.
Keep cold/adoption runs separate. Record lock wait/hold, DB time, rows read/written,
files stat-ed/parsed, graph candidates, output bytes, cache hits, RSS/allocations,
export lag and IPC startup. Scaling tests must distinguish growing required
output from growing unrelated data.

Targets from the design are provisional budgets. Deterministic work assertions
(zero global graph work on metadata edits, no full-tree materialization, no normal
command file scan) are stronger gates than machine-sensitive microsecond limits.

**Exit:** all correctness gates pass; performance improvements and remaining
limits measured without claiming model/network latency as DB performance.

## N17 — Documentation, release candidate and rollout boundary

**Depends on:** N16. **Files:** README/changelog, playbooks, adapters/manifests,
runbooks and final handoff.

Document commit versus projection receipts, sync/Git obligations, migration
requirements, service recovery, context/delta usage and compatibility support.
Test packaged launch paths and version alignment. Choose the release version
after the compatibility impact is reviewed; do not preassign a patch version to
a behavioral/schema migration.

Prepare a concrete release candidate and copied-project demonstration. Publishing,
installation and live CodeMaestro activation require their own authorized step.
Release evidence must distinguish local tests, packaged runtime and actual
project migration. Do not mark native rollout complete on a local green suite.

**Exit:** reviewed release candidate, validated packaged launch paths and a
complete operator handoff. Publishing and live activation remain separately
recorded release actions, with no implied deployment from this step's completion.

## Execution ledger

All entries start **planned**. Update with commit, validation artifact and any
explicitly deferred scope when implementation actually occurs.

| Step | Status | Commit / evidence |
|---|---|---|
| N00 | complete locally | [FTS matrix (40 cases), baseline oracles; 129 integration tests passed](../reports/2026-09-09-native-foundation.md) |
| N01 | planned | — |
| N02 | planned | — |
| N03 | planned | — |
| N04 | planned | — |
| N05 | planned | — |
| N06 | planned | — |
| N07 | planned | — |
| N08 | planned | — |
| N09 | planned | — |
| N10 | planned | — |
| N11 | planned | — |
| N12 | planned | — |
| N13 | planned | — |
| N14 | planned | — |
| N15 | planned | — |
| N16 | planned | — |
| N17 | planned | — |

**First implementation action:** start N00 on an isolated branch; capture the
current baseline and narrow the existing FTS diagnostic. No schema rewrite or
live migration is the first task.
