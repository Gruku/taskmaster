<!-- User intent: let a cold session start N16 without re-deriving N15's state, decisions,
     carry-forwards and working conventions. -->

# Handoff: N15 complete locally, next is N16 (2026-09-24)

## State

- N15 is merged into `feat/database-native-foundation`. Evidence is in
  `docs/reports/2026-09-24-native-n15.md`, and the ledger row is complete. Nothing is pushed
  beyond what the user approved.
- The new production cutover, `python -m taskmaster.native.cutover`, has been exercised only on
  disposable copies. **Live activation of any project is a release decision** (N17 and later).
- Worktrees `.worktrees/n15`, `n15-cutover`, `n15-oracle` and `n15-rehearsal` are merged and can
  be removed (`git worktree remove` without `--force`). Rehearsal copies are in `%TEMP%\tmn15\`,
  about 10 GB, and are disposable.

## Decisions (user)

- **M1 = A:** no reverse migration. Rollback is exact before activation; after it the only way
  is forward, with the documented escape hatch.
- **Rollback restores nothing.** It succeeds only when nothing leaked through the fence;
  otherwise it refuses and offers three choices: resume, `--clear-orphan-fence`, or the manual
  restore.
- **Drift-absorption cap:** 3 per invocation (orchestrator call; follows "never trap an
  acknowledged write").

## Next: N16 (scale, durability and end-to-end acceptance)

Start with `docs/plans/2026-09-24-n16-scope.md`, grounded in the plan's N16 matrix and these
carry-forwards:

- First sync after activation takes **310–492 s** on CodeMaestro, in rounds that end `pending`
  with "import outcome uncertain" (the second sync takes about 1 s).
- Backup on CodeMaestro takes 28–53 s, mostly the projection archive.
- From N13: the managed-Git full-read generation check adds 6–9 s per operation.
- From N11: the native dashboard full read is 411 vs 120 ms; every export hashes the file (no
  mtime/size short-circuit); more commits per export.
- From N09: `budget.budget` is quadratic.
- From N10: large-board native apply is slower.
- The FTS long-lived-connection diagnostic needs a supported, explained outcome.
- Design §11 budgets: bounded reads p95 < 100 ms, DB command core < 50 ms, simple writes
  < 250 ms. Deterministic work assertions come before microsecond targets.

## Conventions (keep)

- **Worktrees:** one per step or track. Parallel tracks with a fixed interface worked well.
- **Testing:** test-first. Every round gets a fresh adversarial reviewer (N15 needed six). When
  patches stop converging, ask the user whether to simplify rather than keep patching.
- **Rehearsal:** copy-only on CodeMaestro before acceptance; never touch the live checkout.
- **Heavy runs one at a time.** The full suite (about 32 min at `-n 3`) and a rehearsal together
  exhaust memory, and Claude Code kills background shells.
