# Native core implementation evidence

## N03: additive staging schema and backfill

New `taskmaster/native/{schema,db,migrate}.py` supplies operational JSON-text
columns, separate documents/extensions/configuration, stable integer identities,
ordered shape-preserving relations, interned paths, event/receipt/outbox schema,
and verified read snapshot admission. SQL NULL means absent; JSON `null` means
an authored null. Legacy cache columns are retained separately for equivalence
diagnostics, not used as native field owners. Historical event payloads and the
AUTOINCREMENT high-water mark are retained without invented command groups.

Backfill owns one `BEGIN IMMEDIATE`, including DDL, data, comparison and the
verified marker. Exceptions and process death at seven stages leave no partial
schema. Repeated backfill preserves integer identities. Legacy domain writes
invalidate staging in their own transaction; an already-admitted reader can
finish its older snapshot. Unknown schemas/protocols are refused before writes
and rechecked after acquiring the writer lock.

This is **staging**, with legacy tables still the sole write authority. No Store
startup path invokes it. The bridge schema/protocol markers remain unchanged;
native staging has a separate version/protocol manifest. Activation requires
the later coordinator and migration gates. The existing projection, base,
session, queue and meta tables remain authoritative and untouched. Reservation
and crash-intent sidecars remain byte-identical; their coordinated cutover is
part of N15, not an implied file migration in N03.

Validation on Python 3.12.9 / SQLite 3.47.1:

- 117 focused migration/schema, bridge admission, API inventory and FTS tests
  passed; ignored JUnit: `test-results/native-foundation/n03-focused.xml`.
- 157 affected Store schema, transaction, recovery, round-trip and ID-allocation
  integration tests passed; `test-results/native-foundation/n03-integration.xml`.
- Rehearsal script `scripts/native_backfill_rehearsal.py` requires a marked
  benchmark copy and makes a new SQLite backup before writing anything.
- Existing isolated fixture: 3,559 entities, event high-water 4,188, first
  backfill 5.08 seconds, repeat 7.77 seconds. These are single diagnostic runs,
  including equivalence checks, not latency percentiles or performance acceptance.
- Both passes produced SHA-256
  `e732dc8c4a0448ba0715a1a12de3f3e14b6c2df50e4347f778e2f39e90d33f60`.
  Fresh-connection database/FTS integrity returned `ok`; no FK violations.
- Ignored raw report: `test-results/native-n03-rehearsal-20260912/report.json`.
  Source baseline `766914c6386c4971ae00581d1198e3d38a5d0e34` plus the N03 worktree
  changes; no live project, installed plugin or external service was modified.

## N04–N06 core and initial N07 lifecycle slices

`Repository.snapshot()` gates coherent read transactions. Gets select requested
core columns; lists apply SQL filters and limits before loading fields. Prose,
extensions and memberships are fetched only when requested. Continuations bind
store identity, event high-water, source generation and filter/field scope;
changed scopes or snapshots require a fresh page. Summaries aggregate in SQL,
reverse memberships use indexes, and search preserves porter/unicode61 and bm25.
The work-budget test adds 2,000 unrelated documents without materially increasing
a one-entity metadata read's SQLite VM work.

Explicit SQL compatibility uses an instrumented full in-memory snapshot of the
public tables, with the existing authorizer, deadline and 500-row cap. It never
copies private native tables or grants view-source exceptions that a CTE could
impersonate. This is an explicit expensive compatibility operation, not the normal
query path. Domain history comes from native events; current graph/projection/
queue/session tables retain their single existing representation.

Stored-body and canonical-section retrieval is implemented. Task external doc
references resolve only to captured `external_documents` rows with matching paths
and return hash/import-sequence provenance. Unimported or changed paths return an
explicit incomplete result. N13/N15 must capture those files before switching
existing clients; normal queries never read them directly from disk.

Commands validate/copy the request before admission, acquire `BEGIN IMMEDIATE`,
recheck native authority, look up scoped retry receipts before revision checks,
apply effects, and commit events/receipt/projection inputs together. No-op commands
emit no fictitious domain revision or job. Same-key/different-payload retries and
stale CAS edits conflict. A process can commit and lose its response; retry returns
its original receipt. Batches use one event group and roll back all operations,
including newly allocated IDs. Receipts currently have indefinite retention; no
key GC silently permits recreation. Native ID allocation refuses missing imported
high-water metadata.

