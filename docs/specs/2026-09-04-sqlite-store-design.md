<!-- User intent: let many agents and sessions write to one Taskmaster backlog at the same time
     without losing each other's writes, blocking each other, or hitting Windows rename errors.
     SQLite becomes the runtime authority; the markdown/yaml files stay the git-facing projection. -->

# SQLite Store: Concurrent Multi-Agent Writes — Design

**Date:** 2026-09-04
**Status:** approved design, not yet planned
**Supersedes:** the write half of `taskmaster_v3.py` (save_v3/save_v4, snapshot diffing, atomic_write)
**Builds on:** `2026-09-03-derived-index-ambient-resurfacing-design.md` (index.db is absorbed)
**Evidence:** 35 field reports in `claude-tools/inbox/` (2026-07-04 to 2026-09-01), all Windows,
34 from CodeMaestro (2.3k tasks, v3 layout, up to 12 concurrent sessions)

## 1. Problem

Every write tool follows `data = _load(); mutate(data); _save(data)`. That shape has five
independent defects, and the inbox reports show every combination of them:

| # | Defect | Where | Observed symptom |
|---|---|---|---|
| 1 | Full-tree rewrite on any mutation (v3: every task file, every epic file, backlog.yaml) | `save_v3`, `save_v4` epic/phase pass | Updating task X fails with `[Errno 13]` / `WinError 32` on `tasks/unrelated.md.tmp`; 3–5 retries per write |
| 2 | No cross-process lock; one `threading.Lock` per MCP server, one server per Claude session | `_backlog_lock` | Peer session's stale snapshot lands after ours: status, `human_action`, `branch`, `record_merge` revert after "Updated" |
| 3 | Module-global load snapshot shared by thread-pool tool calls (FastMCP runs sync tools in `anyio.to_thread`) | `_LOAD_SNAPSHOT` | Same-session parallel calls and even sequential calls compose a write from a snapshot taken before the previous write landed; reproduced with no second session (4 reports, mtime-verified) |
| 4 | `os.replace` over a file a peer has open for reading fails on Windows; callers retry and then report success from the *requested* value, not the persisted one | `atomic_write`, every tool's return string | "Success is indistinguishable from silent loss at the call site" |
| 5 | Next task id derived from the in-memory index only; partial writes leave an `.md` without an index row | `backlog_add_task` | Two consecutive adds return the same id; the second overwrites the first's file, destroying notes, SHAs, review findings (10 reports) |

Measured on CodeMaestro: full load 1.5 s, YAML dump 1.1 s, so a global lock around the current
save path would hold for 2–3 s per write and serialize every agent behind it.

Non-goals surfaced by the same reports, filed separately: `backlog_add_epic` deleting a
hand-written epic body on canonicalization; `backlog_record_gate` accepting a pass that
`backlog_complete_task` rejects.

## 2. Decisions

1. **SQLite is the runtime authority.** Every tool reads and writes `.taskmaster/local/store.db`.
   No tool reads backlog.yaml or `tasks/*.md` to answer a query once the store is warm.
2. **Files are the git-facing projection.** `backlog.yaml`, `tasks/*.md`, `epics/*.md`,
   `phases/*.md`, `bugs/`, `issues/`, `handovers/`, `decisions/`, `ideas/`, `notes/`, `areas/`
   keep today's v4 layout and stay committed. They are exported after each commit and
   imported when they change under the store (git pull, branch switch, hand edit, fresh clone).
3. **One database.** The 5.2.0 derived index (`index.db`) is absorbed: entities, changes, FTS,
   paths, links, handover tables share one file and one schema version.
4. **Writers serialize at the row-transaction level, readers never block.** WAL mode,
   `BEGIN IMMEDIATE`, busy timeout 30 s. A write transaction touches a handful of rows and
   holds the lock for milliseconds.
5. **Success is proven, not assumed.** A tool's result is rendered from the committed row after
   the transaction. Any failure to commit is a raised error, never a success string.
6. **Cloud-synced and network folders get a warning, not a refusal.** Detected once per process
   and surfaced in `backlog_status` and `backlog_store_status`.
7. **Legacy v2/v3 backlogs are imported once and projected back as v4.** Opening a v3 backlog
   with the new store performs the v4 migration. `backlog_migrate_v4` remains as an explicit
   entry point that also rewrites the gitignore and removes the v3 task list from backlog.yaml.

## 3. Component: `taskmaster/store.py`

The only module that opens the database. `backlog_server.py` calls it; `taskmaster_v3.py`
keeps the pure functions (frontmatter parse/render, tldr, path helpers, three-way merge) and
loses `save_v3`, `save_v4`, `_v4_write_task`, `atomic_write` callers.

