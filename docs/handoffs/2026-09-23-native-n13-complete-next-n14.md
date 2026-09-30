<!-- User intent: let a cold session resume the database-native rearchitecture at N14
     without re-deriving N13's state, decisions and working conventions. -->

# Handoff: N13 complete, next is N14 (2026-09-23)

## State

- Branch `feat/database-native-foundation` at `c8c57f0`, pushed to origin. N00–N13
  complete locally; activation of real projects remains gated. Tracking lives only in
  the ledger at the bottom of `docs/plans/2026-09-09-database-native.md` (no backlog).
- N13 evidence: `docs/reports/2026-09-22-native-n13.md`; scope and implementation
  plans: `docs/plans/2026-09-22-n13-scope.md`. Full suite at `6a135fd`:
  **4,049 passed / 4 skipped / 0 failed** (1,082 s, `-n 3`).
- Old N13 worktrees (`.worktrees/native-n13*`, `n13-*`) are merged and can be removed
  safely (use `git worktree remove` without `--force`; see guard-hooks rules).
- Disposable rehearsal copies in `%TEMP%\tmn13\` (CodeMaestro clones, no remotes); the
  `cmf` copy has a stale `.git/index.lock`. Delete the folder when convenient.

## Next: N14 — consumer graph migration and compatibility completion

Plan section `## N14` in the plan doc. Move hook neighborhoods and typed graph queries
to canonical claims/edges with indexed reverse access; keep the exact full
materializer as repair/oracle; decide the public freshness mechanism for `related`
in the compatibility matrix before disabling eager materialization; bounded
recursive dependency traversal; no unannounced top-K/relevance changes.
Exit: native commands pay only for changed relation inputs; supported graph
consumers keep documented freshness and answer semantics.

Start by writing `docs/plans/2026-09-23-n14-scope.md` (goal, ordered steps, files,
acceptance evidence, risks) grounded in the current code — `store.py` related/graph
code, `taskmaster/native/` relations, the edit hook, `backlog_query` guard/views —
and the N02 compatibility inventory. The `related` freshness decision is likely a
user decision: surface it with options before implementing.

## Carried forward (from the N13 ledger row)

- First sync after activation on CodeMaestro ~350 s across stock-budget rounds (N15/N16).
- Managed-Git full-read generation check adds 6–9 s per operation (N16).
- POSIX containment boundary unverified (interrupted ops are acknowledge-gated).
- Crash-left `index.lock` refuses the next managed commit until removed by hand.
- `_`-prefixed frontmatter keys dropped silently on both stores.
- `coordinator stopping` flush and sync's own `publisher busy` reply don't name owed
  PROGRESS debt.

## Working conventions that paid off (keep them)

- One isolated worktree + branch per step; merge `--no-ff` into the milestone branch,
  then the milestone into foundation. Push only with user approval.
- Every step gets an independent adversarial review by a fresh sub-agent; every round
  in N13 found real defects that green suites missed (≈50 total). Fix test-first.
- A copy-only rehearsal on a fresh CodeMaestro clone at stock settings found 8 defects
  the suite could not (scale, real data shapes). Never touch the live CodeMaestro.
- RAM is tight (31 GB, often <8 GB free). Full suite: `-n 3`/`-n 4` with ≥8 GB free;
  focused runs `-n 2`. Multi-process tests belong in
  `@pytest.mark.xdist_group("heavy_processes")`. Claude Code kills background shells
  under memory pressure — don't auto-restart, ask the user to free RAM.
- Codex is out of quota until 2026-09-26 16:51; use a second Claude reviewer meanwhile.
