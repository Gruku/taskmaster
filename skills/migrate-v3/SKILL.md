---
name: migrate-v3
description: "Guided v2 → v3 backlog migration. Invoke when the user says 'upgrade to v3', 'migrate to v3', 'switch to v3', 'enable handovers and lessons', 'enable narrative continuity', 'turn on auto-mode', 'I want recap', or when they're on a v2 backlog and ask about v3 features. Shows pre-flight summary (task count, what moves), explains the schema break (heavy fields move from backlog.yaml to tasks/<id>.md), confirms opt-in via AskUserQuestion, runs backlog_migrate_v3, then handles post-flight: gitignore additions for .taskmaster/snapshots/ and .taskmaster/auto/, plus a tour of newly-available v3 surfaces. This is the only correct way to migrate a project to v3 — do not call backlog_migrate_v3 directly without the pre-flight gate."
---

# Migrate to v3

v3 is an opt-in schema upgrade that restructures backlog storage to support narrative continuity across sessions. Where v2 keeps everything in a single `backlog.yaml`, v3 introduces a slim index (task metadata only) plus per-task files that hold heavy content (`description`, `notes`, `docs`, `review_instructions`). The upgrade also unlocks new capabilities: handovers for session continuity, lessons for compounding patterns, issues for bug tracking, recap for cross-session diffs, and auto-mode for state-machine-driven task execution. If any of those sound useful, migrate.

This is the ONLY correct way to migrate a project to v3 — do not call backlog_migrate_v3 directly without the pre-flight gate.

## Step 1: Detect current schema

Call `backlog_status` to read the current project state.

- If the output shows `schema_version: 3` or mentions per-task files, the backlog is already on v3. Tell the user: "You're already on v3 — nothing to migrate." Stop here.
- If `backlog_status` returns an error indicating no backlog exists, redirect to `taskmaster:init-taskmaster` instead of continuing.
- Otherwise, proceed with the pre-flight summary in Step 2.

## Step 2: Show pre-flight summary

Call `backlog_list_tasks` and `backlog_status` to gather counts. Synthesize and present:

**Migration summary:**
- Total tasks: N
- Active tasks (in-progress or in-review): N — these are mid-flight; migration is safe, but the user should know
- Heavy fields moving out of `backlog.yaml`: `description`, `notes`, `docs`, `review_instructions` — for any task that has these populated, the content moves into a per-task file at `.taskmaster/tasks/<task-id>.md`. The index retains only slim metadata.
- If this is a legacy `.claude/`-layout project, the migrator writes back into `.claude/`; consider running `backlog_canonicalize_layout` afterwards to consolidate everything under `.taskmaster/`.
- No data is lost. The in-memory shape is identical on v2 and v3 — existing tools and skills continue to work without changes.
- Reversibility: the migration is idempotent — running it again is a no-op. If you want to roll back, `git restore` your `backlog.yaml` and delete the new `tasks/` directory.

For a full field-by-field breakdown of what moves and what stays, see [`references/v2-vs-v3.md`](references/v2-vs-v3.md).

## Step 3: Confirm opt-in (AskUserQuestion — MANDATORY)

Do NOT call `backlog_migrate_v3` before receiving explicit confirmation. Present the choice:

```
AskUserQuestion({
  questions: [
    {
      question: "Migrate this backlog to v3?",
      header: "Confirm migration",
      multiSelect: false,
      options: [
        { label: "Migrate", description: "Run backlog_migrate_v3 now. Heavy fields move to per-task files; index slim. Idempotent." },
        { label: "Show diff first", description: "Stop and let me inspect the v2 backlog before migrating" },
        { label: "Cancel", description: "Don't migrate. I'll think about it." }
      ]
    }
  ]
})
```

- **"Migrate"** → proceed to Step 4.
- **"Show diff first"** → stop. Tell the user: "Open `.taskmaster/backlog.yaml` to review the current content (or `.claude/backlog.yaml` for legacy-layout projects). When you're ready, re-invoke `taskmaster:migrate-v3` to proceed."
- **"Cancel"** → stop. Tell the user they can run `taskmaster:migrate-v3` later whenever they're ready.

## Step 4: Run the migration

Call `backlog_migrate_v3()`. Surface the response verbatim — do not paraphrase the per-file count or rewrite the output.

If the tool returns an error, surface it as-is and stop. Do not retry automatically.

## Step 5: Verify the viewer flipped to v3 (and tell the user to refresh)

`backlog_migrate_v3` flips `use_v3: true` internally as part of the migration, but its inner flip is wrapped in a `try/except: pass` that silently swallows errors. Verify it actually took, and prompt the user to refresh their viewer tab so they see the new shell.

1. Call `viewer_prefs_get` and parse the JSON response.
2. If `use_v3` is `true`: tell the user "Viewer is on the v3 shell — **hard-refresh any open viewer tab** (Ctrl+Shift+R / Cmd+Shift+R) to load it. The viewer reads `use_v3` per-request, but the browser may have cached the v2 page."
3. If `use_v3` is `false` or missing: the inner flip silently failed. Call `viewer_prefs_set('{"use_v3": true}')` explicitly. If it returns anything other than `ok`, surface the error and tell the user the schema migrated cleanly but the viewer toggle needs manual fixing — they can call `viewer_prefs_set('{"use_v3": true}')` themselves later. Continue with the remaining steps either way.

