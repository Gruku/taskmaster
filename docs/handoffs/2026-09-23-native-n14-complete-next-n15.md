<!-- User intent: let a cold session finish N14's last gate (full suite, merge into foundation)
     and start N15 without re-deriving N14's state, decisions and conventions. -->

# Handoff: N14 complete locally; next is N15 (2026-09-23)

## State

- N14 is on `feat/native-n14` (worktree `.worktrees/n14`) at `5629e5e`. It is **not yet merged**
  into `feat/database-native-foundation` (at `43acef1`: scope plus the F1 decision). Nothing is
  pushed.
- Evidence is in `docs/reports/2026-09-23-native-n14.md`, and the ledger row is updated on the N14
  branch.
- **Open gate:** the full suite (`-n 3`, ≥8 GB free). The session had about 5 GB. Run it
  in `.worktrees/n14`. If it is green, merge `feat/native-n14` into foundation with
  `--no-ff`, record the count in the ledger and report, and push only with approval.
- Step worktrees `.worktrees/n14-paths`, `n14-deps` and `n14-compat` are merged and can be
  removed (`git worktree remove` without `--force`).
- Rehearsal copies are in `%TEMP%\tmn14\` (`cm`, `cml`, `cm2`, `cml2` plus logs). They are
  disposable, and the old `%TEMP%\tmn13\` can go too.

## Decisions made

- **F1 = A (user):** the graph SQL tables stay current at commit, maintained incrementally,
  with the full oracle as an explicit repair.
- **Orchestrator calls:**
  - Links written before their target are fixed on **both** stores. This is a visible bug
    fix in `links` rows, recorded in the N02 spec.
  - The graph repair runs at activation only, never in a repeatable backfill.
  - `depth` is a strict integer. Archived tasks are followed; missing and deleted ids show as
    `[missing]`.

## Next: N15 (migration and downgrade rehearsal)

N15 must include `migrate.repair_graph_for_activation` in the production activation path,
inside the authority-switch transaction. On CodeMaestro that repair fixes 4,610 historical
`links` rows.

## Carried forward

- D1: legacy `backlog_link create` reports success but doesn't save on the CodeMaestro copy.
  It predates N14 (reproduced at `43acef1`). Repro: `tmn14\repro_link.py`.
- `backlog_link` refuses unprefixed task slugs. CodeMaestro's 65 issues lack `evidence`, so
  `backlog_issue_update` refuses all of them.
- Canonical `target_kind` keeps the `task` fallback. `Snapshot.neighbourhood` has no cursor.
- Everything carried forward in the N13 handoff still stands.

## Conventions (unchanged, they paid off again)

- Use one worktree per step.
- Work test-first.
- Give every round a fresh adversarial reviewer. N14 had six review rounds, and each one
  found real defects.
- Run a copy-only rehearsal before acceptance, and never touch the live CodeMaestro.
- Codex is out of quota until 2026-09-26 16:51.
