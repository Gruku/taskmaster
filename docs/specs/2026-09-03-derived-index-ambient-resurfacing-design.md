<!-- User intent: make the agent working in a large backlog (CodeMaestro: 2.3k tasks, 450 bugs, 350 handovers)
     automatically see related open work for the file it is editing, and be able to ask arbitrary
     questions of the backlog, without visiting the viewer. Files stay the source of truth. -->

# Derived Index, Ambient Resurfacing, and SQL Query — Design

**Date:** 2026-09-03
**Status:** superseded by the SQLite store (6.0.0) — `index.db` is absorbed into `.taskmaster/local/store.db`; see `2026-09-04-sqlite-store-design.md`
**Case study:** `C:\Users\gruku\Files\Work\CodeMaestro\.taskmaster`

## 1. Problem

The user's Taskmaster usage is agent-first: the agent does the work inside the backlog and the
user reads a short handover pasted into Telegram. Two gaps limit how well the agent resurfaces
related work:

- **No cross-entity retrieval.** `backlog_search` is substring match over tasks only. Bugs,
  issues, handovers, and decisions are not searchable, and handovers hold the richest prose
  (2.6 MB on CodeMaestro).
- **Resurfacing depends on the agent remembering to search.** Structured file-path fields
  already exist (task `anchors` 44%, bug `location` 80%, issue `location` 83%), but nothing
  joins them, so "what touches the file I am editing" is never asked.

Secondary costs: every MCP call re-parses the whole backlog from disk (2.3 MB YAML plus 7.4 MB
of markdown, no cache), and the handover confirm step drops the file path the user wants to
paste alongside the summary.

Findings that shaped the design: only 42% of tasks carry a dependency edge and 41% are fully
isolated, so an explicit graph traversal feature would be built on sparse data. Epic membership
(100%) and shared file paths are the real edges.

## 2. Principles

1. **Files remain canonical.** The index is derived, gitignored, and safe to delete at any time.
   No tool writes through the index. This matches the Jira design's derive-on-scan decision.
2. **Ambient over remembered.** Resurfacing happens on the agent's edit, not on a search it
   must think to run.
3. **One line, no second line.** The hook output is a single line. The nudge toward deeper
   history lives in the `backlog_query` tool description, not in the hook.
4. **Give agents SQL, not a query language.** One read-only tool over a documented schema
   replaces a growing set of bespoke query tools.

## 3. Component: derived index

### 3.1 Location and lifecycle

- Path: `.taskmaster/local/index.db`. `.taskmaster/local/` is already the derived-data
  directory (`_write_local_meta_cache`); `backlog_init` and the v4 migration must ensure it is
  gitignored.
- SQLite via the standard library. FTS5 for text. No new dependency.
- Schema version stored in a `meta` table. Missing DB or version mismatch triggers a full
  rebuild. Corruption (any `sqlite3.DatabaseError` on open) deletes and rebuilds.

### 3.2 Build

`taskmaster/index.py` exposes:

- `build_index(backlog_path, *, budget_s: float | None = None) -> IndexReport`
  Walks `backlog.yaml` (tasks live there, not in `tasks/*.md`), `project.yaml`, and every
  entity directory (`tasks/`, `epics/`, `bugs/`, `issues/`, `handovers/`, `decisions/`,
  `ideas/`, plus their `archive/` subdirectories). Stores mtime and size per source file in
  `sources`; re-ingests only changed or new files, deletes rows for removed files.
  When `budget_s` is set and the pending set would exceed it, the build stops and the report is
  marked `stale=True` with `pending_files` populated.
- `open_ro(backlog_path) -> sqlite3.Connection` — opens with `mode=ro`, raises if missing.
- `IndexReport`: `built_at`, `full_rebuild`, `files_ingested`, `pending_files`, `stale`,
  `row_counts` per table, `elapsed_ms`.

Trigger points:

- The MCP server calls `build_index(budget_s=1.5)` inside `_load()`, so every tool call keeps
  the index current, including after out-of-band file edits.
- The SessionStart hook runs `backlog_server.py --build-index` in the background so the first
  edit of a session never sees a stale index.
- The edit hook never builds. It runs under system Python with the standard library only, reads
  the DB, and appends `(index stale)` when `backlog.yaml` or an entity directory is newer than
  the recorded `built_at_epoch`. A missing DB makes the hook silent.

### 3.3 Schema

