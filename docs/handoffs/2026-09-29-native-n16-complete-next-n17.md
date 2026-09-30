<!-- User intent: let a cold session start N17 without re-deriving N16's state, decisions,
     carry-forwards, live-CodeMaestro specifics and leftover scratch. -->

# Handoff: N16 complete locally, next is N17 (2026-09-29)

## State

- `feat/native-n16` (`40512e8`, plus the uncommitted N16 report, ledger row and this handoff) is
  about to merge into `feat/database-native-foundation`. **Nothing is pushed.** The foundation branch
  is already 32 commits ahead of `origin/feat/database-native-foundation`, and more after this
  merge.
- Evidence: `docs/reports/2026-09-29-native-n16.md`. The ledger row is in
  `docs/plans/2026-09-09-database-native.md`.
- Status: **complete locally; activation gated. The exit is met on the correctness gates; the 10×
  acceptance was not run (user decision).**
  - Full suite at `40512e8`: 4,617 passed / 2 skipped. At `fb25737`: 4,550 / 4 skipped.
  - `-m scale` passed at `fb25737`.
  - Final small acceptance at `40512e8`: 106/106 by verdict.
- No live project has been migrated. The cutover has been run only on disposable copies. **Live
  activation of any project is a release decision** that needs its own authorized step.

## Decisions (user, this milestone)

- **Quarantine: preflight and repair.** The cutover refuses while any projection file is quarantined
  or flagged, re-checks under the fence, and always runs a reconcile scan. The runbook carries the
  repair recipe.
- **Batched full sync.** There is no project-size ceiling: one sync operation scanned in batches of
  10,000 paths. 37,010 files sync in 25–34 s, with no 1× regression.
- **10× acceptance skipped.** The final run was stopped during 10× dataset generation (legacy
  adoption takes about 87 min). The 10× latencies and the required-output vs unrelated-data scaling
  check are unmeasured. The only 10× evidence is the batched-sync measurement.

## Next: N17 (documentation, release candidate and rollout boundary)

From the plan:

- Document commit vs projection receipts, sync and Git obligations, migration requirements, service
  recovery, context/delta usage and compatibility support.
- Test the packaged launch paths and version alignment.
- Choose the release version after reviewing the compatibility impact. Do not preassign a patch
  version to a behavioural/schema migration.
- Prepare a concrete release candidate and a copied-project demonstration.
- Publishing, installation and live CodeMaestro activation each need their own authorized step.
- Release evidence must keep local tests, the packaged runtime and an actual project migration
  apart. Do not mark the rollout complete on a local green suite.

**Runbook gaps to close** (found in the N16 CodeMaestro rehearsal):

- A stale quarantine clears only after the file is touched (B-339). The scan skips a file whose
  mtime/size stamp is unchanged.
- The repair must keep the file's own line endings (CodeMaestro's files are CRLF).
- The repair does not refresh `backlog.yaml`: it shows `M` before the first managed commit. Say so,
  or regenerate it.

**Open follow-ups from N16** (decide what N17 fixes and what it documents as a known limit):

- The harness's first sync and managed commit on a fresh copy take 40–88 s. Hypothesis, unverified:
  the copy gives every file a new ChangeTime, so the fingerprint cache misses on every file, which
  is what a user sees after a fresh clone. Open question: does a cold 37,010-file sync fit the
  120 s budget?
- Repeat conflict `sync.apply` events on every full sync (pre-existing).
- A racing `backlog_link` create answers "linked" with no seq and no no-op marker.
- An identical-value update commits or not depending on the wall-clock minute (`last_referenced`).
- Linked-worktree sync loads all published bytes at once (not batched).
- `generation()` does a full read: 6.9–7.6 s per managed Git operation on CodeMaestro.
- The CodeMaestro full viewer read is 164 ms p95 (budget 100 ms).
- Write p95 misses the budget at 8–12 clients (single writer, `synchronous=FULL`). Durability was
  not relaxed.
- 10× legacy adoption takes about 87 min and grows roughly quadratically.
- Carry-overs from N15: 6.0.2 pre-bridge fails uncleanly (stop list), `scan_processes` false
  positives on Windows, rollback compares rows only before the first backup, legacy
  `backlog_link create` (D1), and the `target_kind` fallback.

## Live CodeMaestro (`C:\Users\gruku\Files\Work\CodeMaestro`): before any authorized cutover

- The cutover **will refuse** until 4 quarantined files are fixed:
  - **3 headerless archived handovers** under `.taskmaster/handovers/_archive/2026/`:
    - `2026-06-12-taskmaster-notes-grounded-handover.md`
    - `2026-06-14-mock-grounded-playable-chain-plans-ready.md`
    - `2026-06-15-build-glass-shipped-slide-up-orchestrator-driven.md`

    Repair each with the runbook's minimal frontmatter above the unchanged text, in CRLF.
    `%TEMP%\tmn16\cm2\repair.py` holds the values used on the copy.
  - **`bugs/B-339.md`:** touch it so the scan re-adopts it.
- Then re-adopt until `backlog_store_status` reports `Quarantined: 0`, and expect `backlog.yaml` to
  change.
- On the copy this took the cutover to ~30.6 s by the journal (32.9 s wall), with 3,709 bases
  seeded, a 4.65 s first sync and an 11.5 s managed commit.
- Other facts to expect:
  - 4 orphan tasks (no epic or status) that every tool answers "not found" for;
  - 4,610 historical `links` differences repaired at activation.

## Leftover scratch (disposable; remove safely)

- **`%TEMP%\tmn16\`:** rehearsal copies, acceptance runs and the A/B. Keep `e\` and `cm2\` logs
  until the report is merged, if wanted.
- **Git worktrees `%TEMP%\tmn16\ab\old` (`4867c39`) and `%TEMP%\tmn16\ab\new` (`3f646c2`):** remove
  with `git worktree remove <path>` **without `--force`** before deleting `%TEMP%\tmn16`.
- **Branch `rr/n16-trial-merge`:** a trial merge. It is **not** an ancestor of `feat/native-n16`, so
  `git branch -d` will refuse. Confirm it holds nothing needed, then ask the user before using `-D`.
- **Track worktrees:** `.worktrees/n16-batchsync`, `n16-harness`, `n16-instr`, `n16-linkplan`,
  `n16-perf`, `n16-sync`, `n16-syncjudge` and `n16-wording`, plus
  `.claude/worktrees/agent-a52791b1f87169618` (`feat/native-n16-preflight`). All are merged into
  `feat/native-n16`. Remove them with `git worktree remove` (no `--force`), then delete the
  branches with `git branch -d`.
- `test-results/` in `.worktrees/n16` (git-ignored) holds the full-suite and acceptance logs cited
  by the report.

## Conventions (keep)

- **Worktrees:** one per track, test-first, with a fresh adversarial reviewer each round.
- **Harness changes get planted-fault tests.** In N16 every "product" failure found by the harness
  was a harness artifact until proven otherwise.
- **Heavy runs one at a time, in the foreground or logged to a file.** The `-m scale` output was
  lost because it was not saved.
- **Copy-only on CodeMaestro.** Never touch the live checkout without authorization.
