<!-- User intent: plan the database-native Taskmaster architecture discussed after
     measuring performance on isolated copies of CodeMaestro's backlog. -->

# Database-native Taskmaster design

**Status:** proposed design for implementation planning; no native runtime shipped.
**Baseline:** Taskmaster 6.0.2, `e9ea119`, 2026-09-09.
**Execution plan:** [database-native plan](../plans/2026-09-09-database-native.md).
**Evidence:** [performance audit](../reports/2026-09-09-performance-optimization-roadmap.md)
and [architecture proposal](../reports/2026-09-09-sqlite-native-architecture.md).

## 1. Outcome and scope

Normal commands operate on affected rows; normal queries retrieve bounded answers
from a coherent database snapshot. Full-backlog dictionaries, projection scans,
global relationship rebuilds and Markdown rendering leave the normal command
path. SQLite remains the one runtime authority per repository/main checkout.

The final native mode includes targeted commands/queries, relational operational
fields, separate prose, selective derived maintenance, durable asynchronous
projection, scoped context/change feeds and repository coordination. This is not
complete merely when the existing graph loop gets faster.

Delivery proceeds behind compatible tool adapters. Until native synchronization
is explicitly activated for a project, the existing file-visible behavior stays
available. Activation is a migration boundary, not a silent setting changed by
the first new read. Planning authorizes documentation, not live project migration,
plugin installation, publishing, or production rollout.

## 2. Design decisions for the plan

| ID | Planned decision | Consequence |
|---|---|---|
| D01 | Retain Python, SQLite, public human IDs and main-checkout ownership | No engine rewrite or per-worktree database |
| D02 | Hybrid relational schema: operational columns, normalized membership, separate prose, JSON extensions | Each field has one canonical owner |
| D03 | Existing tools become adapters over shared commands/queries | Lifecycle rules have one implementation |
| D04 | Entity revisions for edits; durable scoped idempotency keys for retries | Unrelated edits do not create false conflicts |
| D05 | Native mutations commit DB state and an export outbox atomically | Receipt distinguishes DB commit from file synchronization |
| D06 | Native external edits are imported at explicit sync boundaries; watching is optional acceleration | No implied immediate hand-edit visibility on every tool call |
| D07 | A repository coordinator owns native write admission and projection/import ordering | No concurrent independent exporters; hooks may read committed snapshots |
| D08 | Preserve relationship answers initially using selective/grouped maintenance | No accidental relevance/ranking change |
| D09 | Replace full graph materialization only after its consumers migrate | SQL compatibility stays explicit |
| D10 | Keep FULL durability, IDs/reservations, tombstones, quarantine and gate rules | Speed must not weaken acknowledgement guarantees |
| D11 | Keep JSON text initially; JSONB/external-content FTS are separately measured refinements | No incidental runtime-version requirement or opaque API change |
| D12 | Native mode requires a coordinated client upgrade and migration | Old binaries must not access a new schema unchecked |

These are concrete recommended defaults, not a menu left to implementation agents.
If a decision changes, revise this spec and its dependent plan steps first.

## 3. Module and ownership boundaries

Proposed new code lives under `taskmaster/native/`:

- `db.py`, `schema.py`, `migrate.py`: connections, schema capabilities, migration.
- `queries.py`, `commands.py`, `contracts.py`: bounded reads, validated operations,
  response/cursor contracts. No imports from the MCP/HTTP server.
- `documents.py`, `relations.py`, `search.py`: prose, relationships, FTS indexing.
- `events.py`, `receipts.py`: domain changes and retry receipts.
- `projection.py`, `sync.py`: outbox export, import and barriers.
- `service.py`, `client.py`: repository ownership, lifecycle and local protocol.

`taskmaster/root.py` remains the lightweight root-resolution boundary.
`taskmaster/backlog_server.py`, viewer routes, hooks and Linear adapt to this core.
Pure v4 parsers/renderers and merge helpers remain reusable. `store.py` becomes
the legacy facade during migration; it must not become a second writer authority.

