---
name: auto-epic
description: "Drive every todo task in an epic through the full auto lifecycle, dispatching one subagent per task with per-task model selection (sonnet/opus). Invoke when the user says 'auto-epic features', 'autopilot the api epic', or wants to batch-execute tasks within one epic."
---

# Auto-Epic — orchestrate a batch of tasks under one epic

This skill is the **orchestrator**. It loops over the todo tasks in an epic, dispatching one subagent per task to run `taskmaster:auto-task`. Each subagent runs in its own context (full task body, lessons, plan, tests, diffs); the orchestrator only sees structured per-task results.

> **Why subagents:** the orchestrator's main context cost stays roughly constant regardless of task count or task complexity. A 10-task auto-epic accumulates ~500 tokens of orchestrator state, not 10× a full implementation context. This is what makes long batch runs viable.

## Step 0: Confirm the run

Use `AskUserQuestion` to confirm before starting (auto-epic kicks off real implementation work):

```
AskUserQuestion({
  questions: [{
    question: "Run auto-epic on '<epic-id>'?",
    header: "Confirm",
    multiSelect: false,
    options: [
      { label: "Run with gates", description: "Stop for plan + review approval on each task (safer)" },
      { label: "Run unattended", description: "Skip user gates, halt only on test failure (--no-gate)" },
      { label: "Continue past failures", description: "Run unattended AND keep going if tests fail (--no-gate --continue-on-fail)" },
      { label: "Cancel", description: "Don't start the run" }
    ]
  }]
})
```

Map to flags:
- "Run with gates" → `no_gate=false, continue_on_fail=false`
- "Run unattended" → `no_gate=true, continue_on_fail=false`
- "Continue past failures" → `no_gate=true, continue_on_fail=true`

## Step 1: Seed the run

```
backlog_auto_start(
  mode="epic",
  target="<epic-id>",
  no_gate=<from step 0>,
  continue_on_fail=<from step 0>
)
```

This:
- Lists all tasks under the epic with `status in (todo, none)`.
- Reads each task's `auto_model` field (defaults to `"sonnet"`).
- Writes `auto/state.json` with cursor at the first pending task.
- Returns the first task id and model.

If output is "No todo tasks under epic" — stop, tell the user.

## Step 2: Loop — dispatch one subagent per task

Repeat until `backlog_auto_status` reports `Current: (none — run complete)` **or** a failure halts the run:

### 2a. Read cursor

```
backlog_auto_status
→ parses out cursor.task_id, cursor.stage, cursor.model
```

If `cursor.stage` is `HANDOVER_STUB` and the task is in `failed`, the previous task halted the run. Stop the loop and proceed to step 3 (failure handover).

### 2b. Dispatch subagent

```
Agent(
  subagent_type="general-purpose",
  model=<cursor.model>,           # "sonnet" or "opus" — read from state
  description="Auto-task <task-id>",
  prompt="""
You are dispatched as the inner-loop subagent for taskmaster:auto-epic.

Your job: drive task <task-id> through the full lifecycle using the
taskmaster:auto-task skill (load it explicitly via the Skill tool).

Constraints:
- The auto state machine is already initialized. Cursor is at task <task-id>, stage <cursor.stage>.
- Follow taskmaster:auto-task exactly. Do not deviate.
- When done (or halted), return ONLY a structured JSON-style summary as your final message:
  {
    "task_id": "<id>",
    "status": "done" | "failed" | "blocked",
    "summary": "<one paragraph>",
    "commits": ["sha1", "sha2"],
    "handover_id": "<id or null>",
    "fail_reason": "<reason or null>"
  }

Do NOT include file diffs, full plans, or test output. The orchestrator
only needs the structured result. Long detail goes in the per-task
handover artifact you write.
"""
)
```

The subagent's final message is its return value. It runs `taskmaster:auto-task`, which itself calls `backlog_auto_complete_task` to advance the cursor before returning.

### 2c. Verify cursor advanced

The subagent should have called `complete_task`. Verify by reading state:

```
backlog_auto_status
```

