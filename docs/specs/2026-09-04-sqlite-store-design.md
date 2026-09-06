<!-- User intent: let many agents and sessions write to one Taskmaster backlog at the same time
     without losing each other's writes, blocking each other, or hitting Windows rename errors.
     SQLite becomes the runtime authority; the markdown/yaml files stay the git-facing projection. -->

# SQLite Store: Concurrent Multi-Agent Writes — Design

**Date:** 2026-09-04 (rev 2, after adversarial spec review: Claude opus + Codex, 36 findings)
**Status:** implemented 6.0.0
**Supersedes:** the write half of `taskmaster_v3.py` (save_v3/save_v4, snapshot diffing,
atomic_write callers, viewer `create_task`, `with_file_lock`)
**Builds on:** `2026-09-03-derived-index-ambient-resurfacing-design.md` (index.db is absorbed)
**Evidence:** 35 field reports in `claude-tools/inbox/` (2026-07-04 to 2026-09-01), all Windows,
34 from CodeMaestro (2.3k tasks, v3 layout, up to 12 concurrent sessions)

## 1. Problem

Every write tool follows `data = _load(); mutate(data); _save(data)`. That shape has five
independent defects, and the inbox reports show every combination of them:

| # | Defect | Where | Observed symptom |
|---|---|---|---|
| 1 | Full-tree rewrite on any mutation (v3: every task file, every epic file, backlog.yaml; v4: backlog.yaml every time because `context` is regenerated into it) | `save_v3`, `save_v4`, `regenerate_context` (`backlog_server.py:681, 904`) | Updating task X fails with `[Errno 13]` / `WinError 32` on `tasks/unrelated.md.tmp`; 3–5 retries per write |
| 2 | No cross-process lock; one `threading.Lock` per MCP server, one server per Claude session | `_backlog_lock` (`backlog_server.py:321`) | Peer session's stale snapshot lands after ours: status, `human_action`, `branch`, `record_merge` revert after "Updated" |
| 3 | Module-global load snapshot shared by thread-pool tool calls (FastMCP runs sync tools in `anyio.to_thread`) | `_LOAD_SNAPSHOT` (`backlog_server.py:327`) | Same-session parallel calls and even sequential calls compose a write from a snapshot taken before the previous write landed; reproduced with no second session (4 reports, mtime-verified) |
| 4 | `os.replace` over a file a peer has open fails on Windows; callers retry and report success from the *requested* value, not the persisted one | `atomic_write` (`taskmaster_v3.py:50`), every tool's return string | "Success is indistinguishable from silent loss at the call site" |
| 5 | Next task id derived from the in-memory index; partial writes leave an `.md` without an index row | `backlog_add_task` (`backlog_server.py:4448`) | Two consecutive adds return the same id; the second overwrites the first's file, destroying notes, SHAs, review findings (10 reports) |

Measured on CodeMaestro: full load 1.5 s, YAML dump 1.1 s. A cross-process lock around the
*current* save path would therefore hold for 2–3 s per write and serialize a dozen agents at
that rate. That latency, not correctness, is why the simpler "files stay authoritative, add a
lock" design was rejected (see §8).

Non-goals surfaced by the same reports, filed separately: `backlog_add_epic` deleting a
hand-written epic body on canonicalization; `backlog_record_gate` accepting a pass that
`backlog_complete_task` rejects.

## 2. Decisions

1. **SQLite is the runtime authority.** Every tool, hook, and viewer endpoint reads and writes
   `<root>/.taskmaster/local/store.db`. Nothing reads backlog.yaml or `tasks/*.md` to answer a
   query once the store is warm.
2. **One store per repository, at the main checkout.** `<root>` is resolved once per process:
   `TASKMASTER_ROOT` if set; else the parent of `git rev-parse --git-common-dir` for the cwd's
   nearest git repository (so a linked worktree under `.worktrees/` uses the main checkout's
   `.taskmaster/`); else cwd. The tracked `.taskmaster/` copy inside a linked worktree is never
   written. Consequence, stated plainly: **the backlog is per repository, not per branch.**
