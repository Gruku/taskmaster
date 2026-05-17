---
name: pick-task
description: "Select a task to work on. Invoke when the user says 'pick a task', 'let's work on X', 'start task auth-003', 'what should I tackle next', 'continue this task', 'continue where we left off', 'resume the work', or names a specific task ID. Sets status to in-progress, checks dependencies, creates a git worktree, and loads task context."
---

# Pick Task

Select a task to work on and set it to in-progress. Default mode is a **glance briefing** (~600–800 tokens). Append `--deep` for full task body, lesson bodies, blast radius, and handover context.

## Arguments

- `task_id` (optional) — specific task ID. If omitted, presents top priorities or auto-resolves via open handovers on v3 backlogs.

## Step 0 — (v3) "Continue" auto-resolve

If the user said "continue this task", "continue where we left off", "resume", or similar AND no explicit `task_id` given:

- Call `backlog_handover_list(status="open", limit=1)`. If empty, fall through to Step 1.
- Take the first id in the handover's `task_ids`. Call `backlog_get_task(<id>)` slim.
- If status is `done` or `archived`, fall through to Step 1.
- Confirm once: "Continuing `<task_id>` from the `<date>` handover (`<tldr>`). Right task?" Default Yes. On confirmation, jump to Step 4.

On v2 backlogs (no handover index), skip silently.

## Step 1 — If no task ID

Call `backlog_next_available` to get ready tasks. Phase-filtered when a phase is active. Present and ask the user to pick. If empty, suggest `/advance-phase` or add work.

## Step 2 — Parallel-task check

Call `backlog_status` (slim). If 3+ tasks already in-progress: "You have N tasks in-flight. Switch focus or pick this up in parallel?"

## Step 3 — Dependency check

Call `backlog_dependencies(<task_id>)`. If any dependency is not done: warn and let user decide. Do not silently skip.

## Step 4 — Pick the task

Call `backlog_pick_task(<task_id>)` — sets status to in-progress, records started date. Note the `**Schema:** v<N>` line; v3 glance steps below activate only on v3 backlogs.

## Step 5 — Glance context load (v3)

Run all sub-steps together. Total budget: ~500 tokens.

**5a. Open handovers for this task**

Call `backlog_handover_list(task_id=<task_id>, status="open", limit=3)`. Returns IDs + tldr + next_action. Surface: "N open handovers. Latest: `<tldr>`." If a handover has `session_kind: context-handoff` AND non-trivial `next_action`, load its full body via `backlog_handover_get <id>`.

**5b. Related issues**

Call `backlog_issue_list(task_id=<task_id>)` for open P0/P1 issues. Surface as: `ISS-014 (P1, open) Login accepts whitespace password`.

**5c. Matched lessons (IDs + tldrs only)**

Call `backlog_lesson_match(task_title=<title>, touched_files=<anchors>)`. Returns ≤3 best-match lesson IDs + tldrs. Surface: "3 lessons match — L-007: auth session read-before-edit · L-014: avoid raw SQL." Do **not** load full lesson bodies — that is `--deep` only.

**5d. Linkage pills**

The slim `backlog_get_task` response includes bare ID linkage. Surface as: `depends_on: T-002 · fixes: ISS-007 · informed_by: L-003`.

## Step 6 — Spec-review + anchors (critical/high only)

If `task.spec_review` present: summarize verdict. If verdict is `fail`, warn and ask for override.
If absent and task has `docs.spec` or `docs.plan`: suggest `taskmaster:spec-review` first. Don't block.
If `task.anchors`: display prominently. Remind to stay within anchor scope.

Skip for medium/low tasks.

## Step 7 — Read linked docs

If the task has a `docs` field, read those files before writing code.

## Step 8 — Git worktree creation (REQUIRED)

The `backlog_pick_task` response includes worktree instructions. Follow them. A dedicated worktree per task is mandatory — never skip it.

1. `git worktree add .worktrees/{task-id} -b feature/{task-id}` from repo root.
2. `backlog_update_task(<task_id>, "branch", "feature/{task-id}")`
3. `backlog_update_task(<task_id>, "worktree", ".worktrees/{task-id}")`

If worktree exists but is orphaned: `git worktree prune` and recreate.

## Deep mode (`--deep`)

When the user says `pick-task <id> --deep` or "load everything": run glance steps above, then continue with `references/deep-mode.md`.

## Task lifecycle

```
todo → in-progress → in-review → done → archived
```

`in-review` = Claude done, user tests. `done` = user confirmed.

## Reclaiming a locked task

`backlog_pick_task(task_id, force=True)` reclaims in a single atomic call. Never manually edit `backlog.yaml`.

## Notes

- `backlog_pick_task` is idempotent for already in-progress tasks in same session.
- Picking an `in-review` task demotes it to `in-progress` — confirm demotion with user first.
- Picking a `blocked` task is rejected — help user resolve blockers first.

## Additional resources

- `references/deep-mode.md` — full deep ceremony (full body, lesson bodies, blast radius, handover bodies)
- `references/v3-context-loading.md` — token budget breakdown for glance steps