If the cursor is still on the same task at the same stage, the subagent failed to call `complete_task` — treat this as a `crashed` failure and call `backlog_auto_complete_task(status="failed", fail_reason="crashed", summary="<subagent did not finalize>")` from the orchestrator yourself, then continue per `continue_on_fail`.

### 2d. Token discipline check

After each task, your main-context accumulation should be:
- The structured result (JSON) — ~80 tokens.
- Cursor read after — ~50 tokens.

That's ~130 tokens per task. Do **not** read task bodies, handover bodies, or lesson contents in the orchestrator main context. Only call `backlog_auto_status` and the subagent dispatch.

### 2e. Continue or halt

Loop back to 2a. The auto state machine itself enforces continue/halt logic based on `config.continue_on_fail` — you don't need separate orchestrator branching.

## Step 3: Run-level handover

After the loop exits (cursor None, or halt due to unrecoverable failure):

1. Read `backlog_auto_status` for the final summary.
2. Read each completed task's recorded summary (already in state).
3. Aggregate into one **epic-level** handover:
   ```
   backlog_handover_create(
     tldr="Auto-epic <epic-id>: <N> done, <M> failed.",
     next_action="<one line — usually 'review failed tasks' or 'continue with next epic'>",
     task_ids=[<all task ids touched>],
     session_kind="end-of-day",
     body="""
## Run summary
- Mode: epic
- Target: <epic-id>
- Started: <timestamp from state>
- Models used: sonnet × N, opus × M

## Completed
- <task-id>: <summary> (commits: <sha-list>)
- ...

## Failed
- <task-id>: <reason> — <summary>
- ...

## Next
- <what to do — fix failures, ramp to next epic, etc.>
"""
   )
   ```

## Step 4: Finish

```
backlog_auto_finish
```

This clears `auto/state.json`. The per-task stage handovers (`session_kind="auto-stage"`) remain on disk under `handovers/`; they are subject to the same 30-entry index cap as any other handover and roll to `handovers/_archive/<year>/` only when the cap is exceeded. The aggregated **epic-level** handover written in step 3 is the durable record — auto-stage stubs are recovery anchors and intentionally noisy.

If you want to prune auto-stage stubs more aggressively after a successful run (so they don't dominate the next session's `backlog_handover_list` output), call `backlog_handover_resync()` — it re-reads from disk and re-applies the cap. Optional.

Tell the user a one-line summary: "Auto-epic <id> complete: N done, M failed. Run-level handover: <handover-id>."

## Failure recovery

If the run halted on failure (`continue_on_fail=false` and a task failed):

1. The state file is preserved. The cursor is at the failed task.
2. Write the run-level handover (step 3) summarizing what got done before the halt and what failed.
3. Do NOT call `backlog_auto_finish` — leave state for the user to resume.
4. Tell the user: "Halted on `<task-id>`: `<reason>`. Run state preserved. Fix the underlying issue, then either re-run with `--continue-on-fail`, or re-invoke `taskmaster:auto-task` to retry just this task, or `backlog_auto_abort` to discard the run."

## What this skill does NOT do

- Does not pick which epic to run on — the user names it.
- Does not implement tasks itself — every task runs in a subagent.
- Does not load task bodies or diffs into orchestrator context — only structured results.
- Does not call `backlog_auto_complete_task` for tasks (subagents do that via auto-task) — only as a fallback on subagent crash.

## Per-task model selection

Each task in `backlog.yaml` may declare:

```yaml
- id: features-007
  title: Refactor session storage
  auto_model: opus     # complex, give it the bigger model
```

Default: `"sonnet"`. Use `"opus"` for: novel design problems, multi-system refactors, anything where context matters more than throughput. The orchestrator reads this field and passes the model when dispatching.

## Token-cost estimate

For an epic with 10 tasks of mixed complexity:

- Step 0 + 1 (init): ~300 tokens.
- Step 2 loop (10 iterations, ~150 tokens each): ~1,500 tokens.
- Step 3 run handover: ~400 tokens.
- Step 4 finish: ~50 tokens.

Total orchestrator-context cost: ~2,250 tokens — independent of how complex the tasks are. Subagent contexts are discarded after each task returns.
