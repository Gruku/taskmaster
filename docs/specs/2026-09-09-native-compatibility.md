# Native compatibility inventory and field ownership (N02)

Machine-readable contract: `tests/fixtures/native_contracts.json`.
It includes logical owners for every current writable allowlist field, explicit
unknown-field ownership for all 13 stored kinds, and lifecycle/gate constants.
Run `python scripts/native_contract_inventory.py` to check drift; `--write`
deliberately updates the reviewed snapshot. It parses source without importing
the server. A separate test compares the fixture against real MCP registration
in a disposable project, not merely another source scanner.

## API boundaries

The fixture records all 83 registered tools, their parameter names/types/defaults,
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
| `edit_resurface.py` | Committed query over entity paths, related graph and change sequence; advisory memo is local derived state |
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