### 3.1 Location, lifecycle, platform

- Path: `.taskmaster/local/store.db` (+ `-wal`, `-shm`). `.taskmaster/local/` is gitignored;
  `backlog_init`, `backlog_migrate_v4`, and first store open all enforce the ignore line.
- Connection per thread, `PRAGMA journal_mode=WAL`, `synchronous=NORMAL`,
  `busy_timeout=30000`, `foreign_keys=ON`. WAL checkpoint (`PASSIVE`) after every commit that
  exported files, `TRUNCATE` at session end.
- Schema version in `meta`. Mismatch or any `sqlite3.DatabaseError` on open: delete the three
  files and rebuild by importing the projection. Rebuild loses only exports that were still
  pending, which the export policy in 3.6 keeps at zero in normal operation.
- Windows, macOS, Linux: SQLite's own locking (Win32 `LockFileEx`, POSIX `fcntl`) is used
  unchanged. The failure mode that plagued `os.replace` (sharing violation on rename over an
  open file) does not exist for the database because it is never renamed.
- Sync-folder detection: path contains `OneDrive`, `Dropbox`, `Google Drive`, `iCloud`,
  `CloudStorage`, `Mobile Documents`, or is a UNC path / network mount. One warning line per
  process.

### 3.2 Schema

```sql
meta(key TEXT PRIMARY KEY, value TEXT)                 -- schema_version, project, created
entities(
  kind TEXT NOT NULL,          -- task | epic | phase | bug | issue | handover | decision | idea | note | area | tracker
  id TEXT NOT NULL,
  epic TEXT,                   -- tasks only; indexed
  status TEXT,                 -- denormalized for hot filters
  archived INTEGER DEFAULT 0,
  doc TEXT NOT NULL,           -- JSON: the full entity as the tools see it today
  body TEXT,                   -- markdown body for file-backed kinds
  rev INTEGER NOT NULL,        -- bumped on every write
  updated_seq INTEGER NOT NULL,-- changes.seq of the last write
  PRIMARY KEY(kind, id)
)
changes(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,            -- ISO ms
  session TEXT NOT NULL,       -- SESSION_ID (host-pid-uuid8)
  tool TEXT NOT NULL,          -- e.g. backlog_update_task
  kind TEXT NOT NULL, id TEXT NOT NULL,
  op TEXT NOT NULL,            -- create | update | archive | delete | import
  fields TEXT,                 -- JSON list of top-level keys that changed
  before TEXT, after TEXT      -- JSON, only for fields in `fields`; NULL for create/import
)
projection(
  kind TEXT, id TEXT, file TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,  -- sha1 of the bytes we last exported or imported
  mtime REAL, size INTEGER,
  dirty INTEGER DEFAULT 0,     -- 1 = DB has a change not yet exported
  exported_seq INTEGER
)
projection_base(file TEXT PRIMARY KEY, content BLOB)  -- last exported bytes, kept only while dirty=1
sessions(session TEXT PRIMARY KEY, pid INTEGER, host TEXT, started TEXT, last_seen TEXT, cwd TEXT)
-- absorbed from index.py, unchanged in meaning:
entity_paths, links, related, handover_tasks, entity_fts (FTS5, external-content on entities)
```

backlog.yaml is treated as three entity kinds (`epic`, `phase`, plus one `meta` row of kind
`backlog`) so "rewrite backlog.yaml" becomes "one of those rows changed".

### 3.3 Transaction API

```python
with store.transaction(tool="backlog_update_task", touches=[("task", task_id)]) as tx:
    task = tx.get("task", task_id)          # fresh row, inside BEGIN IMMEDIATE
    task["status"] = "in-progress"
    tx.put("task", task_id, task)           # rev+1, changes row, projection.dirty=1
result = tx.committed["task", task_id]      # the row as committed; render from this
```

- `transaction()` runs `BEGIN IMMEDIATE`. If SQLite reports busy after the 30 s timeout the
  error names the most recent writer from `changes` and any live rows in `sessions` with
  `last_seen` within 60 s: `store busy for 30s; last writer session codemaestro-e2 (pid 4120)
  2s ago via backlog_batch_update`.
- `tx.get` returns a deep copy of the JSON doc. `tx.put` diffs against the row read in the same
  transaction, so `changes.fields/before/after` are exact. Putting an unchanged doc is a no-op.
- `tx.create(kind, doc)` inserts and fails on primary-key conflict; the id allocator in 3.5 runs
  inside the same transaction.
