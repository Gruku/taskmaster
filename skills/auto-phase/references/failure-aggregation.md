# Auto-Phase — Failure Aggregation Policy

This file covers failure semantics, scope boundaries, and recovery options for the auto-phase skill.

## Failure Aggregation Policy

The auto state machine treats `mode="phase"` as a single flat queue of tasks across all epics in epic-then-task order — there is no per-epic boundary inside `auto/state.json`. Consequence:

- **`continue_on_fail=false` (default in "Run with gates" / "Run unattended"):** the cursor halts on the first failed task. Auto-phase exits the loop and writes the phase-level handover with whatever was completed before the halt. Failures stop the whole phase, not just the current epic.
- **`continue_on_fail=true` ("Continue past failures"):** the cursor advances to the next pending task regardless of the failure. The phase walks every task to completion or failure; the run ends when the queue empties.

There is no separate "halt this epic, advance to next epic" mode — that distinction would need to live in the state machine (it doesn't). If you want that semantic, run `auto-epic` per epic by hand instead of `auto-phase`.

## Recovery After Phase Halt

When auto-phase halts on failure:

1. Read `backlog_auto_status` to see which task failed and at which stage.
2. Use `taskmaster:auto-task` to retry just the failed task (the cursor already points to it).
3. Once fixed, re-invoke `taskmaster:auto-phase` — but it will re-read the state and continue from the current cursor position, not restart from the beginning.
4. If you want to skip the failed task: `backlog_auto_complete_task(status="failed", fail_reason="skipped")` advances the cursor to the next task; then re-invoke auto-phase.
5. To discard the run entirely: `backlog_auto_abort`. The cursor clears; all tasks remain at their current status.

## What this skill does NOT do

- Does not pick which phase to run — the user names it.
- Does not implement tasks — that's three levels down (auto-task in a subagent of auto-epic in a subagent of auto-phase).
- Does not auto-advance the phase — only suggests after all tasks complete.
- Does not run tasks in parallel — the state machine cursor is sequential.
- Does not handle inter-epic dependency ordering beyond epic order in the state file.

## When to Use auto-epic Instead

If you want epic-level failure isolation (one epic fails, the next epic still runs), run `auto-epic` per epic manually rather than using `auto-phase`. Auto-phase's flat cursor treats the whole phase as one run — there is no way to tell it "halt this epic but continue with the next one" without modifying the state machine itself.