During the compatible rollout, every mutation—new or legacy-named—uses the same
transaction owner. Shadow comparison is read-only; never perform two production
writes to compare implementations.

## 4. Data model

The schema task must supply concrete DDL, indexes and an ownership map before
changing callers. The following logical shape and ownership are fixed:

| Relation | Responsibility |
|---|---|
| `entity_core` | Stable integer entity key; unique `(kind, public_id)`; title, status, priority, lifecycle flags, revision and last domain sequence |
| Kind-specific operational rows | Task epic/phase/owner and other frequent fields; handover metadata; equivalent fields for other kinds |
| `entity_documents` | Original prose body and format; optional derived section metadata |
| `entity_extensions` | Fields not promoted to operational columns; preserves unknown fields and existing scalar semantics |
| `dependencies`, `declared_links` | Canonical directed relationships indexed in both directions |
| Membership tables | Handover/task, issue/task and other existing typed associations |
| `paths`, `path_claims`, `glob_patterns` | Interned paths/patterns, entity claims, source/match semantics and contribution multiplicity |
| `document_search` plus stable key mapping | FTS5 with targeted replace/delete by stable document identity |
| `domain_events`, `command_commits` | Ordered logical changes and new-command grouping |
| `command_receipts` | Request scope/key, canonical payload hash, outcome and committed revision/sequence |
| `projection_jobs`, `projection_state` | Durable effects, captured revision, expected file identity, base bytes/hash and job state |
| `sync_state`, runtime metadata | Import/export barriers, schema/protocol identity, ownership and retention watermarks |

Public IDs remain kind-qualified internally; an ID-only lookup follows today's
resolution rules explicitly. Integer keys are not user-facing and are never
derived from mutable title/order. Tombstones retain identity. Do not cascade
business deletion through foreign keys without an explicit domain command.

An unresolved reference that legacy data permits is preserved as an unresolved
reference, not discarded to satisfy a new FK. Validation reports it; later
resolution attaches it atomically. Unknown extension fields and authored Markdown
must round-trip. Promoted columns are canonical: compatibility `doc` JSON is
assembled on request, not independently updated as a duplicate authority.

Preserve historical event sequence values and before/after payloads. Legacy
history need not invent command grouping that was never recorded. New commands
record their event group and return its final sequence. Internal heartbeats,
export retries and cache writes do not invalidate domain data cursors.

## 5. Query and command contracts

### Queries

Reads execute in one short snapshot and return selected columns, a store identity
and a domain sequence. Task details join only requested metadata/membership;
prose is fetched only when requested. SQL limits and filters are applied before
materializing results. No `_load()`, `load_dict()` or filesystem traversal in
native normal queries. Explicit compatibility/export operations are exceptions
and are instrumented as such.

`context(scope, include, max_bytes, cursor)` returns revisions, provenance,
mandatory blockers/gates first, omitted counts and continuation information.
If mandatory content cannot fit, return `incomplete=true` and do not claim the
task is unblocked. Byte limits apply to encoded output, not guessed token counts.

### Commands

Envelope: protocol version, repository/store identity, operation, arguments,
optional expected entity revisions, and `request_id` for retryable creation or
composite commands. Existing callers may omit revision preconditions initially;
they still get one atomic current-state transaction. New edit clients must send
the revisions they used to construct the edit.

Before queueing: validate syntax, enums, payload bounds and supported operation.
Inside `BEGIN IMMEDIATE`: look up an existing scoped request receipt first;
then validate state/revisions and domain invariants, change only affected rows,
maintain necessary local indexes, append events/receipt/outbox, and commit.

Same request key and canonical payload returns the original receipt, even if a
peer later edited the entity. Same key with a different payload is an explicit
conflict. A receipt describes the committed outcome, not necessarily current
state. Keys include repository identity and caller scope; retention is bounded
only by an explicitly advertised retry window, after which reuse is rejected or
requires a new key rather than silently recreating a prior operation.