3. **Files are the git-facing projection.** `backlog.yaml`, `tasks/*.md`, `epics/*.md`,
   `phases/*.md`, `bugs/`, `issues/`, `handovers/`, `decisions/`, `ideas/`, `notes/`, `areas/`,
   `integrations/` keep today's v4 layout and stay committed. They are exported inside the
   committing transaction and imported when they change under the store.
4. **Absence of a file never deletes data.** Rows are removed only by a tool operation that
   leaves a tombstone. A row whose file is missing is re-exported.
5. **One database.** The 5.2.0 derived index is absorbed: entities, changes, FTS, paths, links,
   handover tables share one file and one schema version.
6. **Writers serialize at row-transaction granularity, readers never block.** WAL mode,
   `BEGIN IMMEDIATE`, busy timeout 30 s. Export of the touched files happens inside the
   transaction, so a write holds the lock for the DB update plus a few small file writes.
7. **Success is proven, not assumed.** A tool's result is rendered from the committed row.
   Any failure to commit is a raised error, never a success string. `[seq N]` in a result
   proves that commit happened and was fsynced; it does not promise the value is still current.
8. **Network filesystems refuse writes; cloud-synced local folders warn.** WAL requires
   host-local shared memory, so UNC paths, `DRIVE_REMOTE`, NFS/SMB mounts fail every write tool
   with an explanation. OneDrive/Dropbox/iCloud/Google Drive folders (path substring, reparse
   point, or `CloudStorage`/`Mobile Documents` segment) get one warning per process and continue.
9. **Legacy v2/v3 backlogs are imported once and projected back as v4.** Opening a v3 backlog
   performs the v4 migration. `backlog_migrate_v4` stays as the explicit entry point.
10. **`context` is derived on read and never persisted.** It leaves backlog.yaml. This is what
    makes backlog.yaml stop being rewritten on every task change.

## 3. Component: `taskmaster/store.py`

The only module that opens the database or writes a projection file. `backlog_server.py`
calls it; `taskmaster_v3.py` keeps the pure functions (frontmatter parse/render, tldr, path
helpers, three-way merge, `next_task_id`-style directory scans) and loses every writer.

### 3.1 Location, lifecycle, platform

- Path: `<root>/.taskmaster/local/store.db` (+ `-wal`, `-shm`). `_ensure_local_dir` already
  drops a `*` gitignore there; `backlog_init` and `backlog_migrate_v4` enforce it too.
- **Connections.** One per thread, kept in a `threading.local` registry with a `weakref`
  finalizer that closes it on thread retirement. `isolation_level=None` (explicit `BEGIN
  IMMEDIATE` / `COMMIT`; no implicit DEFERRED transactions), `journal_mode=WAL`,
  `synchronous=FULL` on write connections (the `[seq N]` promise needs the fsync; ~1–3 ms on
  SSD), `busy_timeout=30000`, `foreign_keys=ON`. Every transaction is `try/finally` with
  rollback, so an abandoned transaction on a pooled thread cannot leak. No `mode=ro` URIs: a
  read-only open cannot create `-shm` and fails when a `-wal` exists; read paths use a normal
  connection and a query guard (`query_guard.py` already exists).
- **Checkpoints.** `PRAGMA wal_checkpoint(PASSIVE)` after any commit that exported files;
  `TRUNCATE` attempted at session end, best-effort, non-blocking, never delaying a tool result.
  Read connections hold no cursor across tool calls, so checkpoints are not starved.
- **Schema version** in `meta`, plus `creation_token` (uuid written at create/rebuild).
  Version mismatch: migrate in place if a migration exists, else rebuild.
- **Corruption** is only `sqlite3.DatabaseError` whose message contains `malformed`,
  `not a database`, or `file is encrypted`, or a failed `PRAGMA quick_check`. `database is
  locked`, `unable to open database file`, and other `OperationalError`s are transient and go
  through the busy path. Recovery renames the three files to `store.db.corrupt-<ts>*` (kept
  7 days, listed by `backlog_store_status`), then rebuilds by importing the projection. Rows
  that were committed but still dirty are the only possible loss; §3.6 keeps that set empty in
  normal operation and the renamed file preserves it for manual recovery.
