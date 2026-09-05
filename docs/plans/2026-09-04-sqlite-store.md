<!-- User intent: turn the approved SQLite-authoritative store design into a mergeable,
     test-first implementation sequence for spec section 6 steps 1 and 2. -->

# SQLite Store Implementation Plan — Steps 1–2

> **For agentic workers:** implement one numbered step per worktree. Pin the strongest
> available implementation model, keep each red/green cycle visible, request a fresh-context
> review plus a Codex review after each step, and do not push without explicit approval.

**Goal:** Replace Taskmaster's stale full-tree read/modify/write foundation with a SQLite
runtime authority that supports concurrent processes and thread-pool calls without losing
writes, while retaining the current v4 files as the committed projection.

**Architecture:** `taskmaster/store.py` owns root resolution, the authoritative database,
transactions, import/export, projection recovery, and compatibility-shaped reads. Step 1
builds and exhaustively unit-tests that module without changing a production caller. Step 2
wires the existing server through `transaction_dict()`, removes every task/epic/phase bypass,
and proves the five reported write-loss defects are fixed with integration and multiprocess
tests. Row-level hot paths and non-task entity writers remain for spec steps 3–5.

**Tech stack:** Python 3.11, stdlib `sqlite3`, `threading`, `weakref`, `hashlib`, `subprocess`,
`pathlib`, PyYAML through `taskmaster.yaml_io`, pytest. No new runtime dependency.

**Authoritative spec:** `docs/specs/2026-09-04-sqlite-store-design.md` rev 2.

## Global constraints

- SQLite is authoritative after bootstrap. `backlog.yaml` and entity markdown are a
  projection, never an alternate source of truth during a write.
- Resolve one store per repository. `TASKMASTER_ROOT` wins; otherwise use the parent of
  `git rev-parse --git-common-dir`; otherwise use the start directory. A linked worktree must
  use the main checkout's `.taskmaster/local/store.db`.
- Do not modify or remove the pre-existing root `uv.lock` change. Never write test data into
  CodeMaestro or another real backlog.
- Keep `taskmaster_v3.py` parsing, rendering, path, and three-way-merge helpers pure and
  reusable. Only `store.py` may open `store.db` or write projection entity files.
- Use WAL, explicit `BEGIN IMMEDIATE`, `synchronous=FULL`, `busy_timeout=30000`, and
  `foreign_keys=ON`. Every transaction rolls back in `finally` unless commit completed.
- A successful mutation is read from committed state. Step 1 exposes committed sequence
  metadata, but result-string `[seq N]` suffixes land in spec step 3.
- Missing projection files never delete rows. Only `tx.archive()` or `tx.delete()` may set
  archive/tombstone state or queue a file move/removal.
- An unparseable/conflicted projection is quarantined and never overwritten until repaired or
  removed. An export failure commits the DB row, records `dirty=1` plus `export-fail`, and is
  retried by the next transaction.
- Treat only narrowly identified corruption (malformed/not-a-database/encrypted or failed
  `quick_check`) as rebuildable. An `OperationalError`, especially `database is locked`, must
  never rename or rebuild a database.
- Each test is written first and observed failing for the intended reason. Run focused tests
  after each green change and `uv run pytest -q` before each step commit.

## Step 1 worktree — authoritative store module, no callers wired

**Branch/worktree:** `feature/sqlite-store-step-1` at `.worktrees/sqlite-store-step-1`, created
from the plan commit on `master`.

**Files:**

- Create `taskmaster/store.py`.
- Create `tests/test_store_root.py`.
- Create `tests/test_store_schema.py`.
- Create `tests/test_store_transactions.py`.
- Create `tests/test_store_projection.py`.
- Create `tests/test_store_import.py`.
- Create `tests/test_store_recovery.py`.
- Create `tests/test_store_id_allocation.py`.
- Add only store-specific fixtures/helpers to `tests/conftest.py` if sharing them materially
  reduces duplication. Existing `tmp_taskmaster` must continue to work unchanged.

### 1.1 Public surface and ownership boundary

Implement and type the following stable surface; helpers may stay private:

```python
SCHEMA_VERSION = 1
PROJECTION_SCHEMA = 5
DB_RELPATH = Path("local") / "store.db"

@dataclass(frozen=True)
class RootResolution:
    root: Path
    backlog_path: Path
    source: str                 # env | git-common-dir | cwd | explicit
    filesystem_warning: str | None

@dataclass(frozen=True)
class StoreStatus:
    root: Path
    db_path: Path
    creation_token: str
    max_seq: int
    dirty_files: tuple[str, ...]
    quarantined_files: tuple[str, ...]
    warning: str | None

def resolve_root(start: Path | None = None, *, explicit_root: Path | None = None) -> RootResolution
def db_path(backlog_path: Path) -> Path
def open_store(backlog_path: Path | None = None, *, root: Path | None = None,
               session: str | None = None) -> Store
def load_dict(backlog_path: Path | None = None) -> dict
def transaction(*, tool: str, backlog_path: Path | None = None) -> ContextManager[Transaction]
def transaction_dict(*, tool: str, backlog_path: Path | None = None) -> ContextManager[dict]
def status(backlog_path: Path | None = None) -> StoreStatus
def close_thread_connection() -> None
def checkpoint_all() -> None
def reset_for_tests() -> None
```

`Store` owns bootstrap/rebuild/scan/export/cache behavior. `Transaction` exposes
`get(kind, id)`, `put(kind, id, doc, body=None)`, `create(kind, doc, body=None,
requested_id=None)`, `archive(kind, id)`, `delete(kind, id)`, `committed`, `seq`, and warnings.
No `backlog_server` import is allowed from `store.py`; inject a session id and reuse pure
helpers from `taskmaster_v3.py` to avoid a cycle.

