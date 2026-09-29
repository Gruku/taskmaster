# Native compatibility inventory and field ownership (N02)

Machine-readable contract: `tests/fixtures/native_contracts.json`.
It includes logical owners for every current writable allowlist field, explicit
unknown-field ownership for all 13 stored kinds, and lifecycle/gate constants.
Run `python scripts/native_contract_inventory.py` to check drift; `--write`
deliberately updates the reviewed snapshot. It parses source without importing
the server. A separate test compares the fixture against real MCP registration
in a disposable project, not merely another source scanner.

## API boundaries

The fixture records all 90 registered tools (83 at N02, 89 before N17, and `backlog_sync`,
added in N17), their parameter names/types/defaults,
return annotations, full public documentation, transaction wrappers, and action
dispatch targets. Categories describe their future core role, not a claim that
current reads avoid projection reconciliation. All current Store-backed reads
can still import external edits; that legacy behavior is retained until native
synchronization is explicitly activated.

Simple commands still have shared side effects: revision/events, validation,
FTS/relationships, projection, progress, reservations and optional Linear enqueue.
Composite commands additionally affect linked entities or lifecycle state.
The combined tools carry per-action classification so a read-only `list` is not
confused with a mutating `resolve`, `archive`, `link` or `retry`.

HTTP inventory includes GET/POST/PUT/PATCH/OPTIONS, regular-expression patterns
and exact `clean_path` branch conditions (including suffix routes such as task
related-data). GET returns committed snapshot identity where currently supported;
PUT/PATCH retain optimistic concurrency and gate rules. Preserve current status
codes, missing-object handling, body limits, CORS and repository-file safeguards.
Host preferences, session identity and file serving remain host services, not
entity commands. `/api/tasks/validate` and `/api/bugs/pattern-scan` are queries
despite using POST. Other entity POST/PUT/PATCH routes are command adapters.

Explicit additive changes since the freeze (N14): `backlog_dependencies` gained an
optional `depth` (strict int, default 1; over MCP a boolean, string or float is
refused by validation, never coerced). `depth=1` is the frozen answer byte for byte on
both stores; 2..10 appends bounded transitive sections (200 tasks per direction,
5 s deadline, cycles and truncation reported), identical on legacy and native.
Intentional difference: when an *unrelated* task has an unreadable `order` (e.g.
`null`), legacy `backlog_dependencies` raises while sorting every task; native
answers, because its reverse index never reads that task. Pinned by
`tests/test_native_dependency_graph.py::test_native_answers_where_legacy_raises_on_an_unrelated_null_order`.

## Behavioral contract sources and acceptance matrix

Public text results are not replaced with JSON merely because SQL is underneath.
Preserve missing-versus-null parameters, sentinel empty strings, slim defaults,
section selection, link expansion, sort order and overflow messages. `limit=0`
means uncapped where documented. Retain SQL's 500-row maximum and 5-second VM
deadline; a row limit alone does not bound work. Keep FTS porter/unicode61,
bm25 ordering and the existing searchable-kind set. The fixture retains the
current public docstrings, including historical descriptions that still mention
files; current behavioral tests/source take precedence over those descriptions.

| Contract family | Existing behavioral oracle suites |
|---|---|
| Status/get/list, slim/verbose, thread/continuity | `test_backlog_status_slim`, `test_slim_get_task`, `test_slim_*`, `test_thread*`, `test_continuity*` |
| Search and public SQL | `test_backlog_search_fts`, `test_backlog_query`, `test_store_derived_queries` |
| Task fields, batch and lifecycle | `test_store_server_integration`, `test_batch_update_gates`, `test_archive_transitions`, `test_viewer_write_gates` |
| Links, reverse edges, cycles | `test_backlog_link_*`, `test_auto_link_transaction`, `test_store_merge_and_derived` |
| Entity prose, scalars, unknown fields, import/export | `test_store_entity_round_trips`, `test_adoption_round_trips`, `test_migrate_restore_scalars` |
| IDs, tombstones, reservations | `test_store_id_allocation`, `test_adoption_never_deletes_data` |
| Transactions, rollback, concurrent acknowledgements | `test_store_transactions`, `test_store_concurrency`, `test_store_recovery` |
| Viewer committed revision and errors | `test_viewer_committed_state`, `test_viewer_reads_rows`, `test_api_*` |
| Linear configuration, enqueue, claims, retries | `test_linear_config_writer`, `test_linear_drain_claims`, `test_linear_tools`, `test_linear_worker` |

