<!-- User intent: let a fresh session pick up the SQLite-store migration at spec step 3 without
     re-deriving what Step 2 shipped, what it deliberately left out, and what the reviews found. -->

# Handover — SQLite store Step 2 implemented; Step 3 next

**Date:** 2026-09-05
**Session kind:** milestone (Step 2 implemented, reviewed by Claude + Codex, fix wave closed, merged locally)
**Thread:** sqlite-store
**Branch:** `feature/sqlite-store-step-2` → merged into `master` with `--no-ff` (see merge SHA below)
**Worktree:** `C:\Users\gruku\Files\Claude\taskmaster\.worktrees\sqlite-store-step-2`
**Push status:** not pushed — nothing has left the machine

## Resume prompt

> Open `C:\Users\gruku\Files\Claude\taskmaster` on `master` and verify the Step 2 merge commit is
> HEAD and `uv run pytest -q --basetemp=%TEMP%\tm-pytest` is green (≈13 min; the 8×200 stress
> test runs by default). Read the "Execution status — 2026-09-05" section of
> `docs/plans/2026-09-04-sqlite-store.md`, the "Unreleased — SQLite store" changelog entry, and
> spec §6 step 3 in `docs/specs/2026-09-04-sqlite-store-design.md`. Write the step-3 plan
> (non-task kinds onto the store, row-API hot paths, generalized committed-state rendering,
> removal of the read-scan throttle poke and the compatibility allowlist) with
> `superpowers:writing-plans`, then execute it in a new worktree. Do not push.

## Where execution stands

- Step 1 (store module) and Step 2 (server wiring) of the approved plan are implemented,
  reviewed, and merged locally. Steps 3–5 remain.
- Full suite after the fix wave: **1,926 passed, 1 skipped** (13m30s). The committed stress
  profile is 8 processes × 200 real public-tool calls against one shared store.
- Step 2 boundary (exact, from plan §2.5): defects 1–5 are fixed **for tasks, epics and phases
  only**. Bug, issue, handover, decision, idea, note, area and tracker writers remain on the
  named compatibility allowlist (list in the changelog) and **their id allocation is not
  concurrency-safe yet**. Do not claim the migration complete until steps 3–5 and real
  CodeMaestro open/status verification have shipped.
- This repository has no Taskmaster backlog of its own; progress authority is the plan file
  and `docs/handoffs/`.

## What shipped (commits aae5874..1c787da on the branch)

| Commit | Change |
|---|---|
| `f724c9b`, `2595e6c` | `_load()` → `store.load_dict()`; `_LOAD_SNAPSHOT`/`_backlog_lock` removed; one named `_transaction(tool=…)` per public tool with a `_mutate_and_save` commit latch and rollback on unlatched exit; nested loads identity-stable; auto-link inside the transaction; `backlog_update_task` renders from committed state with a `NOT_PERSISTED` marker |
| `8b74fa9` | Removal audit: no site drops dict members; the real defect was archive-by-status never reaching the store. Nine sites now call `tx.archive`/`tx.unarchive`; `store.active_transaction()` and `Transaction.unarchive()` added |
| `3fe5256`, `b154827` | Autouse projection-bypass guard (`tests/conftest.py`, 27 tests in `tests/test_store_bypass.py`); `_save`, `_ensure_v3_marker`, viewer `create/update/archive_task`, `with_file_lock`, mtime ETags, `write_entity_anywhere` task branch removed or rerouted; viewer ETag `creation_token:max_seq`; `backlog_index_status(rebuild=True)` → `Store.rebuild_derived()`; batch/viewer archived-transition guard |
| `1ba3b76`, `9dd4dc0` | `tests/test_store_concurrency.py`: sequential, thread-pool, two-process, 8×200 stress with per-operation postconditions and row↔file content hashes, contention, linked worktree, viewer ETag, derived rebuild |
| `de528f3` … `1c787da` | Fix wave from the whole-branch and Codex reviews (F1–F12): store pinned to `.taskmaster` with an actionable refusal for legacy layouts; task ids allocated from the transaction's authoritative state (duplicate create raises); viewer `If-Match` evaluated inside the write transaction and GET payload+ETag from one snapshot; task detail derived from the store; every epic/phase archive explicit with cascade; thread reads off the writer lock; idea auto-link inverse in one transaction; `NOT_PERSISTED` sentinel/identity fix; changelog + plan status |