- Windows, macOS, Linux: SQLite's own locking (`LockFileEx`, POSIX `fcntl`). The database is
  never renamed, so the sharing-violation failure mode of `os.replace` does not apply to it.
- **Forward guard.** The exporter writes `meta.projection_schema: 5` into backlog.yaml. A store
  refuses to open a projection whose `projection_schema` is newer than its own instead of
  importing and re-exporting it in an older shape. 5.2.x ignores unknown meta keys.

### 3.2 Schema

```sql
meta(key TEXT PRIMARY KEY, value TEXT)  -- schema_version, creation_token, project,
                                        -- last_scan_generation, last_progress_seq
entities(
  kind TEXT NOT NULL,          -- task | epic | phase | backlog | project | bug | issue | handover
                               -- | decision | idea | note | area | tracker
  id TEXT NOT NULL,
  epic TEXT, status TEXT,      -- denormalized for hot filters (tasks)
  archived INTEGER DEFAULT 0,
  deleted INTEGER DEFAULT 0,   -- tombstone: row kept forever so MAX(id) never recycles
  doc TEXT NOT NULL,           -- JSON, persistable form (post _v4_strip_private_fields);
                               -- load_dict() re-adds runtime keys (_orphan_tasks, context)
  body TEXT,                   -- markdown body for file-backed kinds
  rev INTEGER NOT NULL, updated_seq INTEGER NOT NULL,
  PRIMARY KEY(kind, id)
)
changes(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, session TEXT NOT NULL, tool TEXT NOT NULL,
  kind TEXT NOT NULL, id TEXT NOT NULL,
  op TEXT NOT NULL,            -- create | update | archive | delete | import | merge | export-fail
  fields TEXT, before TEXT, after TEXT
)
projection(
  file TEXT PRIMARY KEY,       -- relative to .taskmaster/
  kind TEXT NOT NULL,          -- 'backlog' for backlog.yaml (multi-entity), else the entity kind
  id TEXT,                     -- NULL for multi-entity files
  content_hash TEXT NOT NULL,  -- sha1 of the bytes we wrote or last imported
  mtime REAL, size INTEGER,    -- taken by fstat on the tmp file BEFORE os.replace
  dirty INTEGER DEFAULT 0, quarantined INTEGER DEFAULT 0,
  exported_seq INTEGER
)
projection_base(file TEXT PRIMARY KEY, content BLOB)  -- last exported bytes, kept while dirty=1
sessions(session TEXT PRIMARY KEY, pid INTEGER, host TEXT, started TEXT, last_seen TEXT,
         cwd TEXT, current_tool TEXT)
linear_queue(seq INTEGER PRIMARY KEY, op TEXT, target_id TEXT, tracker_id TEXT, payload TEXT,
             state TEXT, attempts INTEGER, last_error TEXT)
-- derived, rebuildable from entities: keyed by (kind, id), never by bare id
entity_paths(kind, id, path, match_kind, source)
links(src_kind, src_id, type, dst_kind, dst_id, derived)
related(a_kind, a_id, b_kind, b_id, via, weight)
handover_tasks(handover_id, task_id)
entity_fts  -- FTS5, self-contained (as today), columns kind, id, title, body; refreshed in-tx
```

backlog.yaml maps to the `backlog` row (meta minus `updated`/`context`), one `epic` row per
epic, one `phase` row per phase. `project.yaml` is the `project` row.

### 3.3 Transaction API

```python
with store.transaction(tool="backlog_update_task") as tx:
    task = tx.get("task", task_id)          # fresh row, inside BEGIN IMMEDIATE
    task["status"] = "in-progress"
    tx.put("task", task_id, task)           # per-field diff → rev+1, changes row, file dirty
result = tx.committed["task", task_id]      # the row as committed; render from this
```

Order inside `transaction()`:

1. `BEGIN IMMEDIATE` (busy path in §3.9).
2. Import scan (§3.7) — always, under the lock, hash-verified. External edits land before we
   read.
3. Drain any `dirty=1` files left by an earlier failed export (§3.6).
4. Body of the tool: `tx.get/put/create/archive/delete`, all against rows read in this
   transaction. Composite operations (bug promote, auto-link, Linear link, batch update) run in
   one transaction; `_auto_link_on_save` moves inside it.