```sql
meta(key TEXT PRIMARY KEY, value TEXT);                       -- schema_version, built_at
sources(file TEXT PRIMARY KEY, mtime REAL, size INTEGER, kind TEXT);

entities(
  id TEXT PRIMARY KEY, kind TEXT,        -- task|epic|bug|issue|handover|decision|idea
  status TEXT, title TEXT, epic TEXT, phase TEXT, lane TEXT,
  repo TEXT,                             -- see 3.4
  priority TEXT, created TEXT, updated TEXT, archived INTEGER,
  file TEXT                              -- source file, repo-relative
);
entity_paths(entity_id TEXT, path TEXT, match_kind TEXT, source TEXT);
  -- match_kind: exact|glob ; source: anchors|location|prose
links(src TEXT, type TEXT, dst TEXT);    -- typed links + depends_on, both directions
handovers(id TEXT PRIMARY KEY, thread TEXT, tldr TEXT, next_action TEXT,
          session_kind TEXT, branch TEXT, tip_commit TEXT, supersedes TEXT);
handover_tasks(handover_id TEXT, task_id TEXT);
related(a TEXT, b TEXT, via TEXT, weight INTEGER);  -- see 3.5
entity_fts USING fts5(id UNINDEXED, kind UNINDEXED, title, body, tokenize='porter unicode61');
```

Indexes on `entity_paths(path)`, `entities(kind, status)`, `entities(repo)`, `links(dst)`,
`related(a)`.

### 3.4 Repo inference

Only tasks carry `sub_repo`. For every entity, `repo` is filled from `sub_repo` when present,
otherwise from the longest matching `repos[].path` prefix in `project.yaml` applied to the
entity's paths. Null when nothing matches. Single-repo projects get a constant.

### 3.5 Derived edges

`related` holds implicit edges the explicit link fields lack:

- `via='path'`: two entities share at least one exact path, or an exact path matches the
  other's glob. Weight = number of shared paths.
- `via='handover'`: two task ids listed in the same handover `task_ids`.
- `via='epic'` is not stored; join on `entities.epic` instead.

Rows are unordered pairs stored once with `a < b`.

### 3.6 Prose path extraction

For handover, decision, idea, and bug bodies, a regex extracts repo-relative paths: two or more
segments, a known code or config extension, no URL scheme, no whitespace. Absolute Windows paths
under the project root are normalized to repo-relative. Stored with `source='prose'` so
consumers can discount them. Paths outside the project (for example transcript `.jsonl` paths)
are dropped.

## 4. Component: ambient edit hook

### 4.1 Trigger

`PostToolUse` matcher `Edit|Write|MultiEdit`, wired through the existing `run_hook.sh`
mechanism as `hooks/edit_resurface.py`. Skips when: no `.taskmaster/` in the project root,
the file is under `.taskmaster/` itself, the file is outside the project root, or the tool
call failed.

### 4.2 Resolution

The edited absolute path is made repo-relative with forward slashes. Matching:

- exact rows: equality on normalized path;
- glob rows: `fnmatch` with `**` support (use `pathlib.PurePath.match` semantics or a small
  translator; `fnmatch` alone treats `**` as `*`).

Only `source in (anchors, location)` rows drive the open-items list. Prose-derived matches are
counted but not listed by id.

### 4.3 Output

Exactly one line, injected as hook context, when at least one open entity matches:

```
TM: code-maestro-facade/.../ModelUsageService.cs → B-231 open, B-198 open, abuse-path-003 in-progress, HND 2026-09-02-mapped-unimpl… (+4 closed, +2 prose)
```

Rules:

- Open means task status in `todo|in-progress|blocked|in-review`, bug status `open|adopted`,
  issue status `open|investigating`, handover status `open`.
- Order: bugs, issues, tasks, handovers. Cap at 6 ids; overflow becomes `+N more`.
- Handover ids truncated to 24 characters with an ellipsis.
- `(+N closed)` counts matched done/fixed/archived entities. `(+N prose)` counts prose-only
  matches. Either is omitted when zero.
- Silent when nothing matches at all, including when only closed items match. (Closed history
  is reachable through `backlog_query`; the tool description says so.)
- Append `(index stale)` when source files are newer than the index (see 3.2).

### 4.4 Dedupe

Fires once per normalized path per session. Session key is the hook's `session_id`; seen paths
are stored in `.taskmaster/local/hook-seen/<session_id>.json`. Files older than seven days are
pruned on write.