This is a compatibility baseline, not proof of native parity before native code
exists. N04–N10 must run the same behavior against native adapters and add paired
fixtures for any contract not yet covered. Exact dynamic response shapes/errors
are owned by those behavioral fixtures and adapters, not inferred from `-> str`.

## Canonical field ownership for N03

Ownership precedence is explicit: kind-specific mapping, common mapping, then
extension. A field has exactly one canonical owner. Compatibility JSON is a
read projection of these owners, never a second writable copy.

| Scope/fields | Native owner and preservation rule |
|---|---|
| All entity `(kind,id)` | `entity_core`: stable integer internal key; unique public identity; IDs retain current validation and allocation rules |
| `title`, `status`, `priority`, archive/deletion flags | `entity_core`; preserve absent versus explicit null; no title/name coalescing that loses either field |
| Current DB `rev`, `updated_seq` | `entity_core` revision/sequence; preserve tombstone identity and legacy sequence |
| `_body` / body API parameter | `entity_documents`: original stored prose and format; API `body` maps to the same owner |
| Task `epic`, `phase`, `order`, `stage`, `lane`, `estimate`, `owner`, `locked_by`, `branch`, `worktree`, `sub_repo`, `component`, `human_action`, `design_change` | Task operational row; optional reference resolution must not discard unresolved values |
| Epic/phase `name`, ordering and phase association; phase dates | Kind operational rows, retaining scalar representation/presence |
| Handover kind/date/thread/status, decision resolution state, bug/issue severity/state, note author/pinned, tracker external_system/external_key/instance_alias and sync hashes/times | Corresponding kind operational row; created/updated metadata retained; tracker `workspace_alias` is an API argument, stored as `instance_alias` |
| `depends_on` | Canonical dependency membership, preserving original order/multiplicity where observable and unresolved public IDs |
| `links` | Declared link relation with type/note/direction; identify derived inverse contributions separately; preserve current cycle/domain rules |
| Handover `tasks`, issue `related_tasks`, bug `adopted_into`, decision `task_id`, idea related tasks/issues | Typed membership/reference relations; preserve unresolved refs instead of adding destructive FK cascades |
| `anchors`, `location` | Interned path/pattern plus entity claim; preserve source, match semantics and multiplicity; no Windows case-folding change |
| Task `bundle`, `area`; epic area/components; entity components/tags | Membership/association rows with explicit ordinal/value representation |
| Any other known or unknown authored field (including `docs`, `notes`, `tldr`, `next_step`, gates/merge records and arbitrary nested metadata) | `entity_extensions`, keyed by entity/field; lossless JSON value/presence until deliberately promoted with a migration |
| Backlog configuration, `meta`, thread state and remaining root fields | Canonical configuration fields; thread state can be indexed/extracted per thread without a second authority. Epics/phases/tasks are relation membership, not nested canonical copies |
| Project manifest and arbitrary nested conventions | Canonical project/configuration document with explicit field addressing; preserve arbitrary extension keys |
| Linear configuration and viewer preferences | Separate configuration ownership; secrets remain environment references; viewer preferences remain machine-local |
| Derived `context`, `_rows`, tree nesting, slim indexes | Read projections only; never backfill as independent authored entities |

Kinds covered: task, epic, phase, handover, issue, bug, decision, idea, note,
area, tracker, backlog and project. Threads are currently configuration plus
handover associations, not a persisted independent entity kind.

JSON null, booleans, numbers, strings, arrays and objects must remain distinct;
absence requires an explicit presence representation when promoted to nullable
columns. Existing store JSON encodes Python date/datetime as ISO text. Backfill
preserves the authoritative stored representation; it must not invent timestamps
or reinterpret arbitrary strings. Read-only compatibility restores the prior
API's expected scalar behavior. Original prose bytes come from stored body;
projection line-ending policy remains separately owned by the exporter.

All current field allowlists are frozen in the fixture. Wildcard extension
ownership is intentional because users may author fields absent from those
allowlists. Adding an operational column later requires moving ownership, not
writing both a column and the same extension key. DDL and concrete codecs are
N03 deliverables; this map fixes their information-preservation requirements.

## Hooks, scripts, synchronization and SQL consumers