Selective maintenance compares FTS, normalized paths, declared links and handover
memberships independently. Metadata with unchanged inputs issues no graph SQL and
does not replace search documents. Path edits recompute only the affected entity's
neighborhood; declared-link edits recompute affected inverse pairs. Handover edge
updates preserve duplicate contributions, including self-pairs permitted by the
legacy oracle. Archived records retain existing semantics. Eighty randomized
domain edits match the legacy full rebuild; 100 randomized grouped/prefix cases
match the naive weighted graph algorithm. Stable FTS keys survive replacements.

The first N07 slices reuse existing pure domain functions for note update/archive,
decision creation/resolution/drop, bug creation/update/archive guards, issue
creation/update, idea creation/update, and handover creation/status/supersession.
Archive moves capture both old-file deletion and new-file write intents. Handover
creation plus supersession is atomic. These are **not** the complete lifecycle
inventory: task/epic/phase lifecycle, gates, promotion, auto-link side effects,
Linear/configuration and the existing adapters still need their full port.

The native staging schema is now v2. An explicit backfill upgrades v1 additively
inside the same transaction, retaining integer keys; injected failure rolls back
the upgrade. Active native admission requires the native authority/protocol and
completed local-state import markers. Only tests and the marked-copy benchmark
set those markers. There is no operator activation command or startup cutover.

Review additionally removed unbounded `meta` reads and unused core-column reads,
and tightened lifecycle argument types before database admission. Existing stored
scalar/list/null representations still round-trip; stricter new-command syntax
does not rewrite existing authored data.

Creating a formerly missing target now attaches matching typed memberships to
its stable integer key inside that same command, preserving the authored ID.
Unresolved bug promotions and issue duplicate references retain `issue` as their
target kind instead of guessing `task`.

## Validation coverage at this checkpoint

- Full invocation: 2,499 passed, one skipped, one failure in 1,471.54 seconds.
  The failure was a new test's unqualified `backfill` name; it is corrected.
- Current native-core corrective run: 97 passed. Final command/lifecycle run:
  31 passed, including the subsequently added atomic reference-resolution test.
- Reconciled against a fresh collection: **2,510 cases, 2,509 passed, one skipped,
  zero unresolved failures and zero missing cases**. These are unique cases
  across runs, not the sum of overlapping test counts or a claim that the initial
  full invocation itself was green.
- The skipped case is `test_smoke_full_push_round_trip`, which needs live Linear.
- Existing 8-process × 200-operation public-tool write-survival test passed in
  603.852 seconds. This validates the existing runtime against this tree; native
  service/process and viewer acceptance still belong to N16.
- `git diff --check` and native module compilation passed.

Ignored evidence in `test-results/native-foundation/`: `native-core-full.xml`,
`native-core-final-focused.xml`, `native-lifecycle-final.xml`,
`native-core-collected.txt`, and `native-core-coverage.json`. The full run began
before the review corrections; the focused reports validate the changed native
code afterward. Existing production routing was not changed during that run.

## Core performance observation

`scripts/benchmark_native_core.py` backs up a marked isolated copy into a new
output directory. Its native marker change is test-only, after importing the
copied ID reservations; it is not the N15 operator migration protocol. No client,
Linear worker or exporter starts. The report pins Python/SQLite, source SHA and
every native module's SHA-256, since the measurement preceded the later review
fixes and is not a claim about an unrecorded final tree.

On the 3,559-row copy, one process, local WAL/FULL durability, 20 warmups then 200
measurements, including JSON encoding:

| Operation | p50 | p95 | p99 | Worst |
|---|---:|---:|---:|---:|
| Selected task metadata read | 0.68 ms | 0.95 ms | 1.39 ms | 1.99 ms |
| Task next-step command | 4.15 ms | 7.72 ms | 12.89 ms | 15.29 ms |

All measured writes had zero FTS replacements, path comparisons, link-pair work,
handover-pair work and global graph rebuilds. Maximum selected-read JSON was 276
bytes; maximum receipt JSON was 3,390 bytes. All 220 projection jobs remained
pending deliberately. Fresh-connection DB/FTS integrity passed with no FK errors.
Raw report: `test-results/native-core-benchmark-20260912/report.json`.

These are DB-core measurements. IPC/service latency, projection delivery, actual
viewer interaction, 4/8/12-client native stress and migration acceptance remain
N08–N16 work. They do not establish an installed-product speedup.

## Remaining merge boundary

The user explicitly requires the entire N03–N17 rewrite before merging. The
default branch here is `master`, not `main`. It has not been merged or pushed.
No CodeMaestro files, installed plugin, live service or external account changed.
The next work is completion of N07, followed by the client bypass gate, context/
viewer contracts, durable exporter/coordinator/sync, migration rehearsal and full
end-to-end acceptance. This core checkpoint must not be described as that cutover.