### 4.5 Budget

Whole hook, interpreter startup included, must complete under 1 s on the CodeMaestro fixture.
A test asserts the query portion stays under 50 ms on a 3k-entity synthetic index. Hook
timeout in `hooks.json`: 10 s, matching the other ported hooks.

## 5. Component: query tool and search

### 5.1 `backlog_query(sql: str, limit: int = 50) -> str`

- Opens the index read-only. Rebuilds first if missing.
- Guard: exactly one statement; must start with `SELECT` or `WITH`; rejects `;` outside string
  literals, `ATTACH`, `PRAGMA`, and any write keyword. Uses `conn.set_authorizer` to deny
  anything except read on the known tables as defense in depth.
- Row cap enforced by wrapping: `SELECT * FROM (<sql>) LIMIT :limit`; hard max 500.
- Output: compact aligned table, then `N rows (capped)` when truncated. Errors return the
  SQLite message and the schema summary.
- Tool description carries the schema summary, the open/closed status vocabulary, and three
  example queries: open bugs by repo, everything touching a path including closed history, and
  FTS over handovers. The description is the only place the "dig deeper" nudge lives.

### 5.2 `backlog_index_status() -> str`

Returns the last `IndexReport`: built_at, row counts per table, pending files, stale flag.
Accepts `rebuild: bool = False` to force a full rebuild.

### 5.3 `backlog_search` upgrade

Adds `kinds: list[str] | None`. Internally switches to `entity_fts` with `bm25` ranking and
keeps its current output shape. Falls back to the existing substring scan when the index is
unavailable, so behavior never regresses.

## 6. Handover integration

### 6.1 Paste block

Playbook `handover/playbook.md` step 9 changes from "echo the Resume line" to "end the reply
with the paste block":

```
<tldr>
<absolute file path>
Resume: <thread> — <next_action>
```

Fenced, no headers, nothing else after it. The server response from `backlog_handover_create`
adds `- Path: <absolute>` next to the existing relative `- File:` line so the agent never
computes the path itself. WARNING lines still surface above the block.

### 6.2 Reverse lookup

Step 2 of the playbook (auto-extraction of touched paths) gains a query against `entity_paths`
for each touched path. Matched open bugs, issues, and tasks are proposed for `task_ids` and,
for bugs, the agent asks in one line whether the edit fixed them. No status change happens
without the user's answer.

## 7. Error handling

- Index unavailable or corrupt: hook stays silent; `backlog_query` rebuilds; `backlog_search`
  falls back. Build failures inside `_load()` are logged to `.taskmaster/local/index.log` and
  never fail the tool call.
- Hook exceptions never surface to the tool call; they are logged to
  `.taskmaster/local/hook.log`, capped at 1 MB with truncation.
- Build failures on a single malformed entity file skip that file and record it in
  `IndexReport.errors`; they do not abort the build.

## 8. Testing

- `tests/test_index_build.py`: fixture backlog with every entity kind; asserts row counts,
  incremental re-ingest on mtime change, deletion on file removal, repo inference, `related`
  edges, prose extraction including the `.jsonl` exclusion.
- `tests/test_edit_hook.py`: synthetic PostToolUse events; exact expected line for open,
  closed-only (silent), prose-only, glob anchors, `**` globs, dedupe, stale flag, outside-root
  skip, timing budget.
- `tests/test_backlog_query.py`: guard rejections (`DELETE`, `PRAGMA`, `ATTACH`, two statements,
  comment-hidden writes), cap behavior, error output includes schema.
- `tests/test_handover_paste_block.py`: server response contains absolute path; playbook eval
  asserts the block is the final content of the reply.

## 9. Sequencing

1. Index builder and `backlog_index_status`, wired to server writes and SessionStart.
2. Edit hook.
3. `backlog_query` and the `backlog_search` upgrade.
4. Handover paste block and reverse lookup. Independent of 1 to 3 except reverse lookup,
   which needs 1.

Each step ships with its tests and a CHANGELOG entry. Step 4's paste block can land first if
wanted since it is a playbook plus one server line.

## 10. Out of scope

- Read-hook resurfacing (too frequent, drowns the signal).
- Embeddings or semantic search.
- Graph traversal features beyond the existing `backlog_link query`.
- Writing to entities through the index.
- Viewer changes.