No-op commands do not bump entity revisions or emit fictitious domain changes.
A retained no-op receipt can reference the existing domain sequence. Failed
commands produce no partial domain state or outbox entries. Commands that change
multiple entities are atomic by default; partial batch mode is out of scope for
the first native release.

Receipt fields include operation/request ID, affected IDs and revisions,
`commit_seq`, `projection_state`, and any conflict details. Preserve `[seq N]`
for legacy text wrappers, adding an unambiguous pending-export notice when needed.

## 6. Derived data and relationships

Compare before/after inputs for FTS, paths, memberships, declared links and
summaries separately. Metadata edits must not rebuild unrelated derived data.
All correctness-critical derived state is current at commit.

First use selective updates plus the measured grouped/prefix full rebuild as a
repair oracle. Preserve self-pair exclusions, duplicate contribution weights,
archive behavior, type inverses and case-sensitive glob semantics.

Then migrate consumers to indexed neighborhood queries and canonical reverse
lookups. Declared edges remain authoritative. Dependency traversal has cycle,
depth, output and execution budgets. On-demand graph compatibility must be
explicit: a retained `related` SQL surface cannot quietly return stale/partial
rows. Arbitrary SQL requiring a full graph either materializes a current snapshot
through a documented operation or uses the compatible synchronous representation.

Full relationship removal is not a prerequisite for the first native release.
Changing relevance through top-K, broad-glob exclusions or archived-history
removal is a separate product change.

## 7. Asynchronous projection and synchronization

In native mode the command commits durable domain state and projection intent;
it does not render/fsync Markdown in its database transaction. Jobs reference
immutable committed revision data (or an immutable event/document snapshot), not
an uncontrolled later read of the current entity. Coalescing is allowed only
when older effects are coherently superseded, including archive moves.

One coordinator serializes export/import publication. Parsing and rendering can
occur outside the SQLite writer lock. Before publishing, compare the expected
projection base/file identity. External edits enter three-way merge or quarantine;
never overwrite them just because a DB revision is newer. Atomic replacement,
recovery intents and post-crash reconciliation remain necessary.

Job lifecycle: pending → claimed with lease → exported, superseded, or conflict.
Expired claims are recoverable. Retrying must never publish an older revision
over a newer file. Missing files are repaired unless an explicit tombstone/move
requires their absence.

`sync(import=True, flush_through=S)` establishes an explicit barrier:

1. Acquire synchronization coordination; identify external changes against bases.
2. Parse outside the DB lock; revalidate identities and apply imports through
   commands, or report conflicts without destructive resolution.
3. Capture a domain target sequence after the accepted imports.
4. Export all required effects through at least `S` and the captured target,
   including coherently superseded jobs.
5. Return the barrier sequence, unresolved paths and success/pending state.

Later unrelated writes need not make a finite barrier wait forever. A Git commit
workflow, however, must pin a consistent export generation and prevent publisher
interleaving until staging/commit completes. Multi-file filesystem replacement
is not itself atomic; partial publication may be observed outside a barrier.

Git integrations must define pre-commit flush, pre-checkout reconciliation and
post-checkout/import behavior across main/linked worktrees. Hooks plus explicit
CLI operations support managed flows; arbitrary external Git commands can bypass
hooks. Detect and report generation drift on the next sync, rather than claiming
all external Git operations are transactionally coordinated.

Watched import is a later convenience. Explicit sync is the correctness fallback
for missed events, overflow, process downtime and manual edits.

## 8. Repository service and clients

The native service owns write admission, exporter/importer ownership and schema
migrations. Local clients handshake on canonical root, store identity, schema
capability and protocol. A per-repository ownership lock prevents two services;
lease/nonce checks prevent stale discovery records from identifying a new process
as the old service. Use appropriately restricted local IPC; do not expose a new
unauthenticated remote mutation endpoint.

MCP and viewer are adapters, not independent copies of mutable backlog state.
Read-only hooks may use versioned committed SQL views with no migration or
bootstrap side effects. If service ownership is unavailable, writes fail with a
clear recovery path; they do not silently start a competing legacy writer.

