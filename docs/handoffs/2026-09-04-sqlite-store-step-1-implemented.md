<!-- User intent: let a fresh implementation session continue the approved SQLite-store
     migration without re-discovering Step 1 or accidentally claiming Step 2 is complete. -->

# Handover — SQLite store Step 1 implemented; Step 2 next

**Date:** 2026-09-04
**Session kind:** milestone (Step 1 implemented, reviewed, tested, and committed)
**Thread:** sqlite-store
**Branch:** `feature/sqlite-store-step-1`
**Tip:** `446b612b0aa434f992ebf7a5d11f2fbdb163530f`
**Commit:** `446b612 feat(store): add SQLite authority and projection sync`
**Worktree:** `C:\Users\gruku\Files\Claude\taskmaster\.worktrees\sqlite-store-step-1`
**Push status:** not pushed

## Resume prompt

> Open `C:\Users\gruku\Files\Claude\taskmaster\.worktrees\sqlite-store-step-1` and verify
> that `feature/sqlite-store-step-1` is clean at `446b612`. Read Step 2 in
> `docs/plans/2026-09-04-sqlite-store.md` and the approved design in
> `docs/specs/2026-09-04-sqlite-store-design.md`. Before implementing Step 2, fast-forward
> `master` to the reviewed Step 1 commit without modifying or discarding the root checkout's
> pre-existing `uv.lock` change. Then create `.worktrees/sqlite-store-step-2` on
> `feature/sqlite-store-step-2` from that merged commit. Implement Step 2 with TDD, run the
> focused and full suites, and complete fresh-context plus Codex review. Do not push.

## Where execution stands

- The SQLite-store design was approved and adversarially reviewed before implementation; all
  36 recorded design findings were resolved in spec revision 2.
- The implementation plan for spec §6 Steps 1–2 is at
  `docs/plans/2026-09-04-sqlite-store.md`.
- Step 1 is fully implemented and committed. `taskmaster/store.py` is deliberately not wired
  into `backlog_server.py` yet.
- The Step 1 store contract suite passes: **133 passed**.
- The full repository suite passes: **1,791 passed, 1 skipped**.
- A fresh adversarial implementation review was completed; confirmed critical/high findings
  were fixed with regression tests before commit.
- Step 2 is planned but intentionally unimplemented.
- This repository has no Taskmaster backlog of its own. Progress authority is the plan and
  `docs/handoffs/`; do not use the plugin fixture's `backlog.yaml`.

## What shipped

| Area | Result |
|---|---|
| Store authority | SQLite schema, metadata, migrations, transaction API, and compatibility-dict API |
| Repository identity | Common-git-dir root resolution, creation-token validation, linked-worktree handling |
| Bootstrap/import | Deterministic import of the supported v3/v4 file projections |
| Transactions | Nested identity-stable compatibility transactions, commit latch, rollback, optimistic merge/conflict behavior |
| ID allocation | Durable, non-recycling allocation with tombstone-aware sequences |
| Projection | Atomic file rendering, retry/convergence behavior, crash export intents, conflict artifacts |
| Recovery | Database replacement detection, corruption quarantine, exact-byte projection repair |
| Derived state | Derived table rebuild without replacing or unlinking the authoritative database |
| Platform policy | Local write authority, network-filesystem projection-only reads, cloud-sync warnings |
| Test coverage | Nine focused modules covering schema, root, import, transactions, inventory, IDs, projection, recovery, and derived state |

The committed files are:

- `taskmaster/store.py`
- `tests/test_store_schema.py`
- `tests/test_store_root.py`
- `tests/test_store_import.py`
- `tests/test_store_transactions.py`
- `tests/test_store_inventory.py`
- `tests/test_store_id_allocation.py`
- `tests/test_store_projection.py`
- `tests/test_store_recovery.py`
- `tests/test_store_merge_and_derived.py`
- `docs/plans/2026-09-04-sqlite-store.md` (execution status only)

## Next implementation — Step 2

1. Merge Step 1 locally onto `master` with a fast-forward. Preserve the root checkout's
   unrelated modified `uv.lock` and untracked `.worktrees/`; do not clean or reset them.
2. Create `.worktrees/sqlite-store-step-2` and branch `feature/sqlite-store-step-2` from the
   merged Step 1 commit.
3. Write failing server-integration tests, then replace `_load()` with `store.load_dict()`,
   remove `_LOAD_SNAPSHOT` and `_backlog_lock`, and make `_mutate_and_save(data)` legal only
   for the active `transaction_dict()` object with an explicit commit latch.
4. Wrap every load/mutate/save path in a transaction named after the public tool. Preserve
   nested object identity, move auto-linking into the transaction, and build responses from
   committed state.
5. Audit every implicit removal from task, epic, and phase collections. Convert archive and
   move paths to explicit store operations; add a regression test for every removal site.
6. Add bypass protection for writes to `backlog.yaml`, `tasks/`, `epics/`, and `phases/`, then
   reroute all task/epic/phase and viewer production writers through the store. Keep the
   documented narrow Step 3 allowlist only for non-task kinds.