5. Derived refresh for touched rows (FTS delete+insert, paths, links, related) — in-tx, so an
   error rolls everything back and nothing drifts.
6. Export touched files (§3.6), PROGRESS.md if due.
7. `COMMIT`, then `PASSIVE` checkpoint.

- `tx.put` diffs against the row read in the same transaction; `changes.fields/before/after`
  are exact. Unchanged docs are no-ops.
- `tx.create(kind, doc)` allocates ids (§3.5) and relies on the primary key as final guard.
- `tx.archive` sets `archived=1` and queues the file move; `tx.delete` sets `deleted=1`
  (tombstone), queues file removal. Neither is ever inferred from the filesystem.
- On exception: rollback, no file was replaced (exports happen after the body and before
  commit; a file replaced before a later step fails is re-exported from the committed state on
  the next transaction because its `projection` row was not updated), error to the client.

**Dict-shaped compatibility mode.** Sixty-one call sites use the whole-backlog dict.
`store.transaction_dict(tool)` yields today's dict shape, built **inside** `BEGIN IMMEDIATE`
from the per-process cache refreshed under the lock (cheap: only rows with `updated_seq` above
the cached seq are re-deserialized). On exit, for every entity present in both the private
snapshot and the mutated dict, the diff is computed **per top-level field** and only changed or
removed keys are applied to the row (which equals the snapshot, since both were read under the
same lock; the per-field rule is defence in depth and makes the write log exact). Entities
present in the dict but not the snapshot are creates. **Entities missing from the dict are
ignored** — removal is never implicit; the tools that remove tasks/epics from the dict
(`backlog_archive_task`, `backlog_archive_epic`, and any found by the plan's audit) call
`tx.archive`/`tx.delete` explicitly. Inside a dict transaction, `_load()` returns the
transaction dict **by identity** (thread-local), so nested loads in `backlog_complete_task`,
`_load_related_for_task`, `_expand_fm_links`, and `_enqueue_linear_push_if_synced` mutate and
read the same object. After commit, `_load()` outside a transaction returns the committed state.

### 3.4 Reads

- `store.load_dict()` replaces `_load()` for read tools and the viewer API. Per-process cache
  keyed by `(db path, creation_token, MAX(changes.seq))`; refresh re-deserializes only rows
  with `updated_seq > cached_seq`. The `context` block is computed by `regenerate_context` on
  the cached dict, in memory.
- Reads run the import scan too, but with a 2 s stat cache and no lock; a read may therefore
  lag a hand edit by up to 2 s. Writes never use the cached scan.
- Row-level reads (`backlog_get_task`, `backlog_task_pipeline`, `backlog_dependencies`) move
  to direct queries as a follow-up; not required for correctness.

### 3.5 ID allocation

All inside the creating transaction:

- **Numeric kinds** (task `<epic>-NNN`, bug `B-NNN`, issue `ISS-NNN`, decision `DEC-NNN`,
  idea, note): `MAX(numeric suffix)` over all rows of that kind and prefix **including archived
  and tombstoned rows**, plus the existing directory scan (`next_task_id`,
  `taskmaster_v3.py:1075`, and the `next_bug_id` family) over live and archive directories, so
  an orphan file from an older partial write is never reused. Take the larger, add one.
- **String kinds** (handover `<date>-<slug>`, area `<kebab>`, tracker): caller-derived id;
  on primary-key conflict handovers append `-2`, `-3`; areas and trackers fail with a clear
  error.
- `options.task_id` overrides are checked against live, archived, and tombstoned rows and the
  directory scan, not `_find_task` (which sees live epics only, `backlog_server.py:4427`).
- Cross-kind collisions are impossible because the key is `(kind, id)`; derived tables carry
  `kind`.

### 3.6 Export (DB → files), inside the transaction

- For every touched entity, render its file with today's renderers (`task_v4_to_file`,
  `render_frontmatter`, epic/phase/backlog writers). backlog.yaml is rendered whole from the
  locked state whenever an `epic`, `phase`, or `backlog` row changed; because `context` is gone
  and task lists are not in it (v4), that is rare and small.