The current canonical tracker projection is `.taskmaster/trackers/*.md`; import that path.
Treat `integrations/trackers` as legacy/import-only unless a later migration decision changes
the committed v4 layout. Handover archives are `handovers/_archive/<year>/*.md`; accept any
legacy `handovers/archive/*.md` and `issues/archive/*.md` files on import without inventing a
new canonical issue-archive writer in this step.

### 1.2 Red tests: root, platform, connection, and schema

Write `tests/test_store_root.py` first:

- explicit root and `TASKMASTER_ROOT` precedence;
- main checkout resolution from `.git` and linked worktree resolution from the common dir;
- non-git cwd fallback;
- all returned paths are absolute and normalised;
- network filesystem classification refuses `BEGIN IMMEDIATE` writes but permits bootstrap
  reads; cloud-synced paths emit one process warning and proceed. Platform probes are injected
  or monkeypatched so tests are deterministic on every OS.

Write `tests/test_store_schema.py` first:

- first open creates `.taskmaster/local/.gitignore` with `*\n`, `store.db`, every table/index
  in spec §3.2, `schema_version`, and a UUID `creation_token`;
- PRAGMAs are WAL/FULL/foreign-keys-on/30-second busy timeout;
- one connection is reused within a thread, different threads get different connections, and
  `close_thread_connection()` releases the current one; `reset_for_tests()` clears root,
  connection, and cache state so pytest cwd/root changes cannot cross-contaminate fixtures;
- compatible schema opens in place; a supported migration runs in place; an unsupported
  version rebuilds from files; a newer `meta.projection_schema` refuses to open;
- creation-token changes invalidate all cached dict state.

Implement only enough root/open/schema code to turn these tests green. Keep SQL in one
idempotent schema constant, use `(kind,id)` keys throughout derived tables, and register each
session with pid/host/cwd/current tool.

### 1.3 Red tests: transaction semantics and compatibility dict

Write `tests/test_store_transactions.py` first:

- `BEGIN IMMEDIATE` reads a fresh row and `put()` records exact changed top-level fields,
  before/after values, `rev+1`, `updated_seq`, session, tool, and one committed `changes.seq`;
- unchanged `put()` is a no-op and does not increment revision or sequence;
- an exception rolls back entities, changes, derived rows, and queued projection work;
- abandoned pooled-thread work cannot leave an open transaction;
- `committed[(kind,id)]` is available only after commit and matches a fresh SQL read;
- `transaction_dict()` yields the current public backlog shape; nested `load_dict()` returns
  that same object by identity; deleted top-level fields are applied per-field; missing
  entities are ignored; explicit `archive/delete` is required;
- a writer queued behind an active compatibility transaction sees the committed state after
  acquiring `BEGIN IMMEDIATE`; both changes survive. Separately, a process with stale cached
  state refreshes before `BEGIN` and preserves a peer's creation and independent field update;
- cache identity is `(resolved db path, creation_token, max changes seq)` and refreshes only
  rows newer than the cached sequence;
- `context` and `_orphan_tasks` are derived/runtime-only and never appear in `entities.doc` or
  backlog.yaml.

Implement `Transaction`, `Store.transaction()`, `Store.transaction_dict()`, cache refresh,
JSON canonicalisation, and the dict↔row mapping. Keep transactions short and ensure every
cursor is exhausted/closed before returning.

### 1.4 Red tests: bootstrap/import scan

Write `tests/test_store_import.py` first using only temp backlogs:

- no DB imports untouched v4 task/epic/phase/backlog/project rows and every file-backed kind
  listed in spec §3.7, including archived issues, nested archived handovers, notes, areas, and
  integration trackers;
- a v3 backlog imports inline tasks and immediately projects an equivalent v4 layout;
- candidate selection uses stat metadata and then hashes; backlog/project are always hashed;
  changed git generation files force a full hash scan, including a same-size/same-mtime edit;
- an unknown valid file creates a row with `op=import`; a changed clean file updates exact
  fields; a tombstoned id is deliberately resurrected by an external file;
- missing files are queued for re-export; an archive-directory move imports as
  `archived=1`, never delete/create;
- conflict markers, invalid YAML, and invalid frontmatter set `quarantined=1`, preserve the DB
  row, log a warning, and suppress export;
- a whole scan is atomic: injected parse/derived-refresh failure leaves the prior committed
  inventory unchanged;
- a dirty-row external edit uses `_three_way_merge_fields`; the DB wins true field conflicts,
  the independently changed body wins, and the foreign body is retained in change history;
- ignore dotfiles, lock files, temp files, and corrupt backups.

Build the importer as inventory → candidate hash → parse → row diff. Keep a registry per kind
for glob, archive glob, parser, renderer, and path resolver so the complete §3.7 inventory is
testable rather than encoded in scattered conditionals.

### 1.5 Red tests: export and crash recovery

Write `tests/test_store_projection.py` first:

- create/update exports only the touched entity file; backlog.yaml changes only for
  backlog/epic/phase rows and contains `meta.projection_schema: 5` but no `context` or tasks;
- bytes round-trip arrows, parentheses, Unicode, and YAML-significant strings;
- temp name is `<file>.tmp.<session>`; hash/mtime/size are taken from the open temp file before
  replace; no temp survives success;
- Windows-style sharing failures retry with jitter for at most two seconds;
- exhausted replace retries commit the row, mark projection dirty, add `export-fail`, retain
  `projection_base`, and return an export-pending warning;
- the next transaction imports/merges an intervening hand edit before draining dirty output;
- a file missing while dirty is simply re-exported;
- archive/delete moves update both projection paths atomically; tombstones remain forever;
- PROGRESS regeneration is throttled by store sequence to once per five seconds and failures
  do not fail a transaction;
- exported transactions request a PASSIVE checkpoint after commit.

Write `tests/test_store_recovery.py` first:

- corrupt signatures and failed `quick_check` rename `store.db`, `-wal`, and `-shm` to one
  timestamped `corrupt-*` family, then rebuild from projection;