- `tx.archive`, `tx.delete` mark rows and queue file moves for the exporter.
- On exception: rollback, nothing dirty, error propagates to the MCP client as a tool error.
- After commit: export (3.6), then FTS/paths/links refresh for the touched rows only.

**Dict-shaped compatibility mode.** Sixty-one call sites use the whole-backlog dict. Rather
than rewrite them all before anything ships, `store.load_dict()` builds today's dict shape
from `entities` (epics with nested tasks, phases, meta, orphan list) and
`store.transaction_dict(tool)` yields that dict, snapshots it privately, and on exit writes back
only entities whose doc differs. This keeps every existing tool correct on day one (fresh state
under the lock, per-entity writes, verified result) at a cost of one dict build per write
(~60 ms for 2.3k tasks, cached incrementally by `changes.seq` so the usual cost is far lower).
Hot tools then move to the row API for speed; the rest can stay on the dict API indefinitely.

### 3.4 Reads

- `store.load_dict()` replaces `_load()` for read tools and the viewer API. It is served from a
  per-process cache keyed by `MAX(changes.seq)`; a cheap `SELECT` decides whether to refresh,
  and a refresh re-deserializes only rows with `updated_seq > cached_seq`.
- Read tools that only need a few rows (`backlog_get_task`, `backlog_task_pipeline`,
  `backlog_dependencies`) switch to direct queries as a follow-up; not required for correctness.
- Reads never open a write transaction and never block on one (WAL).
- Before serving any read or write, the store runs the import check in 3.7 (mtime scan of the
  projection directories, ~5 ms for 2.3k files on NTFS, cached for 2 s per process).

### 3.5 ID allocation

Inside the creating transaction: `SELECT MAX(numeric suffix) FROM entities WHERE kind='task'
AND epic=? ` including archived rows, plus a directory scan of `tasks/` and `tasks/archive/`
for the same prefix so an orphaned file from an older partial write is never reused. The insert
uses the primary key as the final guard. Same rule for bugs, issues, decisions, ideas, handovers,
areas with their own prefixes. `options.task_id` overrides remain honored and conflict-checked.

### 3.6 Export (DB → files)

- Runs after commit, in the same tool call, for the rows the transaction touched. backlog.yaml
  is rewritten only if an `epic`, `phase`, or `backlog` row changed. `local/PROGRESS.md` and
  `context` regeneration stay as today but read from `load_dict()`.
- Each file: write `<file>.tmp.<session>`, `os.replace` with retry on `PermissionError` and
  `OSError` 5/32 for up to 2 s (jittered 20–200 ms), re-read, compare hash, update `projection`
  (hash, mtime, size, `dirty=0`, `exported_seq`).
- Export failure after retries: leave `dirty=1`, log to `local/store.log`, return the tool's
  normal success (the DB commit is the truth) with a one-line suffix
  `(export pending: tasks/x.md — will retry on next call)`. Every store open and every
  transaction first drains dirty rows, so a pending export lasts one call, not a session.
- Archive and delete: file moves happen here too, through the same retry path.
- Because exports are per-entity, a session's export never touches another session's hot files
  unless both edit the same entity, which the transaction already serialized.

### 3.7 Import (files → DB)

- Trigger: scan `projection` files' mtime/size; any mismatch, any file on disk not in
  `projection`, any `projection` row whose file is gone. Cheap, cached 2 s.
- For each changed file, inside one write transaction:
  - Row not dirty: parse the file, replace the doc, record a `changes` row with `op=import`
    and `session=<external>`.
  - Row dirty (DB has an unexported change): three-way merge with base = the content at
    `projection.content_hash` (kept in a `projection_base` blob table for dirty rows only),
    ours = DB doc, theirs = file. Field-level merge reuses `_three_way_merge_fields`; body
    merge takes the side that changed, ours if both. The result is written to DB and
    exported; a `changes` row with `op=merge` records both inputs.
  - File gone and row not dirty: mark archived-or-deleted per directory (a task file under
    `tasks/archive/` is an archive, elsewhere it is a delete).
- No DB at all (fresh clone, corruption rebuild, v2/v3 backlog): full import. v2/v3 task lists
  inside backlog.yaml are imported as task rows; the first export writes them as `tasks/*.md`
  and drops them from backlog.yaml. That is the migration.

### 3.8 Observability

- `backlog_store_status()` tool: schema version, db size, WAL size, last 20 `changes` rows
  (ts, session, tool, kind/id, fields), dirty export count, live sessions, sync-folder warning,
  merge conflicts in the last 24 h.
- Write tool results end with `[seq N]`, the `changes.seq` of their commit, so an agent can cite
  proof and a reviewer can `backlog_query("select * from changes where seq=N")`.
