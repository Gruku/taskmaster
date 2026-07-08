# Migrate to v3

v3 is an opt-in schema upgrade: slim index + per-task files. Unlocks handovers, issues, and recap.

This is the ONLY correct way to migrate a project to v3 — do not call backlog_migrate_v3 directly without the pre-flight gate.

## Step 1: Detect current schema

Call `backlog_status`. If `schema_version: 3` or per-task files mentioned: "You're already on v3 — nothing to migrate." Stop. If no backlog exists: redirect to `taskmaster:init-taskmaster`.

## Step 2: Show pre-flight summary

Gather counts via `backlog_list_tasks` and `backlog_status`. Present: total tasks, active tasks, heavy fields moving out of `backlog.yaml` (description, notes, docs, review_instructions -> per-task files at `.taskmaster/tasks/<id>.md`). For full field-by-field breakdown: `references/v2-vs-v3.md`.

## Step 3: Confirm opt-in (confirm with the user — MANDATORY)

Ask the user (use your structured-question tool if available; otherwise present the options):

- "Migrate this backlog to v3?" — options:
  - "Migrate": Run backlog_migrate_v3 now. Heavy fields move to per-task files. Idempotent.
  - "Show diff first": Stop and let me inspect the v2 backlog before migrating.
  - "Cancel": Don't migrate. I'll think about it.

<!-- cc-only:start -->
On Claude Code:

```
AskUserQuestion({
  questions: [{
    question: "Migrate this backlog to v3?",
    header: "Confirm migration",
    multiSelect: false,
    options: [
      { label: "Migrate", description: "Run backlog_migrate_v3 now. Heavy fields move to per-task files. Idempotent." },
      { label: "Show diff first", description: "Stop and let me inspect the v2 backlog before migrating" },
      { label: "Cancel", description: "Don't migrate. I'll think about it." }
    ]
  }]
})
```
<!-- cc-only:end -->

"Show diff first" -> stop; tell user to open `.taskmaster/backlog.yaml` and re-invoke when ready. "Cancel" -> stop.

## Step 4: Run the migration

Call `backlog_migrate_v3()`. Surface the response verbatim. If error: surface as-is, stop.

## Steps 5-9

Full detail for steps 5-9 (viewer flip verification, layout canonicalize, recap baseline seed, gitignore, v3 surface tour) in `references/migration-steps.md`.