7. Add deterministic sequential, thread-pool, two-process, 8×200-process stress, Windows
   contention, linked-worktree, viewer ETag, and derived-rebuild tests.
8. Run the focused and full verification commands from plan §2.5. Repeat fresh-context and
   Codex reviews, resolve all critical/important findings, and commit as
   `feat(store): route task backlog writes through SQLite transactions`.

## Exact Step 2 boundary

Step 2 fixes the five documented write defects for tasks, epics, and phases. It does **not**
complete the whole migration: bug, issue, handover, decision, idea, note, area, and tracker
writers remain on a narrow compatibility allowlist until Step 3. Do not claim the SQLite
migration complete until Steps 3–5 and real CodeMaestro open/status verification have shipped.

Do not optimize hot tools to row APIs during Step 2. First make the compatibility transaction
boundary correct; row-API conversion belongs to spec Step 3.

## Step 2 files of interest

| Path | Purpose |
|---|---|
| `docs/plans/2026-09-04-sqlite-store.md:279` | Approved Step 2 implementation sequence and verification commands |
| `docs/specs/2026-09-04-sqlite-store-design.md` | Normative design and invariants |
| `taskmaster/store.py` | Step 1 public store surface; do not duplicate its ownership logic |
| `taskmaster/backlog_server.py` | `_load`, `_save`, `_mutate_and_save`, globals, public tools, and viewer handlers to wire |
| `taskmaster/taskmaster_v3.py` | Retain pure parser/renderer/path/merge helpers; remove production write ownership |
| `tests/conftest.py` | Store bootstrap/reset and projection-write bypass guard |
| `tests/test_store_server_integration.py` | New compatibility-boundary integration coverage |
| `tests/test_store_concurrency.py` | New real-operation concurrency and stress coverage |
| `tests/test_store_bypass.py` | New direct-projection-write rejection coverage |
| `C:\Users\gruku\Files\Work\CodeMaestro\.taskmaster` | Final real-backlog verification target; never use it as a test fixture |

## Important non-obvious constraints

1. `taskmaster/store.py` cannot import `backlog_server.py`; wire derived-data callbacks with
   `configure_derivers()` to avoid an import cycle.
2. Acquire the OS recovery mutex before `BEGIN IMMEDIATE`; recovery and normal writers must
   have one lock order.
3. Revalidate the database generation/inode and use SQLite URI `mode=rw` so a connection
   cannot silently create a replacement database after external deletion or replacement.
4. Projection crash recovery uses durable export intents. Preserve `.intent-conflict-*`
   artifacts when intent replay detects a conflicting destination.
5. Corruption recovery quarantines the database and performs exact-byte projection repair;
   do not normalize or casually re-render evidence during quarantine.
6. Network filesystems are projection-only: no writes, migrations, checkpoints, or recovery
   mutation. Cloud-synced local folders warn but remain writable.
7. A busy heartbeat cannot commit while another write transaction is active. Same-host stale
   `current_tool` information is retained while the recorded PID is alive so a long writer
   remains diagnosable.
8. `sqlite3.OperationalError` is a `DatabaseError`; lock/busy errors must never enter the
   corruption-rebuild path.
9. FastMCP sync tools execute in a thread pool, so correctness is required across threads in
   one process as well as across processes.
10. Nested `_load()` calls in completion, related-task expansion, frontmatter link expansion,
    and Linear enqueue logic must return the same active compatibility dict.
11. Compatibility write-back intentionally ignores missing entities. Every intended archive
    or delete must therefore become an explicit operation.
12. Viewer reads must use committed store state. Its ETag becomes
    `creation_token:max_seq`; viewer writes use the same transaction path as MCP tools.
13. `backlog_index_status(rebuild=True)` must call `Store.rebuild_derived()` and must never
    unlink or replace `store.db`.
14. The bypass fixture must guard `atomic_write`, `write_task_file`, `Path.write_text`,
    `Path.write_bytes`, `os.replace`, and `shutil.move` for projection paths while allowing
    fixture setup before store bootstrap.

## Verification notes

The Step 1 commit was verified with:

```powershell
uv run pytest -q tests/test_store_schema.py tests/test_store_root.py `
  tests/test_store_import.py tests/test_store_transactions.py `
  tests/test_store_inventory.py tests/test_store_id_allocation.py `
  tests/test_store_projection.py tests/test_store_recovery.py `
  tests/test_store_merge_and_derived.py
uv run pytest -q
```

For the full-suite run, keep pytest's base temp directory outside the Git checkout; otherwise
the project-root detection test sees the temp project as nested in this repository. Two TLDR
tests launch bare system `python` and overwrite `PYTHONPATH`; verification temporarily used a
`sitecustomize.py` compatibility shim and removed it afterward. Do not commit such a shim.

## Working-tree state at handover

- Step 1 worktree was clean at `446b612` before this handover file was written; this handover
  file is the only expected new uncommitted file there.
- Main checkout remained on `master` at `48d27f2` with pre-existing unrelated state:
  modified `uv.lock` and untracked `.worktrees/`.
- An adversarial probe briefly created a synthetic root `.taskmaster/`; it was verified as
  test debris and removed. If one reappears, do not treat it as the project backlog.
- No branch or commit was pushed.