## Step 6: Offer to canonicalize layout (legacy `.claude/` projects only)

If the project's backlog lives at `.claude/backlog.yaml` rather than `.taskmaster/backlog.yaml`, the migration completed in-place but artifacts (tasks, handovers, lessons, issues, snapshots, auto state) are now scattered under `.claude/`. v3 conventions, gitignore guidance, and most documentation assume `.taskmaster/`. Offer to consolidate.

Detect by checking whether `.claude/backlog.yaml` exists in the project root and `.taskmaster/backlog.yaml` does not. If so, ask:

```
AskUserQuestion({
  questions: [
    {
      question: "Consolidate v3 artifacts under .taskmaster/ instead of .claude/?",
      header: "Canonicalize",
      multiSelect: false,
      options: [
        { label: "Yes, canonicalize", description: "Run backlog_canonicalize_layout — moves backlog.yaml + artifact subdirs into .taskmaster/. Idempotent; refuses to clobber." },
        { label: "Show plan first", description: "Run backlog_canonicalize_layout(dry_run=true) so I can see exactly what would move" },
        { label: "Skip", description: "Leave artifacts under .claude/. I can canonicalize later." }
      ]
    }
  ]
})
```

- **"Yes, canonicalize"** → call `backlog_canonicalize_layout()`. Surface the response verbatim.
- **"Show plan first"** → call `backlog_canonicalize_layout(dry_run=true)` and surface the plan. Then re-ask whether to proceed for real.
- **"Skip"** → acknowledge and move on.

If `.taskmaster/backlog.yaml` already exists (already canonical), skip this step entirely without asking.

## Step 7: Seed the recap baseline

`backlog_recap` diffs the current backlog against the last snapshot in `<artifact_root>/snapshots/last.json`. Until one exists, recap output won't be useful. Seed it now by calling `backlog_snapshot()`. Surface its one-line response. No confirmation question — this is harmless and unblocks recap immediately.

## Step 8: Post-flight — gitignore

After a successful migration, check whether `.gitignore` in the project root contains entries for `.taskmaster/snapshots/` and `.taskmaster/auto/`. These directories hold runtime state (snapshot diffs, auto-mode cursor) that should not be committed.

If either entry is missing, ask:

```
AskUserQuestion({
  questions: [
    {
      question: "Add runtime directories to .gitignore?",
      header: "Gitignore",
      multiSelect: false,
      options: [
        { label: "Yes, add them", description: "Append .taskmaster/snapshots/ and .taskmaster/auto/ to .gitignore" },
        { label: "Skip", description: "I'll handle .gitignore myself" }
      ]
    }
  ]
})
```

If the user selects "Yes, add them": append the following block to `.gitignore` (do not overwrite; append only; check first that neither line is already present):

```
# v3 taskmaster runtime
.taskmaster/snapshots/
.taskmaster/auto/
```

If the user selects "Skip": acknowledge and move on.

## Step 9: Tour the v3 surfaces

Tell the user what they just unlocked:

> **You're on v3. New capabilities available:**
> - **Handovers** — `taskmaster:handover` skill captures session continuity. Auto-offered at end-session for heavy sessions.
> - **Lessons** — `taskmaster:lesson` skill records patterns, anti-patterns, and gotchas; reinforces and compounds across sessions.
> - **Issues** — `taskmaster:issue` skill for bug tracking separate from work tasks.
> - **Recap** — `backlog_recap` shows what changed in the project since the last snapshot.
> - **Auto modes** — `taskmaster:auto-task`, `auto-epic`, `auto-phase` for state-machine-driven execution.
>
> The PreCompact hook ships with this plugin and runs automatically before context compaction (writes `.taskmaster/snapshots/last.json` so the next `recap` reflects pre-compaction state). No per-project setup required.

For the latest handover: `backlog_handover_latest`. For the full surface list, see `init-taskmaster/SKILL.md` lines 149–160.

## When to invoke

Invoke `taskmaster:migrate-v3` when the user says any of:

- "upgrade to v3"
- "migrate to v3"
- "switch to v3"
- "enable handovers and lessons"
- "enable narrative continuity"
- "turn on auto-mode"
- "I want recap"

Also invoke when the user is on a v2 backlog and asks about v3 features (handovers, lessons, issues, recap, auto-mode) — they are implicitly asking how to get access to those things.

## When NOT to invoke

- **Already on v3** — `backlog_status` will report `schema_version: 3`. Step 1 detects this and stops.
- **No backlog yet** — redirect to `taskmaster:init-taskmaster`. The init skill has its own v3 option and runs `backlog_migrate_v3` on a fresh backlog if the user selects v3.
- **User on v2 and content with current capabilities** — do not push v3 unprompted. Only migrate when the user signals interest in v3 features or explicitly requests the migration.
