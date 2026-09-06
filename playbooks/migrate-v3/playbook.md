# Adopt the backlog into the store

Since 6.0.0 there is no v2/v3/v4 file rewrite. `backlog_migrate_v3` (and its alias `backlog_migrate_v4`) moves a legacy `.claude/` or project-root layout into `.taskmaster/`, then opens the SQLite store, which adopts whatever schema it finds and projects it back as v4. The skill keeps its historical name.

This is the ONLY correct way to migrate a project to v3 — do not call backlog_migrate_v3 directly without the pre-flight gate.

## Step 1: Detect current state

Call `backlog_status`; its first line is `**Schema:** v<N>`. If no backlog exists: redirect to `taskmaster:init-taskmaster`. An already-adopted project is not an error — the tool is idempotent and just re-reports the counts — but say so before running it rather than after.

## Step 2: Show pre-flight summary

Gather counts via `backlog_list_tasks` and `backlog_status`. Present: total tasks, active tasks, and what adoption changes on disk — heavy fields land in per-entity files at `.taskmaster/tasks/<id>.md`, the derived `context:` block is dropped from `backlog.yaml`, id-less epics, phases and tasks are given deterministic ids, and `viewer.json`, `auto/`, `snapshots/` and `PROGRESS.md` move under `local/` (moved, never deleted; a taken name gets a `-2` suffix). Warn on a large backlog: adoption rewrites and verifies every projection file once, which takes minutes on a few thousand files. For the field-by-field breakdown: `references/v2-vs-v3.md`.

## Step 3: Confirm opt-in (confirm with the user — MANDATORY)

Ask the user (use your structured-question tool if available; otherwise present the options):

- "Adopt this backlog into the store?" — options:
  - "Adopt": Run backlog_migrate_v3 now. Heavy fields move to per-entity files. Idempotent.
  - "Show diff first": Stop and let me inspect the v2 backlog before migrating.
  - "Cancel": Don't migrate. I'll think about it.

<!-- cc-only:start -->
On Claude Code:

```
AskUserQuestion({
  questions: [{
    question: "Adopt this backlog into the store?",
    header: "Confirm adoption",
    multiSelect: false,
    options: [
      { label: "Adopt", description: "Run backlog_migrate_v3 now. Heavy fields move to per-entity files. Idempotent." },
      { label: "Show diff first", description: "Stop and let me inspect the backlog before adopting" },
      { label: "Cancel", description: "Don't migrate. I'll think about it." }
    ]
  }]
})
```
<!-- cc-only:end -->

"Show diff first" -> stop; tell user to open `.taskmaster/backlog.yaml` and re-invoke when ready. "Cancel" -> stop.

## Step 4: Run the adoption

Call `backlog_migrate_v3()`. Surface the response verbatim. A success starts `Adopted into the store (backlog_migrate_v3).` and names the store path, the store schema version and max seq, and the row counts per kind; it may add lines for canonicalized files, quarantined or dirty files, and a filesystem warning. Anything starting `Error:` — no backlog, several `backlog.yaml` files, or a canonical layout that already holds different content — is surfaced as-is; stop there. So is `adoption refused: …`, which means a rendered file did not read back as what it was rendered from. It names the entity, nothing on disk was changed, and the empty database the open created is removed. Fix that entity, then re-run; never retry blind.

## Verifying writes

A mutating result ending in `[seq N]` is committed; `(export pending: <file> — retried on next call)` means the row committed and only the file export is being retried. `backlog_store_status` shows dirty and quarantined files and the live sessions the store is tracking.

## Steps 5-7

Full detail for steps 5-7 (viewer refresh, layout canonicalize, v3 surface tour) in `references/migration-steps.md`.