- `backlog_query` gains `changes`, `projection`, `sessions` in its documented schema.
- `local/store.log`: export retries, import merges, corruption rebuilds. Rotated at 1 MB.

### 3.9 Error handling summary

| Condition | Behaviour |
|---|---|
| Busy > 30 s | Tool error naming last writer and live sessions; nothing written |
| Export rename fails after 2 s | Commit stands; `dirty=1`; suffix in result; retried next call |
| DB corrupt on open | Delete, rebuild from files, warn in result |
| File changed under a dirty row | Three-way merge, `op=merge` recorded, no data lost on either side |
| Duplicate id on create | Integrity error → tool error; allocator makes this unreachable in practice |
| Sync-folder path | One warning per process; operation continues |
| Schema migration needed | Rebuild from files (index semantics unchanged from 5.2.0) |

## 4. Tool migration

1. `_load()` → `store.load_dict()`; `_save()` → removed; `_mutate_and_save(data)` → only valid
   inside `store.transaction_dict()`; calling it outside raises. All 61 sites wrapped in the
   dict transaction mechanically. `_LOAD_SNAPSHOT` and `_backlog_lock` deleted.
2. Row API for the hot path: `backlog_update_task`, `backlog_record_gate`, `backlog_skip_gate`,
   `backlog_record_merge`, `backlog_add_task`, `backlog_complete_task`, `backlog_pick_task`,
   `backlog_batch_update`, `backlog_note`.
3. File-backed entity writers (`write_handover`, `write_bug`, `write_issue`, `write_decision`,
   `write_idea`, `write_note`, `write_area`, `write_tracker`) become `tx.create/put` on their
   kind; their file rendering moves into the exporter.
4. `index.py` build path becomes the FTS/paths/links refresh for touched rows; `build_index`
   survives only as the full-rebuild routine.
5. Skills and playbooks: remove "retry with read-back verification" guidance; document `[seq N]`.

## 5. Testing

- **Multiprocess stress** (`tests/test_store_concurrency.py`): spawn 8 processes, each running
  200 mixed ops (add, update status/human_action, record_gate, record_merge, note, archive) on
  one temp v4 backlog through the real MCP tool functions. Assert: every op's `[seq N]` exists in
  `changes`, every asserted field value is present at the end, all ids unique, projection files
  match DB for every entity, no `.tmp` files left.
- **Sequential same-task** (the single-session loss case): two updates to one task in one
  process, then two in one thread pool concurrently; both fields present.
- **Open-handle contention** (Windows-only, skipped elsewhere): a helper process holds
  `tasks/x.md` open while a write to x commits; commit succeeds, export retries, file matches
  within 2 s.
- **Import/merge**: hand edit while dirty; git-style whole-file replace; file deletion; fresh
  clone with no DB; v3 backlog import produces v4 layout and identical `load_dict()` output.
- **Busy timeout**: hold `BEGIN IMMEDIATE` from a helper for 35 s; assert the error text names it.
- **Corruption**: truncate the db mid-file; assert rebuild and identical state.
- **Existing 182 tests** pass unchanged: the `tmp_taskmaster` fixture writes files, the first
  store open imports them.
- **Value content**: fields containing arrows, parentheses, YAML-significant characters round
  trip DB → file → DB byte-identical (one report suspected sanitization loss).

## 6. Sequencing (each step mergeable)

1. `store.py`: schema, open/rebuild, transaction and dict-transaction APIs, import, export,
   id allocation, sync-folder warning. Unit tests for each. No tool wired yet.
2. Wire `_load`/`_mutate_and_save` through the store; delete snapshot and lock globals; run the
   full suite; add the stress and sequential tests. This step alone fixes defects 1–5.
3. Row-API conversions for the hot tools; `backlog_store_status`; `[seq N]` suffixes.
4. Absorb `index.py` (FTS/paths/links per-row refresh; `backlog_query` schema docs);
   entity writers through the store.
5. Docs, skill text cleanup, changelog, release; migrate CodeMaestro by opening it and
   confirming `backlog_store_status`.

Adversarial review (fresh-context reviewer, and Codex per the money-path/high-stakes rule
analogue for data integrity) after steps 1 and 2, focused on import/export merge and the
busy path.

## 7. Out of scope

- Simultaneous writers on two machines sharing one folder (unsupported; warned).
- Event-sourced per-writer logs and CRDT merge semantics (considered; unnecessary once writes
  serialize at millisecond granularity).
- Viewer changes: it already reads through the server API.
- The two unrelated bugs in §1 and the five non-write inbox reports.