Read-only verification against the existing CodeMaestro copy found 3,559 rows
across 11 of these kinds and no unclassified kind. There are 42 distinct task
field names, 24 handover fields and 19 issue fields. Task `anchors` occurs as
both a string and a list; nullable severity, references, resolution metadata and
archive dates also occur. **Membership/path backfill must preserve container
shape as well as contents**, not turn every scalar claim into an array on
round-trip. A null reference differs from an absent reference. These are observed
data requirements, not hypothetical extension cases. The ignored
`test-results/native-foundation/field-types.json` records field/type counts only,
with no authored field values.

| Caller | Category / core boundary |
|---|---|
| `edit_resurface.py` | Committed query over entity paths, related graph and change sequence; advisory memo is local derived state. On native stores (N14) it reads the canonical neighbourhood (`Snapshot.neighbourhood` via `hook_reads`), not the `related` table; legacy stores are unchanged |
| `merge_gate_decide.py` | Committed task/gate query with established file fallback; system-Python admission |
| `merge_recorder_stamp.py` | Command adapter to `backlog_record_merge`; successful Git operation already occurred |
| `merge_gate.py`, `merge_recorder.py`, `.sh` wrappers, `run_hook.sh` | Host dispatch; preserve stdin/stdout/exit and fail-open behavior |
| `taskmaster_merge_approve.py` and shell wrapper | Host approval/enforcement policy; no new database authority |
| Worktree-submodule init and session-start hooks | Host Git/bootstrap guidance; native barrier integration belongs to N14 |
| `backfill_tldr.py`, `migrate_handover_statuses.py`, `migrate_links.py` | Maintenance commands; dry-run committed queries via `_store_rows.py`; writes use Store transaction |
| `check_adapter_coverage.py` | Distribution validation, no project mutation |
| Benchmark/reproducer/inventory scripts | Isolated development diagnostics; never production synchronization |
| Linear probe | External read; preserve token handling and network error shape |
| Linear bootstrap/link/unlink/retry | Configuration or composite command plus durable queue ownership |
| Linear worker/drain | Queue claim/lease/ack protocol and external side effect; receipts cannot imply external delivery before acknowledgement |

Legacy SQL names are frozen in the fixture: entities, changes, projection,
sessions, linear_queue, entity_paths, links, related, handover_tasks, entity_fts.
The authorizer also permits the FTS shadow reads needed by MATCH and sqlite_master;
meta/projection_base stay private. Native views must explicitly allow legitimate
view expansion without exposing new private tables.

`backlog_query` makes every public SQL name a compatibility consumer. The edit
hook directly consumes `related`; search consumes `entity_fts`; Store rebuilds
relationships from paths and handover-task associations. Audit scripts retain
the full graph as the oracle. Viewer related-data currently scans continuity
files instead of consuming the graph. Therefore removing `related` or changing
edge multiplicity before N14 would break a supported contract. Keep compatibility
views/materialization until these consumers migrate and equivalence passes.

### Graph SQL freshness (N14, decision F1 = A)

| SQL name | Native freshness | Maintained by | Repair |
|---|---|---|---|
| `entity_paths` | Current at commit | `relations.maintain`, per changed document | Full oracle |
| `links` (incl. `derived=1` mirrors) | Current at commit | `relations.maintain`, per changed link set, plus re-resolution of links to an id when that id is created | Full oracle |
| `handover_tasks` | Current at commit | `relations.maintain`, per changed handover | Full oracle |
| `related` | Current at commit | `relations.maintain`; path pairs through the indexed candidate search in `native/neighbourhood.py`, which costs the edited entity's candidates rather than every claim | Full oracle |
| `backlog_dependencies` `depth` (typed API, not SQL) | Current at read: canonical `dependencies` in the call's snapshot | No stored graph | None needed; bounds and parity are in the N14 step 5 additive-changes note |

All four tables are maintained incrementally inside the command's transaction, and
the full oracle is the repair operation. Rows, weights and multiplicity are the
frozen legacy contract: undirected sorted pairs, path weight = matching claim
pairs (glob-vs-glob, case-sensitive), one `handover` row per co-membership,
archived-but-live entities kept, deleted entities dropped. `backlog_query` still
materializes these tables in its per-call private snapshot, so its latency is
unchanged.

**Bug fixes: link target kinds (N14 review).**