- Write `<file>.tmp.<session>`, `fstat` it (mtime, size), hash the bytes, `os.replace` with
  retry on `PermissionError` / `OSError` errno 5, 13, 32 for up to 2 s (jittered 20–200 ms).
  Record hash/mtime/size from the tmp file, never from a post-replace stat, so a hand edit
  landing right after the replace is detected by the next scan as a mismatch.
- Multi-entity files are serialized by the DB lock itself: two sessions cannot both be inside
  a transaction, so "A's stale render of backlog.yaml overwrites B's" cannot occur.
- Export failure after retries: the transaction still commits (the DB is the truth), the file's
  `projection.dirty=1`, a `changes` row `op=export-fail` is written, and the tool result gets
  the suffix `(export pending: tasks/x.md — retried on next call)`. Step 3 of the next
  transaction, in any session, drains it — after the import scan, so a hand edit made in
  between is merged (§3.7) rather than overwritten. "File gone while dirty" is simply an
  export.
- `local/PROGRESS.md` (v4) is regenerated inside the transaction at most once per 5 s per
  store (`meta.last_progress_seq`), best-effort; a failure logs and never fails the tool. Its
  read-modify-write of the changelog section is now under the lock.
- Archive/delete file moves happen here through the same retry path; a move is recorded as
  two `projection` updates in one transaction.

### 3.7 Import (files → DB)

- **Scan.** Walk the projection directories; compare `(mtime, size)` to `projection`. Any
  mismatch, unknown file, or missing file is a candidate. **Hash every candidate** before
  acting; a hash equal to the stored one only refreshes the stat. If `.git/HEAD`, `.git/index`,
  or `.git/ORIG_HEAD` (from `git rev-parse --git-common-dir`, or the worktree's own `.git`
  file target) changed since `meta.last_scan_generation`, hash **every** file, because git
  rewrites mtimes on content-identical files and can produce same-size edits inside NTFS
  mtime granularity. backlog.yaml and project.yaml are always hashed. Ignore `*.tmp.*`,
  `*.lock`, `*.corrupt-*`, and dotfiles.
- **Unparseable file** (bad frontmatter, YAML error, git conflict markers `<<<<<<<`): never
  import, never export over it. Mark `projection.quarantined=1`, log to `store.log`, surface
  in `backlog_store_status` and as a one-line warning in the tool result. The entity keeps its
  DB state; export resumes when the file parses again or is removed.
- **Changed file, row not dirty:** parse, replace `doc`/`body` (per-field diff recorded),
  `changes` row `op=import`, `session=<external>`.
- **Changed file, row dirty:** three-way merge, base = `projection_base`, ours = DB doc,
  theirs = file. Fields via `_three_way_merge_fields`; body: the side that changed, ours if
  both, and the foreign body is preserved in `changes.before` so nothing is unrecoverable.
  Result written and exported; `changes` row `op=merge`.