Cancellation before execution removes queued work; cancellation/transport loss
after admission may leave a committed operation. Request receipts resolve that
ambiguity. Preserve claim/lease and Linear outbox semantics. Network calls never
hold a DB transaction or repository synchronization lock.

## 9. Viewer, change feeds and compatibility

Board DTO: task rows once, bounded operational fields and epic/phase summaries;
no document bodies or private `_rows`. Detail endpoints provide requested prose.
Conditional GET checks a coherent scope revision before building a large result.
The client retains ETags; unchanged results return 304.

Scoped change cursors contain store identity, scope signature and sequence.
Rebuilds, scope changes or expired history require a fresh snapshot. Events carry
enough before/after membership to emit removals when an entity leaves a filter.
Do not expose half of a multi-entity commit as a complete client state: page at
commit boundaries or buffer grouped fragments until complete.

Existing tools, hooks, SQL query names and v4 artifacts remain a compatibility
surface with a documented support matrix. Compatibility SQL views are read-only;
update the authorizer for their underlying reads without granting direct access
to internal receipts, projection bases or new private tables. Preserve query
deadlines and caps. A materialization requested for compatibility is never an
unannounced normal-read cost.

## 10. Migration, downgrade and recovery

**Mandatory prerequisite:** today's `Store._prepare_schema()` calls
`_rebuild_for_version()` for a newer DB schema; that implementation drops tables.
A native schema must never be rolled out while this behavior can be exercised
by an active supported client.

Deliver a bridge release first: unknown newer schemas fail closed without DB or
projection mutation; runtime/projection capability markers are respected by all
entry points. Test already-open connections as well as cold opens. The existing
projection-version guard is defense in depth, not sufficient fencing by itself.

Native cutover requires quiescing/stopping old MCP servers, viewers, hooks and
workers and verifying the installed launch paths. Obtain exclusive migration and
publication ownership, reconcile pending exports/imports, and take a consistent
SQLite backup plus matching projection manifest. Back up queue rows, reservations,
tombstones, quarantine state and local-only data; projected files are not a full
DB backup. Active unknown/old clients block cutover.

Use additive schema and transactional backfill while compatibility adapters own
all writes. Verify counts, hashes, semantic DTO equivalence, FK/FTS integrity and
sequence preservation before switching the native capability marker. Do not
leave two independently writable representations after cutover. Interrupted
migration resumes from a durable stage or rolls back before activation.

After native writes, downgrading is not “run the old binary” or restoring a stale
snapshot. Roll back code only to a version capable of the current schema; a real
data downgrade needs a validated reverse migration including all new commits and
local-only state. The native-mode switch and any declared irreversible migration
step require an explicit release decision, separately from local implementation.

Unknown old binaries with filesystem access cannot be retroactively forced to
honor a new protocol. Supported deployment requires coordinated upgrade and
process exclusion; do not claim OS-level fencing against arbitrary old writers.

## 11. Acceptance

Correctness gates are mandatory; performance targets are benchmark goals:

- Same committed results for legacy/native operations and lossless artifacts.
- No unrelated entity/document loads, projection scans or global graph rebuild
  on a native metadata edit; bounded query work for bounded requests.
- Stable idempotent retries, monotonic revisions/events and coherent cursors.
- Four/eight/twelve clients with no lost acknowledgement, reused ID or partial
  composite; conflicts and timeouts are explicit and receipts recoverable.
- Export/import crash matrix, Git barrier tests and stale-client migration tests.
- Investigate the existing long-lived-connection FTS diagnostic before declaring
  concurrency acceptance complete; a fresh-connection `ok` alone is insufficient.
- Measure warm p50/p95/p99 with enough samples: target bounded reads <100 ms p95,
  DB command core <50 ms p95 and simple tool writes <250 ms p95 on the declared
  local fixture/hardware. Exclude transport/model latency explicitly; report
  adoption/export costs separately and revisit targets if durability hardware
  makes them unrealistic. No correctness requirement is relaxed to meet them.
