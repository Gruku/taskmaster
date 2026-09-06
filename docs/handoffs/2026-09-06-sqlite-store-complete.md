<!-- User intent: let a fresh session (or the user reading this on a phone) know exactly where the
     SQLite-store migration stands after steps 3–5, which decisions were taken on the user's behalf,
     and what is deliberately left open — so nothing has to be re-derived from 140 changed files. -->

# Handover — SQLite store steps 3–5 implemented; 6.0.0 ready for the user's review

**Date:** 2026-09-06
**Session kind:** milestone (steps 3, 4, 5 of the SQLite-store spec implemented, reviewed, merged locally)
**Thread:** sqlite-store
**Branches → master (all `--no-ff`, none pushed):**

| Merge | SHA | Content |
|---|---|---|
| step-3 | `a224ba9` | every entity kind, Linear queue, PROGRESS.md and `linear.yaml` on the store; hot-path row API; renderer stack; `[seq N]`; `backlog_store_status`; hooks (4.3) and scripts (5.1) |
| step-4a | `63edf10` | `index.py` absorbed; `taskmaster/paths.py`; `backlog_query`/search on the store |
| step-4b | `35f1204` | viewer GETs on committed rows; migrate tools adopt through the store; viewer gates |
| step-4fix | `e80f09f` | step-4 review fixes + real-backlog adoption fixes (V1–V6) |
| step-5b | `4457329` | 6.0.0 docs, playbooks, changelog, version |
| step-5c | (this merge) | docs touch-up for the fix-wave behaviours, adoption timing, plan status, this handoff |

**Push status:** nothing has left the machine. Version is `6.0.0` in `pyproject.toml` and both plugin manifests.

## Resume prompt

> Open `C:\Users\gruku\Files\Claude\taskmaster` on `master`. Read the "Execution status — 2026-09-06 (steps 4–5)" section at the end of `docs/plans/2026-09-04-sqlite-store.md` and the `6.0.0` CHANGELOG entry. Decide whether to push (nothing is pushed) and whether to open the live CodeMaestro checkout (`C:\Users\gruku\Files\Work\CodeMaestro`) — adoption there rewrites ~2,300 tracked files and blocks for minutes on first open; do it on a clean git state. File the follow-up bugs listed below. Worktrees under `.worktrees/` and `C:\Users\gruku\AppData\Local\Temp\...\scratchpad\codemaestro-copy*` are debris; remove them with the guard-hooks safe procedure only.

## Gate results