- backups older than seven days are pruned, newer backups remain and appear in status;
- `database is locked`, `unable to open`, permissions, and other `OperationalError`s never
  trigger corruption recovery;
- an actual 30-second busy path is covered by a short injected timeout: the error uses a
  separate diagnostic connection and names live probable holders/current tools plus the last
  writer; if diagnostics also fail, return the exact bounded fallback;
- PASSIVE/TRUNCATE checkpoint failures are best-effort and never rewrite/rename the DB.

Pre-render and prewrite every touched output before the first `os.replace`, then make the
replace/metadata phase non-fallible except for per-file retry exhaustion, which is converted to
`dirty=1`. If an unexpected exception occurs after any replacement, restore replaced files
from `projection_base` before rollback. Add fault injection after the first of multiple
replacements so uncommitted bytes can never be re-imported as an external edit.

### 1.6 Red tests: allocation and derived rows

Write `tests/test_store_ids.py` first:

- task/bug/issue/decision/idea/note numeric suffixes take the maximum across live, archived,
  tombstoned rows and live/archive directory scans;
- requested ids are checked against every one of those sources;
- concurrent allocators produce unique ids via the `(kind,id)` primary key;
- duplicate handover slugs get `-2`, `-3`; duplicate areas/trackers fail clearly;
- FTS, paths, links, related, and handover-task rows refresh in the same transaction and are
  removed/rebuilt on update/archive/delete;
- a derived-table refresh failure rolls back the authoritative row too;
- rebuilding derived tables never deletes or rewrites `entities`, `changes`, or `projection`.

Reuse the current allocator directory scanners and the current derived-index normalisation
where possible, but adapt all joins and uniqueness to `(kind,id)`.

### 1.7 Step 1 verification and review gate

Run, in order:

```powershell
uv run pytest -q tests/test_store_root.py tests/test_store_schema.py
uv run pytest -q tests/test_store_transactions.py tests/test_store_id_allocation.py
uv run pytest -q tests/test_store_import.py tests/test_store_projection.py tests/test_store_recovery.py
uv run pytest -q
```

Then run a fresh-context review and an independent Codex review focused on:

- import/export three-way merge and failure ordering;
- worktree/common-dir root resolution and test isolation;
- corruption classification versus busy/transient errors;
- connection lifetime and thread/process transaction safety;
- row/file bidirectional invariants and non-recycling ids.

If this review finds the sync surface cannot be made bounded and auditable, stop before step 2
and invoke the spec §8 contingency (SQLite mutex + id sequence, files authoritative) as a new
design decision. Otherwise resolve findings, rerun the full suite, and commit:

`feat(store): add SQLite-authoritative transactional store`

## Step 2 worktree — server wiring and concurrency proof

**Prerequisite:** step 1 review passed and its commit is on `master`.

**Branch/worktree:** `feature/sqlite-store-step-2` at `.worktrees/sqlite-store-step-2`, created
from the merged step 1 commit.

**Files:**

- Modify `taskmaster/backlog_server.py`.
- Modify `taskmaster/taskmaster_v3.py` only to remove/deprecate write ownership and retain pure
  parsers/renderers/path/merge helpers.
- Modify `tests/conftest.py` for store bootstrap/reset and bypass detection.
- Create `tests/test_store_server_integration.py`.
- Create `tests/test_store_concurrency.py`.
- Create `tests/test_store_bypass.py`.
- Update existing viewer/task/v4 tests only where their asserted persistence boundary changes;
  do not weaken behavior assertions.

### 2.1 Wire the compatibility boundary first

Write failing integration tests, then:

- replace `_load()` with `store.load_dict()`;
- remove `_LOAD_SNAPSHOT` and `_backlog_lock`;
- make `_mutate_and_save(data)` validate that `data` is the active thread-local dict inside
  `store.transaction_dict()`, explicitly latch the transaction for commit, and raise outside
  that scope. Context exit without that latch rolls back so validation errors and early returns
  cannot persist partial in-memory mutations;
- wrap every existing `_load`→mutate→`_mutate_and_save`/`_save` call in one named
`transaction_dict(tool=<public tool name>)` context;
- keep nested `_load()` calls identity-stable inside `backlog_complete_task`,
  `_load_related_for_task`, `_expand_fm_links`, and Linear enqueue logic;
- move `_auto_link_on_save` into the same transaction rather than doing a post-commit write;
- render responses from `tx.committed`/a fresh committed read, not the requested local value.

Do not convert the hot tools to row APIs here; that is spec step 3. The compatibility boundary
must become correct before optimization.

### 2.2 Audit every implicit removal

Use a syntax-aware audit plus `rg` to enumerate mutations of `epics`, `phases`, and task
lists. Tests must prove each removal is explicit. At minimum convert:

- `backlog_archive_task` to `tx.archive("task", task_id)`;
- `backlog_archive_epic` to explicit archive operations for the epic and its intended tasks;
- task moves between epics/phases to explicit `put()` operations without transient deletion;
- canonicalize/migrate paths that remove dict members to explicit archive/delete or a
  deliberately deferred external-editor path.

Compatibility write-back continues to ignore missing entities. Add a regression test for
every removal site found by the audit.

### 2.3 Delete production bypass writers

Write `tests/test_store_bypass.py` and an autouse guard that rejects writes to
`backlog.yaml`, `tasks/`, `epics/`, and `phases/` unless the call stack enters
`taskmaster/store.py`. Patch/guard at least
`atomic_write`, `write_task_file`, `Path.write_text`, `Path.write_bytes`, `os.replace`, and
`shutil.move` for projection paths while exempting temp-fixture setup before store bootstrap.

Then remove or reroute:

- `_save()` and direct `_atomic_write` entity writes in `backlog_server.py`;
- viewer `taskmaster_v3.create_task`, `update_task`, `archive_task`, `with_file_lock`, raw
  backlog reads, stale file-mtime ETags, and every POST/PUT/archive handler;