- *Links written before their target.* A link is resolved when it is written, so a
  link to an id that did not exist yet recorded the `task` fallback kind and kept
  it after the target was created. The same drift existed in legacy incremental
  maintenance, while `rebuild_derived` resolved the kind correctly. Now, when an
  entity is created (native `relations.maintain` on creation; legacy
  `Store._refresh_derived` for every touched key), the declared `links` rows whose
  `dst_id` is that id are re-resolved with `_kind_for_id`, and their mirrors are
  re-derived.
- *Tie-break.* When one id belongs to several kinds, for example an epic and a
  phase both named `shared`, `_kind_for_id` orders task, then issue, then by kind
  name, on both stores. Before this fix, legacy had no tie-break, so the answer
  depended on insertion order.
- *Indexes.* Both lookups are index searches: the incoming rows use
  `ix_links_dst` with every stored kind listed. The kind uses
  `ix_entities_id(id,deleted,kind)` on legacy, a new `CREATE INDEX IF NOT EXISTS`
  in the schema script that runs on every store open. It needs no version bump,
  and older clients ignore it. On native it uses
  `ix_entity_core_public_id(public_id,deleted,kind)`, created by
  `neighbourhood.ensure_indexes` at backfill and on the first admitted command.
  A test asserts the query plans.

The public `links` rows now match `rebuild_derived` at commit, and legacy and
native produce identical rows. This is tested for both epic/phase insertion
orders, for targets that are an issue, a bug, a task or never appear, and for one
batch that creates entities linking to each other. A target that never appears
keeps the `task` fallback, which is the legacy answer.

**Repair operation.** `backlog_index_status(verify=True)` compares the four tables
with the full oracle and reports missing and spurious rows, with examples, the
entity count, rows compared and seconds. It changes nothing. `rebuild=True` runs the
native `graph.repair` command, which is admitted through `commands.execute` on its
own (never inside a batch). It replaces only the differing rows in one writer
transaction and appends no domain event, because derived rows are not authored
state. Only a repair that changed rows records `graph_repaired_at` and increments
`graph_repairs` in `native_manifest`. The hooks' dedupe revision on native stores
(`hook_reads.revision`) is the event high water plus `graph_repairs`, so a repair
that changed rows invalidates remembered hook answers. A clean repair changes
nothing. The oracle recomputes rows from canonical documents with
`relations.grouped_weights` plus the legacy link, mirror and handover rebuild
rules (`native/graph_repair.py`). No command or read path calls it. Backfill
leaves the shared graph tables untouched: it is a repeatable staging step, and
legacy still owns those rows. Instead, `migrate.repair_graph_for_activation` runs
the repair once, inside the activation transaction, after `authority` has been set
to `native` there. It refuses to run outside such a transaction. A crash rolls the
switch and the repair back together, and activation can simply be re-run. As a
result, a store activated from a drifted legacy store verifies clean. Every repair
records `graph_checked_at`, which `backlog_index_status` shows as "Rebuilt:". Only
`graph_repaired_at` and `graph_repairs` feed the hook revision.
With `rebuild=True, verify=True` on a native store, the repair runs. On a legacy
store, `rebuild=True` alone is the unchanged `Store.rebuild_derived`, and any call
with `verify=True`, with or without `rebuild`, is refused and changes nothing. On
native, `rebuild` covers only these four graph tables, not the search table.
Measured on a synthetic store with 4,000 tasks: 15,998 `links` rows, 23,583
`entity_paths` rows (19,583 of them prose), 9,351 `related` rows, 97,864 rows
compared. Verify took 0.45 s. Repair took 0.55 s with 20 `links` rows missing and
0.51 s when clean. Backfill took about 1.05 s. The one-time activation repair took
0.45 s (0 differences).

**Explicit additions.** `Snapshot.neighbourhood(kind, id, limit)` returns distinct
`(kind, id, via, weight)` neighbours, with weight summed over `related` rows. The
optional `verify` parameter and the native `graph.repair` operation are also new.
Neither adds top-K ranking or changes relevance.

**Known limits.**

- `Snapshot.neighbourhood`, like `Snapshot.relations()`, pages with `truncated`
  only and has no continuation cursor.
- The canonical `declared_links` and `memberships` rows keep the `task` fallback
  in `target_kind` for a target written before it existed. Only the public
  `links` table is re-resolved. `Snapshot.relations()` and `references_to()`,
  which read these rows, have no production caller today. Correcting them is
  future work.

**Later capability, not unfinished work.** Materializing `related` on demand from
canonical claims (option B) instead of maintaining it at commit is recorded as a
possible later capability. This release does not need it.