- Final full suite on master `4457329`: **2,247 passed, 1 skipped, 0 failed** in 20m39s (default 8×200 stress profile included).
- Final real-backlog run (5.3, on a scratchpad COPY of CodeMaestro's `.taskmaster`, 3,391 files): **PASS**.

  | Measure | Result |
  |---|---|
  | Cold adoption (first `backlog_status()`) | 363.9 s (was 458.2 s before verification was added; reproduced 364.2 s) |
  | Second cold open after deleting `local/store.db*` | 98.0 s, succeeds (first pass died with ValueError) |
  | Third cold open | 94.3 s, zero files changed (byte-stable) |
  | Rows | 3,445 = task 2,301, bug 462, handover 363, epic 156, issue 65, decision 35, idea 26, phase 18, note 17, backlog+project 2 |
  | Projection | 3,445 files, dirty 0, quarantined 4 (pre-existing damage: one conflict-marker bug, three bad-frontmatter archived handovers) |
  | Warm reads | status 2.3 s, list_tasks 1.1 s, get_task 0.5 s, bug_list 0.3 s, handover_list 1.3 s, store_status 0.1 s, query 0.2 s |
  | One `backlog_update_task` | 8.3 s, `[seq 3446]`, changed exactly that task's file plus `local/PROGRESS.md`, no `created` sentinel anywhere |
  | Adoption diff | 1,956 modified / 274 deleted (archive moves) / 90 new; every hunk real, zero whole-file line-ending diffs (was 169); `PROGRESS.md` and `snapshots/` moved under `local/` |
  | Hooks | 0.10–0.14 s; resurface emits then dedupes; merge gate ALLOW (policy off) |

  Notes only: one-time `meta` key reorder in backlog.yaml on first re-import (4 lines, converges); adoption blocks ~6 min with no progress output; the 4 quarantine reasons are re-logged on every warm tool call (store.log grows; implies a per-call re-scan of quarantined files); files created or archive-moved during adoption are written LF while in-place rewrites keep CRLF, so `tasks/` ends mixed.
- Reviews: five step-3 task reviews, three step-4 task reviews, two docs reviews, two whole-step reviews, two Codex adversarial reviews, two fix waves (step 3: 16 findings; step 4: 19 findings + V1–V6 from the real backlog, two rounds), one final whole-branch review ("Ready with fixes" — gate conditions only). Every finding is either fixed with a red test or listed below.

## What 6.0.0 changes (short form; the CHANGELOG entry has the citations)

Every kind writes through the store with ids allocated in the creating transaction; every mutating tool ends in `[seq N]` (or a `seq` key); `backlog_store_status` is the read-only diagnostic; `index.py`/`index.db` are gone; the Linear queue lives in the store; `IDEAS.md` and the PROGRESS.md session log are derived; hooks read the store in query-only mode via `taskmaster/root.py`; the viewer serves committed rows under one ETag and applies the full transition table plus the completion gate; adoption assigns deterministic ids to id-less entities, moves `snapshots/` and `PROGRESS.md` under `local/`, preserves CRLF, and verifies every rendered file round-trips before committing (refusing otherwise and removing the empty db it created); scripts write through the store (`migrate_handover_statuses.py` now takes `--root`).

## Rulings made on the user's behalf (reverse if wrong)

Plan rulings R1–R12 are in the plan. Session rulings, in order:

1. Sections 3.x/4.x/5.x were the SDD tasks; independent sections ran in parallel worktrees (3.1‖3.2, 3.4‖3.5, 4.3 and 5.1 ahead of their steps, 4.1‖4.2). Cost if wrong: merge conflicts (none occurred; one predicted integration break, 5a/C1, was caught by the post-merge suite).
2. `backlog_update_epic(status=archived)` refusing and pointing at `backlog_archive_epic` satisfies the step-2 cascade item.
3. `_ensure_handover_status_backfilled`: read-first durable-marker check; write transaction only when needed; in-process flag only from committed state.
4. `backlog_migrate_v3/_v4` parked from 3.3 to 4.2 (now canonicalize + adopt through the store).
5. `backlog_store_status` never bootstraps or repairs a store — reports "no store yet" / corruption instead.
6. The session changelog paragraph is a committed row; PROGRESS.md renders an idempotent marker-delimited region from a bounded (200) applied log. Cost if wrong: on an existing project old and new changelog entries sit in two blocks.
7. `[seq N]` is appended by `_transactional` to every latched tool; JSON-returning tools get a `seq` key.
8. The IDEAS.md index regenerates on idea writes and on first import only — a hand edit to an existing `IDEA-*.md` is picked up on the next idea write (per-import regeneration starved the writer lock under 8 processes). Known limitation.
9. The Linear queue state machine is pending/claimed/done/failed with a recoverable lease; attempts count failures only; permanent legacy failures import as `failed`.
10. Root rule lives in stdlib-only `taskmaster/root.py`; the non-git branch walks up to the nearest `.taskmaster/` (server and hooks alike). Cost if wrong: a non-git server started in a subdirectory now finds the parent backlog.
11. Hooks open `store.db` with `mode=rw` + `PRAGMA query_only` (a `mode=ro` URI fails at the first statement on WAL without `-shm`); `merge_gate_decide` fails OPEN when a store exists but is unreadable and falls back to the projection only when the store is absent; `merge_recorder_stamp` never bootstraps a store (no store → no stamp, logged) and waits 30 s for the writer.
12. Id-less epics/phases/tasks get deterministic ids on adoption (kebab of name, suffixed; `<epic>-NNN` from the same allocation surface as `allocate_id`); an epic that exists only as `epics/<id>.md` keeps its stem as id.
13. The viewer applies `_completion_block_reason` (409) and rejects `status: null`.
14. Adoption verifies every rendered file (entities, backlog.yaml, IDEAS.md) round-trips in memory before COMMIT, else rollback + refuse + remove the empty db.
15. The exporter preserves an existing file's CRLF style; new files are LF.
16. Runtime-backfilled fields (the `created` sentinel) are never persisted.
17. Codex reviews were run for steps 3 and 4 (destructive-path rule); they found 16 Important defects the Claude reviews missed.
18. CodeMaestro was verified on a COPY only; the live checkout was never opened.

## Follow-up bugs to file (not fixed, all triaged "follow-up" by the final review)

- `scripts/migrate_links.py` drops `depends_on` by default while the server reads it in ~12 places (pre-existing; pinned by `test_links_smoke`).
- Linear: an explicit retry can requeue a row a drain has claimed (double push); the queue quarantine is not shown on the status "Corrupt" line; `done` rows have no retention; the enqueue hook swallows exceptions silently.
- Stress test: the bidirectional row↔file check walks file→row for four directories only; no mid-scan-checkout test.
- `_projection_identity` stats every entity file per read on network roots (twice since X8); `backlog_query` runs on the read/write connection with the authorizer as the only write barrier.
- Test-only: the bypass guard intercepts `Path.open` but not builtin `open`; `_said()` strips only `[seq N]`; two timing/urlopen flakes under load.
- Cosmetic: cold reopen reorders `meta` keys; `force_scan_on_next_read` has no production caller; three dead writers remain in `taskmaster_v3.py` (`atomic_write`, `write_task_file`, viewer-prefs writer).
- Performance: first adoption of a 2,300-file backlog is export-bound (~6 min; per-file fsync + round-trip verification + CRLF probe) and shows no progress output. Not optimised by ruling.
- From the final 5.3 run: quarantine reasons re-logged on every warm call (store.log unbounded); new/archive-moved files written LF next to CRLF in-place rewrites (mixed endings in `tasks/`).

## Working-tree notes

- `uv.lock` in the root checkout carries the pre-existing uncommitted drift that `uv run` regenerates; never committed by this work.
- The final reviewer's `uv run --frozen` attempt resynced `.venv` once; the suites run on the system interpreter and were unaffected.
- Worktrees to remove (safe procedure, no `--force`): `.worktrees/sqlite-store-step-1`, `-2`, `-3`, `-3b`, `-3c`, `-4a`, `-4b`, `-4c`, `-4fix`, `-5a`, `-5b`, `-5c`, plus the stray `UsersgrukuAppDataLocalTemptm-pytest-rr3/` directory inside `.worktrees/sqlite-store-step-2`.
- The SDD ledger, briefs, reports and reviews live in `.worktrees/sqlite-store-step-3/.superpowers/sdd/2026-09-05-sqlite-store-steps-3-5/` (untracked). Copy anything worth keeping before removing that worktree; the handoff above summarises it.
