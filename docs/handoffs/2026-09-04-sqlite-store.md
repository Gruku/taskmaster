<!-- User intent: let a fresh session pick up the SQLite store work (concurrent multi-agent
     backlog writes) with zero re-exploration: what was decided, what was reviewed, what to do first. -->

# Handover — SQLite store for concurrent multi-agent writes

**Date:** 2026-09-04
**Session kind:** milestone (design + adversarial spec review complete; no code written)
**Branch:** master, tip `066b782` (spec rev 2). Working tree otherwise: `uv.lock` modified
(pre-existing, unrelated), `.worktrees/` untracked.
**Thread:** sqlite-store

## Resume prompt

> Read `docs/specs/2026-09-04-sqlite-store-design.md` in full (it is the approved, reviewed
> design). Then invoke `superpowers:writing-plans` to produce
> `docs/plans/2026-09-04-sqlite-store.md` covering spec §6 steps 1–2 first (store module, then
> wiring + tests). Implementation floor: opus sub-agents, TDD, one worktree per step. Do not
> push. Do not start coding before the plan exists. This repo has no `.taskmaster/` backlog of
> its own; track progress in the plan file and in `docs/handoffs/`.

## Where execution stands

- Design chosen by the user after three rounds: (1) lock + journal, (2) event-sourced
  per-writer logs, (3) **SQLite as runtime authority with files as git projection** — chosen.
- Spec rev 1 committed `7788528`; adversarial review by a fresh-context opus agent and a
  Codex pass produced 36 findings (8 critical). All resolved in rev 2, `066b782`.
- Gate matrix (advisory, no backlog task exists in this repo to record it on):
  Spec sanity PASS · Adversarial (Claude) FAIL→resolved in rev 2 · Adversarial (Codex)
  FAIL→resolved in rev 2 · Blast radius WARN (touches every write tool, viewer, three hooks).
- No implementation, no plan yet. Tests: 182 existing, untouched, all expected green.

## What shipped this session

| # | What | Where |
|---|------|-------|
| 1 | Root-cause analysis of 35 inbox write-loss reports (5 defects, all verified in code) | spec §1 |
| 2 | Approved design, rev 2, every review finding resolved inline | `docs/specs/2026-09-04-sqlite-store-design.md` |
| 3 | Measured baseline: CodeMaestro (v3, 2.3k tasks) load 1.5 s, dump 1.1 s, libyaml on | spec §1 |

## What's next

1. Write the implementation plan (`superpowers:writing-plans`) for spec §6 steps 1–2.
2. Implement step 1 (`taskmaster/store.py`) in a worktree with TDD; run a fresh-context
   review plus Codex on import/export/root resolution before step 2.
3. Implement step 2 (wire `_load`/`_mutate_and_save`, delete globals, bypass-detection
   fixture, stress test). Audit which tools remove entities from the dict implicitly
   (`backlog_archive_task`, `backlog_archive_epic`, others) and convert them to explicit ops.
4. Steps 3–5 per spec; migrate CodeMaestro last by opening it.
5. File the two unrelated bugs from the inbox: `backlog_add_epic` clobbers a hand-written
   epic body; `backlog_record_gate` accepts a pass that `complete_task` rejects. Then triage
   the five non-write inbox reports (`/inbox`).

## Files of interest

| Group | Path | What | Why next session needs it |
|---|---|---|---|
| Touched | `docs/specs/2026-09-04-sqlite-store-design.md` | the design, rev 2 | the plan is derived from it; §6 is the step order |
| Read | `taskmaster/backlog_server.py:300-520` | `_load`, `_save`, `_LOAD_SNAPSHOT`, `_backlog_lock` | these are what step 2 replaces |
| Read | `taskmaster/backlog_server.py:904` | `_mutate_and_save` (context regen, save, PROGRESS.md) | becomes transaction-only; context becomes derived |
| Read | `taskmaster/backlog_server.py:4380-4460, 5273-5600` | `backlog_add_task`, `backlog_update_task` | first row-API conversions; id allocator at 4448 |
| Read | `taskmaster/backlog_server.py:7605-7700, 8062` | viewer `_serve_json`, POST handlers, PUT | bypass writers and stale ETag to replace (step 4) |
| Read | `taskmaster/taskmaster_v3.py:50, 1075, 1111, 4145-4330, 4528, 4605` | `atomic_write`, `next_task_id`, `load_v4`, `save_v4`+merge, `with_file_lock`, viewer `create_task` | renderers/merge stay; writers go |
| Read | `taskmaster/index.py:31-84, 255-310, 693-800` | schema, globs (handover archive glob wrong), open, build | absorbed into store.db (step 4) |
| Read | `hooks/edit_resurface.py:192-240, 333` | root finder, staleness by mtime, hardcoded index.db | must move to store.db + seq (step 4) |
| Read | `tests/conftest.py:30-100` | `tmp_taskmaster` fixture (files + ROOT monkeypatch) | store must bootstrap by import; cache keyed by creation token |
| Relevant | `C:\Users\gruku\Files\Work\CodeMaestro\.taskmaster` | real 2.3k-task v3 backlog | performance baseline and final migration target; never write to it from tests |
| Relevant | `C:\Users\gruku\Files\Claude\claude-tools\inbox\` | 35 write-loss reports + 5 unrelated | evidence; triage after the fix ships |

## Resolved this session

- Authority: DB authoritative, files projected (user choice over "files + DB as lock").
- One store per repository at the main checkout via `git rev-parse --git-common-dir`; the
  backlog is per repository, not per branch.
- Absence of a file never deletes; tombstones keep ids from recycling.
- `context` is no longer persisted in backlog.yaml.
- Merge `index.db` into `store.db`.
- Network filesystems refuse writes; cloud-synced local folders warn and continue.
- Busy wait 30 s then error naming probable holders.
- Contingency if the sync surface proves unmanageable in step-1 review: SQLite as mutex + id
  sequence only, files stay authoritative (spec §8).

## Important non-obvious things

1. FastMCP runs sync tools in a thread pool: one session's sub-agents already race inside one
   process. Any fix must handle intra-process concurrency, not just cross-process.
2. Four inbox reports reproduced silent loss with **no** second session (mtime-verified). The
   stale-snapshot bug is independent of concurrency; the sequential test in spec §5 guards it.
3. `ROOT` is the MCP server's cwd (`backlog_server.py:42`). A session started inside a
   worktree gets its own `.taskmaster/` copy today. Spec decision 2 changes that.
4. The viewer's `GET /api/backlog` reads raw backlog.yaml and its POST path uses
   `taskmaster_v3.create_task` with a `filelock` that is not a dependency (falls back to a
   thread lock). Both are bypasses to remove, not to keep "out of scope".
5. `backlog_index_status(rebuild=True)` unlinks the DB file. Once store.db is the authority
   that tool must rebuild derived tables only. Do this in step 1, not step 4.
6. `sqlite3.OperationalError` is a `DatabaseError`; "database is locked" must never trigger
   the corruption rebuild path.
7. The `tm` MCP server failed to connect in this session; plugin tools
   (`mcp__plugin_taskmaster_tm__*`) were not needed because this repo has no backlog.
8. User rules for this work: opus floor for implementation agents, always pin `model`, Codex
   review for this data-integrity code, never push without asking, no `git worktree remove
   --force`.