- `_write_local_meta_cache` authority assumptions;
- direct task/epic/phase file deletion or moves in `taskmaster_v3.py` production paths.

Non-task bug/issue/handover/decision/idea/note/area/tracker writers scheduled for spec step 3
remain on a narrow temporary allowlist and are named in the step-2 changelog. Do not imply
those ids are concurrency-safe yet; the allowlist is removed in step 3. No task/epic/phase or
viewer write bypass is permitted.

### 2.4 Concurrency and defect-regression tests

Add deterministic tests for all five defects:

- same process, same thread: sequential writes never use an older snapshot;
- same process, thread pool: parallel status/human-action/branch writes all persist;
- two processes: a compatibility transaction and a direct store transaction preserve
  independent fields and creations;
- 8 processes × 200 mixed real task/epic/phase public-tool operations: add, update, gate,
  merge, pick, complete, archive, and batch update; assert every returned sequence exists, ids are globally unique,
  asserted fields survive, no temp/dirty rows remain, and row↔file hashes match both ways;
- Windows open-handle contention commits DB truth and converges projection within the retry
  window;
- server launched from a linked worktree opens the main checkout store and never modifies the
  worktree's tracked `.taskmaster/` files;
- viewer GET reads `load_dict()` and ETag is `creation_token:max_seq`; all mutations use the
  same transaction path as MCP tools;
- `backlog_index_status(rebuild=True)` calls `Store.rebuild_derived()` and never unlinks or
  replaces the authoritative database;
- existing v3/v4 migration and all 182 baseline tests remain green.

Make stress parameters overridable by environment variable for local debugging, but keep the
specified 8×200 profile in the committed default test.

### 2.5 Step 2 verification, review, and commit

Run:

```powershell
uv run pytest -q tests/test_store_server_integration.py tests/test_store_bypass.py
uv run pytest -q tests/test_store_concurrency.py
uv run pytest -q tests/test_v4_two_process.py tests/test_v4_server.py tests/test_server_task_write.py
uv run pytest -q
```

Repeat the fresh-context and Codex reviews with emphasis on bypass completeness, implicit
removals, transaction nesting, real-tool stress coverage, viewer ETag semantics, and the
temporary step-3 limitations. Resolve every critical/important finding before merge. Commit:

`feat(store): route task backlog writes through SQLite transactions`

The step-2 changelog must state the boundary exactly: defects 1–5 are fixed for tasks, epics,
and phases; bug/issue/handover id allocation remains on the compatibility path until spec step
3. Do not claim the full migration complete until steps 3–5 and real CodeMaestro open/status
verification have shipped.

## Execution status — 2026-09-04

- Step 1 is implemented in `taskmaster/store.py` with 133 focused store contract tests.
- The full repository suite passes: 1,791 passed and 1 skipped.
- The implementation received a fresh adversarial review; confirmed critical and high findings
  were resolved with regression coverage before commit.
- Step 2 remains planned above and is intentionally not implemented in this commit.

## Execution status — 2026-09-05 (step 2)

- Step 2 is implemented on `feature/sqlite-store-step-2`: every task, epic and phase read
  and write in `backlog_server.py` runs inside one store transaction per public tool call,
  the projection bypass writers for those three kinds are gone, and the viewer reads and
  writes committed state.
- A whole-branch review (Claude) and an adversarial review (Codex, `codex-review.md`)
  produced twelve findings; all twelve were closed in a single fix wave, each with a
  regression test observed failing first. See `fix-wave-report.md`.
- Boundary, unchanged from the plan: defects 1–5 are fixed **for tasks, epics and phases
  only**. Bug, issue, handover, decision, idea, note, area and tracker writers stay on the
  compatibility allowlist and **their id allocation is still not concurrency-safe**. Step 3
  moves them onto the store.
- Also deferred to step 3: generalizing committed-state response rendering beyond
  `backlog_update_task`. Every other mutating tool renders from the in-flight transaction
  dict, which is safe only because a failed commit raises instead of returning success.
- New in step 2, and breaking: a `.claude/` or project-root backlog is refused with an
  actionable message rather than silently redirected to an empty store. The one supported
  route forward is `backlog_canonicalize_layout`, which now also moves `epics/`, `phases/`,
  `bugs/`, `decisions/`, `ideas/`, `notes/` and `integrations/`, and refuses to run on
  network storage.
- Full repository suite after the fix wave: 1,926 passed and 1 skipped in 13m30s.
- Committed stress profile (plan-mandated, run by default): 8 processes x 200 real public
  tool calls against one shared store — 777 entities created, 2,186 tool calls (30 legitimately refused), 363.8s wall clock, with every operation class asserted from committed rows.
- Do not claim the full migration complete until steps 3–5 and real CodeMaestro open/status
  verification have shipped.

---

# Steps 3–5 — written 2026-09-05 after Step 2 merged (`e9026e7`)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. One
> fresh implementer per section below, TDD inside each, one commit per section, review between
> sections. Code map with line numbers at commit `c67ecf5`:
> `.superpowers/sdd/2026-09-05-sqlite-store-steps-3-5/code-map.md` (gitignored workspace).

**Goal:** finish the spec: every kind and every writer on the store, derived index absorbed,
hooks and viewer store-backed, docs and scripts updated, release cut.

**Branch/worktree:** `feature/sqlite-store-step-3` at `.worktrees/sqlite-store-step-3` (from
`c67ecf5`). Steps 3, 4 and 5 all land on this one branch as separate commits per section; the
branch is merged into `master` once per step (`--no-ff`) after that step's review gate.

**Spec:** `docs/specs/2026-09-04-sqlite-store-design.md` §3.3–§3.9, §4.2–§4.8, §5, §6.

## Global constraints (in force for every section)

- `taskmaster/store.py` remains the only module that opens the database or writes a projection
  file. `taskmaster_v3.py` keeps pure parse/render/validate/path helpers and loses every writer.