Also fixed along the way (surfaced by wiring, regression-tested): pre-v4 adoption dropped every
task's slim fields (`store.py`), and `hooks/merge_gate_decide.py` fell open on every v4 project.

## Review record

- Per-task reviews (Claude opus) with scoped re-reviews; all findings closed or triaged.
- Whole-branch review (Claude opus): "ready with fixes" → all fixed.
- Codex adversarial read-only review (`.superpowers/sdd/2026-09-04-sqlite-store/codex-review.md`,
  gitignored workspace — copy survives only in this worktree): found the id-allocation
  overwrite (Critical) and nine Important items that four Claude reviews had missed; all closed.
- The SDD ledger with every ruling and deferred minor: `.superpowers/sdd/2026-09-04-sqlite-store/progress.md`
  (gitignored; summarized below).

## Rulings made on the user's behalf (reverse if wrong)

1. v3→v4 adoption on first store read is spec item 9; six "v3 stays v3" tests were updated.
2. Legacy `.claude/` or root-level layouts are **refused** (actionable error) rather than adopted;
   `backlog_canonicalize_layout` is the one migration route and now moves every artifact
   directory. Cost if wrong: legacy-layout users must run one tool before use.
3. Committed-state rendering is generalized in step 3, not step 2 (documented in changelog).
4. Out-of-plan fixes to `store.py` and `hooks/merge_gate_decide.py` were accepted into Step 2.
5. Viewer/batch restore of an archived task is allowed only to `todo`, matching MCP tools.

## Deferred to step 3 (from the ledger)

- Non-task kinds and their id allocation onto the store; remove the compatibility allowlist,
  the `_store_for_read()` read-scan-throttle poke, and the private `_last_read_scan_clock` seam.
- Generalize `_render_after_commit` (currently one call site; nested-frame clobber footgun).
- Context regenerated three times per transaction; `regenerate_progress_dashboard` has no
  production caller (six test monkeypatches are no-ops).
- Two committed shapes for archived state (`archived` timestamp vs. status-only); `put` and
  `archive` are two writers of the archived column.
- Full `LEGAL_STATUS_TRANSITIONS` table applied only for `archived` in batch/viewer paths.
- `_viewer_etag()` is `":0"` on network-filesystem stores; `migrate_v3_to_v4` still reaches
  `save_v4` (shielded by adoption); `_store_tx()` ignores `backlog_path`; dead `entity` param.
- Stress test is ≈6–7 min and about half the default suite runtime; the plan mandates the
  8×200 default in step 2 — consider a slow-marker opt-out in step 3.
- Spurious 409 is possible when the write transaction's own import bumps `max_seq` under a
  dirty projection (retry, not loss). `_threads_data` catches `RuntimeError` broadly to detect
  the projection-only refusal; a typed store exception would be cleaner.
- Store logs many "could not recover export intent" lines under 8-way contention (noise).
- `_load_task_full` keeps a projection-file overlay branch for v3 backlogs whose tasks the store
  owns no rows for (fix-wave F5). Confirm in step 3 that the branch is unreachable on v4 projects.

- From the final re-review: `_ensure_handover_status_backfilled` re-opens `BEGIN IMMEDIATE` on every
  handover list/get for projects backfilled in an earlier run (flag only set when the call owned
  the transaction); `backlog_update_epic(id, "status", "archived")` flips the row flag without
  the task cascade the batch path applies; viewer `_serve_json`/PATCH surface a legacy-layout
  refusal as a 500 carrying the actionable message instead of a 409.

## Working-tree notes

- `uv.lock` in the root checkout and worktree carries the pre-existing `4.5.0 → 5.2.0` version
  drift that `uv run` regenerates; it was never committed by this work.
- A stray untracked directory `UsersgrukuAppDataLocalTemptm-pytest-rr3/` sits at the worktree
  root (a reviewer's mangled `--basetemp`). It is test debris; delete it by hand.
- The worktree `.worktrees/sqlite-store-step-1` is now fully merged and can be removed with the
  guard-hooks safe-worktree-removal procedure (no `--force`).
