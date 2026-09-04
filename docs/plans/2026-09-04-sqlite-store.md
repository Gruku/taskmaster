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
