# Auto-Epic — Failure Recovery

This file covers the failure recovery protocol and scope boundaries for the auto-epic skill.

## Failure Recovery

If the run halted on failure (`continue_on_fail=false` and a task failed):

1. The state file is preserved. The cursor is at the failed task.
2. Write the run-level handover (step 3) summarizing what got done before the halt and what failed.
3. Do NOT call `backlog_auto_finish` — leave state for the user to resume.
4. Tell the user: "Halted on `<task-id>`: `<reason>`. Run state preserved. Fix the underlying issue, then either re-run with `--continue-on-fail`, or re-invoke `taskmaster:auto-task` to retry just this task, or `backlog_auto_abort` to discard the run."

## Recovery Options After Halt

When a run halts, the user has three paths:

- **Fix and retry:** Address the root cause (failing tests, implementation error), then invoke `taskmaster:auto-task` directly on the failed task to retry it. The cursor stays at the failed task so auto-task picks up where it left off.
- **Skip and continue:** Call `backlog_auto_complete_task(status="failed", fail_reason="<reason>", summary="skipped")` manually to mark the task failed and advance the cursor, then re-invoke auto-epic to continue with the next task. Only use this with `continue_on_fail=true` semantics.
- **Abort the run:** Call `backlog_auto_abort` to discard the state. The cursor is cleared; tasks remain at whatever status they reached. Re-run from scratch when ready.

## What this skill does NOT do

- Does not pick which epic to run on — the user names it.
- Does not implement tasks itself — every task runs in a subagent.
- Does not load task bodies or diffs into orchestrator context — only structured results.
- Does not call `backlog_auto_complete_task` for tasks (subagents do that via auto-task) — only as a fallback on subagent crash.
- Does not write code, run tests, or make commits — the inner auto-task subagent does all of that.

## Subagent Crash vs Task Failure

A subagent crash (subagent returned without calling `backlog_auto_complete_task`) is treated as a `failed` status by the orchestrator — it calls `backlog_auto_complete_task(status="failed", fail_reason="crashed")` itself and continues per `continue_on_fail`. A genuine task failure (subagent called `complete_task` with `status="failed"`) is a recognized failure state with a recorded `fail_reason` and `summary`.
