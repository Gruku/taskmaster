# Pick Task

Select a task to work on and set it to in-progress. Default mode is a **glance briefing** (~600–800 tokens). Append `--deep` for full task body, blast radius, and handover context.

## Arguments

- `task_id` (optional) — specific task ID. If omitted, presents top priorities or auto-resolves via open handovers on v3 backlogs.

## Step 0 — (v3) "Continue" auto-resolve

If the user says "continue", "resume", or similar with no explicit `task_id` or thread name:

- Call `backlog_thread_list()`. If empty, fall through to Step 1.
- If exactly one open thread, treat it as the resume target. If more than one, show the board and ask which (or accept a pasted thread name / handover id).
- Call `backlog_thread_resume(<name>)` to load its latest handover. Take the first id in the handover's `task_ids`. Call `backlog_get_task(<id>)` slim.
- If status is `done` or `archived`, fall through to Step 1.
- Confirm: "Continuing `<task_id>` from thread `<name>` (`<tldr>`). Right task?" Default Yes → jump to Step 4.

v2 backlogs: skip silently.

## Step 1 — If no task ID

Call `backlog_next_available` to get ready tasks. Phase-filtered when a phase is active. Present and ask the user to pick. If empty, suggest `/advance-phase` or add work.

## Step 2 — Parallel-task check

Call `backlog_status` (slim). If 3+ tasks in-progress: "You have N tasks in-flight. Switch focus or pick this up in parallel?"

## Step 3 — Dependency check

Call `backlog_dependencies(<task_id>)`. Warn on unmet deps — let user decide; do not skip silently.

## Step 4 — Pick the task

Call `backlog_pick_task(<task_id>)` — sets status to in-progress, records started date. Note the `**Schema:** v<N>` line; v3 glance steps below activate only on v3 backlogs.

Lane'd tasks: if spec/body present, call `backlog_record_gate(<task_id>, "spec", status="done")`.

## Step 5 — Glance context load (v3)

Run all sub-steps together. Budget: ~500 tokens.

**5a. Open handovers for this task**

Call `backlog_handover_list(task_id=<task_id>, status="open", limit=3)`. Surface: "N open handovers. Latest: `<tldr>`." If `session_kind: context-handoff` AND non-trivial `next_action`, load full body via `backlog_handover_get <id>`.

**5b. Related issues**

Call `backlog_issue_list(task_id=<task_id>)` for open P0/P1 issues.

**5c. Linkage pills**

Surface bare ID linkage from slim response: `depends_on: T-002 · fixes: ISS-007`. If `tracker_id` starts with `linear-`, append tracker pill (third hyphen-split segment uppercased). No extra tool calls.

## Step 6 — Spec-review + anchors (critical/high only)

If `task.spec_review` present: summarize verdict; `fail` → warn and ask for override.
If absent and task has `docs.spec` or `docs.plan`: suggest `taskmaster:spec-review`. Don't block.
If `task.anchors`: display prominently; remind to stay within scope.

Skip for medium/low tasks.

## Step 7 — Read linked docs

If the task has a `docs` field, read those files before writing code.

## Step 8 — Git worktree creation (REQUIRED)

The `backlog_pick_task` response includes worktree instructions. Follow them.

**Bundle task** (response carries `bundle` / `_session_bundle`): use the returned shared-worktree instruction (`.worktrees/<slug>`, `feature/<slug>`); announce bundle members; record branch + worktree on this task. Required — do not skip. See `references/bundles.md`.

**Solo task** (no bundle):
1. `git worktree add .worktrees/{task-id} -b feature/{task-id}`
2. `backlog_update_task(<task_id>, "branch", "feature/{task-id}")`
3. `backlog_update_task(<task_id>, "worktree", ".worktrees/{task-id}")`

Orphaned: `git worktree prune` then recreate.

## Step 8.5 — Bundle detection fallback (solo picks only)

After solo worktree creation, run the detection fallback: scan for overlapping `todo` tasks, assign them a shared bundle slug, and announce each sweep — that is the veto window. User objects → clear the slug. Full protocol in `references/bundles.md` (Path B).

## Deep mode (`--deep`)

Run glance steps above, then continue with `references/deep-mode.md`.

## Task lifecycle

```
todo → in-progress → in-review → done → archived
```

`in-review` = Claude done, user tests.

## Reclaiming a locked task

`backlog_pick_task(task_id, force=True)`. Never manually edit `backlog.yaml`.

## Notes

- Idempotent for already in-progress tasks. Picking `in-review` demotes to `in-progress` — confirm first. Picking `blocked` is rejected.

## Additional resources

- `references/bundles.md` — bundle pickup protocol + detection-fallback detail
- `references/deep-mode.md` — full deep ceremony
- `references/v3-context-loading.md` — token budget for glance steps