- Every public tool call owns exactly one store transaction; nested calls reuse it.
- Removal is never implicit; archive/delete are explicit `tx.archive` / `tx.delete` calls.
- Responses are rendered from committed state; a failed commit raises, never returns success.
- Ids are allocated inside the creating transaction from authoritative state.
- No file under `.taskmaster/` is written by anything but `store.py`; the test guard enforces it.
- Do not push. Do not touch `uv.lock`. Full suite: `uv run pytest -q --basetemp=%TEMP%\tm-pytest`.
  Fast loop: `uv run pytest -q -m "not slow"` once §3.1 lands.
- Every new file starts with a 1–3 line `User intent:` header comment.

## Rulings (made on the user's behalf; reverse if wrong)

| # | Ruling | Why |
|---|---|---|
| R1 | Tracker canonical path is `trackers/<id>.md` (today's writer). `integrations/trackers/*.md` stays an import fallback and is moved to the canonical path on first export. | Matches current `write_tracker`; the store already does this. |
| R2 | `ideas/IDEAS.md` becomes derived output: the exporter regenerates it whenever an `idea` row is touched, the scan never imports it. | It is an index, not an entity; two writers of one file is the defect we are removing. |
| R3 | The `bugs`/`issues`/`handovers`/`trackers` index arrays inside `backlog.yaml` stay (git-facing summary) but are rebuilt from store rows inside the transaction, never from a directory glob. | Keeps the projection shape stable for 5.2.x readers and `PROGRESS.md`. |
| R4 | Validators (`_validate_bug` … `_validate_area`) run at the tool boundary before `tx.create`/`tx.put`; they stay in `taskmaster_v3.py` as pure functions. | The store is kind-agnostic; invariants belong to the kind. |
| R5 | Handover id suffixing (`-2`, `-3`) moves into `Transaction.allocate_id("handover", doc)` using `id_taken`. Area/tracker ids stay caller-derived and a collision raises. | Spec §3.5 verbatim. |
| R6 | Committed-state rendering is generalized with a per-call renderer **stack** on the frame; `[seq N]` is appended by `_transactional` to every latched tool's string result. | Closes the nested-frame clobber; spec §3.8. |
| R7 | The 8×200 stress test keeps its default profile and gains `@pytest.mark.slow`; the fast loop is `-m "not slow"` (native pytest, no config needed). | Plan §2.4 mandated the default; the marker costs nothing. |
| R8 | The Linear queue moves to the `linear_queue` table; `linear-queue.json` is imported once on first open if present, then removed by the store. | Spec §4.3. |
| R9 | `index.py` is deleted except `normalize_task_anchor`, `normalize_location`, `extract_prose_paths`, `infer_repo`, `REVERSE_TYPE`, which move to a new `taskmaster/paths.py`. `local/index.db` is unlinked by `backlog_index_status(rebuild=True)` and ignored otherwise. | Spec §4.7, decision 5. |
| R10 | Hooks never trigger a full import. `edit_resurface` and `merge_gate_decide` open `store.db` (normal connection, query guard) when it exists, else fall back to reading files and log. | Spec §4.5. |
| R11 | Step 5 CodeMaestro verification runs against a **copy** of `C:\Users\gruku\Files\Work\CodeMaestro\.taskmaster` in the scratchpad, not the live repo. Opening the live repo (which migrates it) is left to the user. | Adopting rewrites 2.2k files in the user's real project. |
| R12 | Version bumps to `6.0.0`. | Breaking: legacy layouts refused, `context` gone from backlog.yaml, index.db gone. |

---

## Step 3 — every writer on the store; hot-path row API; status tool; `[seq N]`

### 3.1 Test infrastructure first

**Files:** `tests/conftest.py`, `tests/test_store_concurrency.py`, `tests/test_store_bypass.py`.

- Extend `_GUARDED_DIRS` to `("tasks","epics","phases","bugs","issues","handovers","decisions",
  "ideas","notes","areas","trackers","integrations")`; teach `_guard_path` about
  `handovers/_archive/<year>/` and `notes/_archive/` (two levels) and about `ideas/IDEAS.md`
  and `integrations/linear-queue.json` (file names, like `backlog.yaml`).
- Invert `test_guard_lets_non_task_entity_writers_through` into
  `test_guard_trips_on_a_production_bug_file_write` (a `_write_as_production` into `bugs/`
  raises). Add one parametrized red test per kind directory.
- `@pytest.mark.slow` on the 8×200 stress test (R7). Document `-m "not slow"` in its docstring.
- Commit: `test(store): guard every entity directory and mark the stress run slow`. The suite
  is now red for every non-task writer — that is the point; sections 3.2–3.5 turn it green.

### 3.2 Store API additions

**Files:** `taskmaster/store.py`, `tests/test_store_transactions.py`, `tests/test_store_id_allocation.py`, `tests/test_store_projection.py`.

Red tests, then implement:

- `Transaction.list(kind, *, include_archived=False) -> list[tuple[str, dict, str | None]]`
  returning `(id, doc, body)` for live rows of `kind`, deterministic order by id.
- `Transaction.allocate_id("handover", doc)` derives `make_handover_id(doc["date"], doc["tldr"])`
  and suffixes `-2`, `-3`, … while `id_taken` (R5). `issue` allocation must see
  `issues/archive/` (already does via `_known_entity_files`; add the regression test).
- Exporter: `ideas/IDEAS.md` rendered from idea rows whenever any `idea` row is in
  `_export_keys` (R2); the file is recorded in `projection` with kind `ideas-index`, id NULL,
  and `_scan_projection` skips kind `ideas-index` files (never parsed, hash refreshed only).
  Renderer: move the body of `taskmaster_v3._write_ideas_index` to a pure function
  `render_ideas_index(entries) -> str` in `taskmaster_v3.py`.
- Archive moves for `bug`, `issue`, `note`, `handover` go through `tx.archive` and the existing
  `_entity_path(..., archived=True)`; add a test per kind that the old file is gone, the new
  file exists, and both `projection` rows are updated in one transaction.
- Linear queue (R8): `Transaction.linear_enqueue(op, target_id, tracker_id, payload) -> int`,
  `Store.linear_pending(limit) -> list[dict]`, `Store.linear_mark(seq, *, state, error=None)`
  (each `mark` is its own short `BEGIN IMMEDIATE`). First open imports
  `integrations/linear-queue.json` rows as `state='pending'` and deletes the file inside that
  transaction.
- `Store.force_scan_on_next_read()` public method replacing external pokes of
  `_last_read_scan_clock`.
- Commit: `feat(store): list rows, handover ids, ideas index, archive moves, linear queue`.

### 3.3 Non-task writers through the store

**Files:** `taskmaster/backlog_server.py`, `taskmaster/taskmaster_v3.py`,
`tests/test_store_server_integration.py`, plus the existing per-kind test files (`test_areas.py`,
`test_decision_*.py`, `test_handover_*.py`, `test_api_notes.py`, …) only where their asserted
persistence boundary moves.

For each kind, in this order — bug, issue, decision, idea, note, area, tracker, handover:

- Every tool/viewer handler listed in code-map §A.2 gets `@_transactional` (or its viewer
  equivalent `_transaction(tool="viewer:…")`) and replaces the `taskmaster_v3` writer call with
  `tx = _store_tx()`; doc validated by the kind validator (R4); `tx.create(kind, doc, body=body)`
  / `tx.put` / `tx.archive`. Reads inside those tools use `tx.get`/`tx.list`, never `read_*`.
- The `sync_*_index(backlog_data, backlog_path)` helpers change signature to
  `sync_*_index(backlog_data, rows)` where `rows` is `tx.list(kind)` output (R3);
  `sync_handover_index` archives overflow via `tx.archive("handover", id)` — pass `tx` in.
  `sync_thread_registry` likewise.
- Composite ops stay one transaction: `promote_bugs_to_issue` (bug puts + issue create),
  `backlog_complete_task` (task + handover smart-close + bug archive), `link_decision_to_handover`,
  `backlog_link_create/remove/reconcile` (rewrite `write_entity_anywhere` so **every** kind goes
  through the configured store hook, not only tasks; `read_entity_anywhere` reads `tx.get`).
- Delete from `taskmaster_v3.py`: `write_bug`, `update_bug`, `archive_bug`, `write_issue`,
  `update_issue`, `write_handover`, `apply_supersession`, `apply_handover_review_flag`,
  `update_handover_status`, `smart_auto_close_handovers` (becomes a pure planner returning ids
  to close), `backfill_handover_status`, `migrate_handover_statuses` (pure: returns the new
  docs), `archive_handover`, `write_decision`, `update_decision`, `resolve_decision`,
  `drop_decision`, `link_decision_to_handover`, `write_idea`, `update_idea`, `_write_ideas_index`,
  `write_note`, `update_note`, `archive_note`, `write_area`, `update_area`, `write_tracker`,
  `update_tracker`, `next_bug_id`, `next_issue_id`, `next_decision_id`, `next_idea_id`,
  `next_note_id`, the `list_*_ids` globbers used only by writers, `atomic_write` callers for
  `project.yaml` (`backlog_project_set/init` → `tx.put("project", …)`), and
  `_write_local_meta_cache`'s `_atomic_write` (route through a `Store.write_local_cache(name, bytes)`
  helper so the guard's stack rule holds).
- Delete `_sync_projection()` and all seven call sites; delete `_store_for_read()` and the poke
  in `_store_read_task`; `_load_snapshot` uses `_store()`.
- `_ensure_handover_status_backfilled`: set the durable flag whether or not the call owned the
  transaction (code-map §A.2 defect).
- `backlog_update_epic(id, "status", "archived")` applies the same task cascade as the batch
  path (step-2 re-review item).
- Read tools for these kinds (`backlog_bug_list/get`, `backlog_issue_list/get`,
  `backlog_handover_list/get`, `backlog_idea_list/get`, `backlog_note_list/get`,
  `backlog_area_list/get`, `backlog_decision_list/get`) read from `load_dict()` rows: extend
  `Store._load_cached_dict_from_connection` so the compat dict carries a private
  `_rows: {kind: {id: (doc, body)}}` map for the non-task kinds (runtime-only, stripped by
  `_flatten_backlog_dict`), and `backlog_bug_list` stops mutating the dict.
- Tests: per kind, one create/update/archive round trip asserting the committed row **and** the
  exported file; one two-process id-allocation test per numeric kind (extend the stress worker
  op mix with `bug_create`, `issue_create`, `decision_create`, `idea_create`, `note_create`,
  `handover_create`, `area_create` and add them to `REQUIRED_OPS`); one test that
  `linear-queue.json` is imported then absent; one test that `IDEAS.md` regenerates on idea
  create and is never imported.
- Commit per kind group is acceptable; final commit: `feat(server): move every entity writer onto the store`.

### 3.4 Hot-path row API and generalized committed rendering

**Files:** `taskmaster/backlog_server.py`, `tests/test_committed_response_rendering.py`,
`tests/test_store_server_integration.py`.

- `_TxFrame.renderers: list` (stack). `_render_after_commit(renderer)` pushes; `_transactional`
  snapshots the stack depth before calling `fn`, and on a **nested** call (frame already
  active) pops anything pushed by the nested body after it returns and discards it, so only
  the outermost tool's renderer survives (R6). Test: `backlog_complete_task` calling into an
  update path returns complete-task's own string.
- Convert `backlog_update_task`, `backlog_record_gate`, `backlog_skip_gate`,
  `backlog_record_merge`, `backlog_add_task`, `backlog_complete_task`, `backlog_pick_task`,
  `backlog_batch_update`, `backlog_note` to `tx.get`/`tx.put`/`tx.create` on `("task", id)`
  rows for the task they touch (epic/phase lookups may still use the dict). Each renders via
  `_render_after_commit` from `committed[("task", id)]`; `backlog_batch_update` renders each
  line from its own committed doc.
- `_transactional` appends ` [seq N]` (N = `frame.tx.seq`) to every latched tool's string
  result; viewer JSON responses carry `"seq": N`. Test: every mutating public tool's result
  matches `r"\[seq \d+\]$"` and the number exists in `changes`.
- `regenerate_context` runs once per transaction (on latch), not three times;
  `regenerate_progress_dashboard` is either called from the exporter's PROGRESS.md path or
  deleted with its six no-op monkeypatches.
- `_load_task_full_identified`'s projection-overlay branch: add a test that it is unreachable
  on a v4 project, then delete the branch if the test proves it; keep it only if a v3 path
  still reaches it, with the test documenting which.
- Commit: `feat(server): row API for the hot path, committed rendering everywhere, [seq N]`.

### 3.5 `backlog_store_status` and the Linear worker

**Files:** `taskmaster/backlog_server.py`, `taskmaster/integrations/linear/worker.py`,
`tests/test_store_status_tool.py` (new), `tests/test_linear_*.py` as affected.

- New MCP tool `backlog_store_status()` beside `backlog_index_status`, rendering
  `Store.status()` per spec §3.8: root + resolution source, schema version, db/WAL sizes, last
  20 changes, dirty and quarantined files, live sessions, filesystem warning, merge conflicts
  in 24 h, `corrupt-*` files, pending Linear queue count.
- `worker.enqueue` → `tx.linear_enqueue` (inside the caller's transaction);
  `worker.drain` → `Store.linear_pending` + per-item `linear_mark` in short transactions;
  delete `queue_path`, `read_queue`, `_write_queue`.
- Commit: `feat(server): backlog_store_status; Linear queue drains from the store`.

### 3.6 Step 3 verification and gate

```powershell
uv run pytest -q -m "not slow" --basetemp=%TEMP%\tm-pytest
uv run pytest -q tests/test_store_concurrency.py --basetemp=%TEMP%\tm-pytest
uv run pytest -q --basetemp=%TEMP%\tm-pytest
```

Then a fresh-context whole-step review (opus) plus a Codex adversarial review, focused on:
allowlist completeness (grep `taskmaster/` for `write_text|atomic_write|os.replace|rename|unlink`
outside `store.py`), composite-op atomicity, renderer stack correctness, id allocation under the
extended stress mix, and the `IDEAS.md` / `linear-queue.json` one-way files. Fix every
critical/important finding with a red test first. Update the changelog boundary paragraph to
"defects 1–5 fixed for every kind". Merge to `master` with `--no-ff`.

---

## Step 4 — absorb `index.py`; viewer reads; hooks

### 4.1 Absorb the derived index

**Files:** create `taskmaster/paths.py`; delete `taskmaster/index.py`; modify
`taskmaster/store.py`, `taskmaster/backlog_server.py`, `hooks/edit_resurface.py`, `README.md`;
tests `tests/test_index_*.py` → rewritten as `tests/test_store_derived_queries.py`,
`tests/test_backlog_query.py`, `tests/test_backlog_search_fts.py`, `tests/test_edit_resurface_hook.py`.

- Move the five helpers named in R9 to `paths.py`; `store.py` imports from there.
- `backlog_query` runs its guarded SQL against `store.db` (`Store.connection` in a `BEGIN`
  snapshot); docstring documents `entities`, `changes`, `projection`, `sessions`, `linear_queue`,
  `links`, `related`, `entity_paths`, `handover_tasks`, `entity_fts`.
- `_search_via_index` queries `entity_fts` in the store; never builds anything.
- `_load_snapshot` stops calling `build_index`; `INDEX_REFRESH_BUDGET_S`, `_log_index_error`,
  `local/index.log` go away. `backlog_index_status` reports derived-table row counts and last
  rebuild from the store and, with `rebuild=True`, calls `rebuild_derived()` and unlinks a
  legacy `local/index.db` (R9).
- Delete the CLI entry at the bottom of `backlog_server.py` that called `index.build_index`.
- Commit: `refactor(index): derived tables live in the store; index.py removed`.

### 4.2 Viewer reads and the artifact root

**Files:** `taskmaster/backlog_server.py`, `taskmaster/taskmaster_v3.py`,
`tests/test_v4_server.py`, `tests/test_api_notes.py`, `tests/test_epic_detail_endpoint.py`.

- `GET /api/bugs`, `/api/issues`, `/api/ideas`, `/api/notes` serve rows from `_load_snapshot()`
  (the `_rows` map from §3.3) and carry the snapshot ETag; delete `list_issue_ids_cwd`,
  `load_issue`, `_resolve_artifact_root` and its two viewer uses.
- `_viewer_etag()` on a network-filesystem store returns `f"{token}:{max_seq}"` from
  `load_dict_with_identity` rather than `":0"`; viewer `_serve_json`/PATCH surface a
  legacy-layout refusal as 409 with the actionable message (step-2 re-review item).
- Full `LEGAL_STATUS_TRANSITIONS` applied in batch and viewer paths, not only for `archived`.
- Commit: `feat(viewer): every GET serves committed store rows under one ETag`.

### 4.3 Hooks

**Files:** `hooks/edit_resurface.py`, `hooks/merge_gate_decide.py`, `hooks/merge_recorder_stamp.py`,
`tests/test_edit_resurface_hook.py`, `tests/test_merge_gate_decide.py` (create if absent).

- Shared root resolution: `taskmaster.store.resolve_root(cwd)` (already handles
  `TASKMASTER_ROOT` and the git common dir) used by all three hooks.
- `edit_resurface`: open `<root>/.taskmaster/local/store.db` if present; staleness =
  `MAX(changes.seq)` versus the seq stored in `local/hook-seen/`; query `entity_paths` + `related`
  in the store; if the db is absent, print nothing and exit 0 (never import in a hook, R10).
- `merge_gate_decide`: query `entities WHERE kind='task' AND json_extract(doc,'$.branch')=?` for
  the source branch's task and its gate state; fallback to `iter_task_files` only when the db is
  absent, with a log line.
- `merge_recorder_stamp`: keep delegating to `backlog_record_merge`; add the common-dir root
  resolution before importing `backlog_server` so a linked worktree stamps the main store.
- Commit: `feat(hooks): resurface and merge gates read the store, never import`.

### 4.4 Step 4 verification and gate

Same three commands as §3.6; fresh-context review focused on query-guard coverage of the new
tables, hook fail-open behaviour, and viewer ETag consistency. Merge to `master` with `--no-ff`.

---

## Step 5 — scripts, docs, release, CodeMaestro verification

### 5.1 Scripts through the store

**Files:** `scripts/backfill_tldr.py`, `scripts/migrate_links.py`, `scripts/migrate_handover_statuses.py`, `tests/test_scripts_store.py` (new).

- Each script opens the store via `store.open_store(root)` and runs its edits as one
  `store.transaction(tool="scripts/<name>")` using `tx.list`/`tx.put`; `save_v3` and raw
  `write_text` calls are removed. `--dry-run` stays. `migrate_handover_statuses.py` no longer
  tells the user to run `backlog_handover_resync`.
- Commit: `refactor(scripts): maintenance scripts write through the store`.

### 5.2 Docs, skills, playbooks, changelog, version

**Files:** `README.md`, `docs/TASKMASTER.md`, `skills/*/SKILL.md` and `playbooks/*/playbook.md`
that mention `backlog_index_status`, `index.db`, or the Linear retry queue; `CHANGELOG.md`;
`pyproject.toml`; `docs/specs/2026-09-04-sqlite-store-design.md` status line.

- README index section rewritten: one database, `backlog_store_status`, `[seq N]` meaning per
  decision 7, legacy-layout refusal, per-repository backlog (decision 2).
- Skill/playbook text: add a short "Verifying writes" paragraph to `taskmaster`, `pick-task`,
  `review-gate`, `end-session`, `handover`, `bug`, `issue`, `linear` skills: a result ending in
  `[seq N]` is committed; `backlog_store_status` shows dirty/quarantined files and busy holders.
  Skill lint tests (`tests/test_*_skill_lint.py`) must stay green — check their budgets.
- CHANGELOG: replace the "Unreleased — SQLite store, steps 1 and 2" entry with a `6.0.0` entry
  covering steps 1–5 (breaking list: legacy layout refusal, `context` removed from backlog.yaml,
  `index.db` gone, `linear-queue.json` gone, `IDEAS.md` derived, backlog per repository).
- `pyproject.toml` version `6.0.0` (R12). Spec status line → "implemented 6.0.0".
- Commit: `docs: 6.0.0 — SQLite store complete`.

### 5.3 CodeMaestro verification (copy, R11)

- Copy `C:\Users\gruku\Files\Work\CodeMaestro\.taskmaster` into a scratchpad directory with a
  `.git` initialized (`git init` so root resolution works), set `TASKMASTER_ROOT` to it, and in a
  subprocess: `backlog_store_status()`, `backlog_status()`, `backlog_list_tasks(limit=5)`,
  one `backlog_update_task` on a real task id, `backlog_store_status()` again. Record: import
  time, task/epic/bug/handover row counts versus file counts, quarantined files (list them),
  the `[seq N]` of the update, and that the update's file changed and nothing else did
  (`git status --short` on the copy shows exactly the touched files plus the dropped `context`
  block). Write the numbers into the handoff and the plan's execution status.
- Do **not** open the live CodeMaestro checkout.

### 5.4 Step 5 gate and handoff

Full suite green; fresh-context review of the docs for claims not backed by code; merge to
`master` with `--no-ff`; write `docs/handoffs/2026-09-05-sqlite-store-complete.md` with the
merge SHAs, the CodeMaestro copy numbers, and the remaining deferred items (if any). Do not push.

## Execution status — 2026-09-06 (step 3, plus 4.3 and 5.1)

- Step 3 is implemented on `feature/sqlite-store-step-3`: every entity kind (bug, issue,
  handover, decision, idea, note, area, tracker) plus `project.yaml`, `linear.yaml`, the Linear
  queue and `PROGRESS.md` write through the store; the test guard covers every directory and
  file under `.taskmaster/`; the nine hot-path tools use the row API; every mutating tool
  renders from committed state and ends in `[seq N]`; `backlog_store_status` exists and is
  genuinely read-only; the read-scan throttle poke and `_sync_projection` are gone.
- Reviews: five per-task reviews with fix rounds, a whole-branch review (Claude opus) and a
  Codex adversarial review; their 13 Important findings were fixed in one wave and re-reviewed
  clean. Rulings R1–R12 stand; additional rulings are in the SDD ledger and the step handoff.
- Also merged into this branch ahead of their steps because they touch disjoint files:
  task 4.3 (hooks read the store via the shared root rule in `taskmaster/root.py`) and
  task 5.1 (maintenance scripts write through the store).
- Full suite on the merged branch: 2,152 passed, 1 skipped, 5 failed, all five the
  handover-migration script unpacking pairs where the fix wave widened planner rows to
  triples; fixed in `82a3a06` (25 covering tests green). The 8×200 stress test passed.
- Known gaps carried to step 4/5: a hand edit to an existing `IDEA-*.md` refreshes
  `ideas/IDEAS.md` only on the next idea write (regenerating on every import starved the
  writer lock under 8 processes); the bypass guard intercepts `Path.open` but not builtin
  `open`; an explicit Linear retry can requeue a row a drain has claimed.