- **Missing file, row present:** re-export (decision 4). If the same id appears under
  `tasks/archive/` (or the kind's archive dir) the move is recognised by id and applied as
  `archived=1` with an `op=import` row, not as delete-plus-create.
- **Unknown file, no row:** create with `op=import`. If its id collides with a tombstone, the
  file wins and the tombstone is cleared (the user resurrected it deliberately via git).
- **Whole-scan atomicity.** A scan runs in one transaction and applies all its results
  together, so a checkout in progress yields at worst one hybrid scan that the next scan
  corrects; with decision 4, nothing is destroyed in between.
- **No DB** (fresh clone, rebuild, v2/v3 backlog): full import. v2/v3 task lists inside
  backlog.yaml become task rows; the first export writes `tasks/*.md` and drops them from
  backlog.yaml. That is the migration. The rebuild inventory covers every kind in §3.2,
  including `notes/`, `areas/`, `integrations/trackers`, `issues/archive/`, and
  `handovers/_archive/<year>/` (the 5.2.0 glob `handovers/archive/*.md` is wrong).

### 3.8 Observability

- `backlog_store_status()`: root and how it was resolved, schema version, db/WAL size,
  last 20 `changes` rows, dirty and quarantined files, live sessions (heartbeat within 60 s),
  filesystem warning, merge conflicts in the last 24 h, `corrupt-*` files present.
- `backlog_index_status(rebuild=True)` rebuilds only derived tables (FTS, paths, links,
  related); it never touches `entities`, `changes`, or `projection`. Docstring rewritten.
- Write results end with `[seq N]`. Meaning per decision 7.
- `backlog_query` schema docs gain `changes`, `projection`, `sessions`, `linear_queue`.
- `local/store.log`: export retries, imports, merges, quarantines, rebuilds. Rotated at 1 MB.

### 3.9 Busy path

`BEGIN IMMEDIATE` failing after 30 s raises a tool error built on a **separate** connection
with a 2 s timeout: live `sessions` rows (heartbeat within 60 s, with `current_tool`) labelled
*probable holders*, plus the last committed writer. If even that read is blocked: `store busy
for 30s; could not read session table; retry`. Nothing is written.

### 3.10 Error handling summary

| Condition | Behaviour |
|---|---|
| Busy > 30 s | Tool error naming probable holders; nothing written |
| Export rename fails after 2 s | Commit stands; `dirty=1`; suffix in result; drained next transaction after import scan |
| DB corrupt (narrow definition) | Rename aside, rebuild from files, warn in result and status |
| Transient `OperationalError` | Busy path / retry; never rebuild |
| File changed under a dirty row | Three-way merge, `op=merge`, foreign body kept in `changes` |
| File unparseable | Quarantine; DB state kept; export suppressed for that file |
| File missing, row present | Re-export |
| Duplicate id on create | Integrity error → tool error; allocator makes it unreachable |
| Network filesystem | Write tools refuse with explanation; reads work |
| Cloud-synced local folder | One warning per process; continue |
| Projection newer than store | Refuse to open; instruct to upgrade the plugin |

## 4. Migration of callers

1. `_load()` → `store.load_dict()` (identity rule inside transactions); `_save()` removed;
   `_mutate_and_save(data)` valid only inside `store.transaction_dict()`, raises otherwise.
   Wrap all 61 sites. Audit and convert implicit entity removals to `tx.archive/delete`.
   Delete `_LOAD_SNAPSHOT`, `_backlog_lock`, `with_file_lock`, viewer `create_task`.
2. Row API for the hot path: `backlog_update_task`, `backlog_record_gate`,
   `backlog_skip_gate`, `backlog_record_merge`, `backlog_add_task`, `backlog_complete_task`,
   `backlog_pick_task`, `backlog_batch_update`, `backlog_note`.
3. File-backed entity writers (`write_handover`, `write_bug`, `write_issue`,
   `write_decision`, `write_idea`, `write_note`, `write_area`, `write_tracker`) become
   `tx.create/put`; their renderers move to the exporter. Linear queue moves to the
   `linear_queue` table; the worker drains it in short transactions.
4. Viewer: `GET /api/backlog` serves `load_dict()`; ETag = `creation_token:max_seq`; every
   `POST`/`PUT`/archive handler runs a store transaction; `_resolve_artifact_root` is replaced
   by the store root.
5. Hooks: `edit_resurface.py` opens `store.db`, resolves root via the common dir like the
   server, uses `MAX(changes.seq)` for staleness; `merge_gate` / `merge_recorder_stamp` query
   the store instead of nested tasks in backlog.yaml (already broken on v4) and never trigger a
   full import in a hook (if the DB is missing, they fall back to reading files and log).
6. `backlog_canonicalize_layout` runs as one transaction that moves files and rewrites every
   `projection.file` path. `scripts/backfill_tldr.py`, `scripts/migrate_links.py`,
   `scripts/migrate_handover_statuses.py` are documented as external editors (run with no live
   session; the next scan imports their output) or rewritten through the store in step 5.
7. `index.py` becomes the derived-table refresh and full-rebuild routine.
8. Skills/playbooks: remove "retry with read-back verification" guidance; document `[seq N]`
   and `backlog_store_status`.

## 5. Testing

- **Bypass detection.** A conftest autouse fixture patches `atomic_write`, `write_task_file`,
  and `Path.write_text` under `.taskmaster/` to raise unless called from `store.py`. Any
  remaining direct writer fails the suite.
- **Multiprocess stress** (`tests/test_store_concurrency.py`): 8 processes × 200 mixed ops
  (add, update status/human_action, record_gate, record_merge, note, archive, bug_create)
  through the real tool functions on one temp v4 backlog. Assert: every `[seq N]` exists in
  `changes`; every asserted field present at the end; ids unique across live, archived,
  tombstoned; **bidirectional** projection check (every row has its file with matching hash,
  every file has its row); no `.tmp.*` left; no `dirty` rows.
- **Sequential same-task** in one process and in one thread pool.
- **Per-field compat write-back**: A enters `transaction_dict`; B (other process) commits
  `human_action`; A sets `status`; both present after. Also: B creates a task while A is in a
  dict transaction; the task survives A's exit.
- **Open-handle contention** (Windows-only): helper holds `tasks/x.md` open during a write;
  commit succeeds, export retries, file matches within 2 s.
- **Import/merge**: hand edit while dirty; git-style whole-file replace with unchanged mtime
  and size (hash path); file deletion (re-export, not delete); archive move by id; conflict
  markers (quarantine); `.tmp.*` ignored; checkout simulated mid-scan.
- **Round trip**: import an untouched 2.3k-task v4 backlog, export, zero-byte diff except the
  removed `context` block and the added `projection_schema` key. v3 backlog import produces v4
  layout and identical `load_dict()` output.
- **Corruption**: truncate the db → rename-aside, rebuild, identical state; a transient
  `database is locked` never triggers rebuild; a dirty row before corruption is present in the
  `corrupt-*` file.
- **Busy timeout**: helper holds `BEGIN IMMEDIATE` 35 s; error names its session.
- **Root resolution**: server started inside `.worktrees/x` uses the main checkout's store.
- **Value content**: arrows, parentheses, YAML-significant characters round trip byte-identical.
- **Existing 182 tests** pass unchanged: the `tmp_taskmaster` fixture writes files, the first
  store open imports them; cache keyed by `creation_token` keeps tests hermetic.

## 6. Sequencing (each step mergeable)

1. `store.py`: root resolution, schema, open/rebuild, transaction and dict-transaction APIs,
   import scan with hashing and quarantine, export, id allocation, filesystem checks, busy
   diagnostic. Unit tests for each. No tool wired.
2. Wire `_load`/`_mutate_and_save`; delete globals and the viewer's file writers; audit
   implicit removals; add bypass detection, stress, sequential, compat, root tests. This step
   fixes defects 1–5 for tasks, epics, phases; bug/issue/handover ids remain scan-then-write
   until step 3, which is stated in the changelog.
3. Row API for hot tools; entity writers and Linear queue through the store;
   `backlog_store_status`; `[seq N]`.
4. Absorb `index.py`; viewer endpoints and ETag; hooks.
5. Docs, skill text, scripts, changelog, release; migrate CodeMaestro by opening it and
   confirming `backlog_store_status`.

Adversarial review (fresh-context reviewer plus Codex) after steps 1 and 2, focused on
import/export merge, root resolution, and the busy path.

## 7. Out of scope

- Simultaneous writers on two machines sharing one folder (network FS refused; cloud sync warned).
- Event-sourced per-writer logs and CRDT merge semantics (unnecessary once writes serialize
  at millisecond granularity).
- Per-branch backlogs (decision 2 makes the backlog per repository).
- The two unrelated bugs in §1 and the five non-write inbox reports.

## 8. Alternatives considered

- **Files authoritative + cross-process lock + per-entity writes + read-back verify.** Fixes
  defects 2–4 and most of 5 with far less code and no sync surface. Rejected on measured
  latency: the lock spans the 1.5 s load and 1.1 s dump, so a dozen sessions serialize at
  2–3 s per write, and every read still re-parses 2.4 MB.
- **SQLite as mutex and id sequence only, files authoritative.** No two-way sync, same
  latency ceiling. This is the **contingency**: if the step-1 review finds the import/export
  surface unmanageable, ship this first and keep the schema for later.
- **Event-sourced per-writer op logs.** Zero coordination on the hot path, but every tool must
  emit semantic ops and every field needs merge semantics; two to three times the effort for
  no gain once writes serialize in milliseconds.
