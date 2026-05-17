---
name: auto-epic
description: "Drive every todo task in an epic through the full auto lifecycle, dispatching one subagent per task with per-task model selection (sonnet/opus). Invoke when the user says 'auto-epic features', 'autopilot the api epic', or wants to batch-execute tasks within one epic."
---

# Auto-Epic — orchestrate a batch of tasks under one epic

This skill is the **orchestrator**. It loops over todo tasks in an epic, dispatching one subagent per task to run `taskmaster:auto-task`. Each subagent runs in its own context; the orchestrator only sees structured per-task results.

> **Why subagents:** the orchestrator's main context cost stays ~500 tokens for a 10-task run, not 10x a full implementation context. This is what makes long batch runs viable.

## Step 0: Confirm the run

Use `AskUserQuestion` to confirm before starting:

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

Map: "Run with gates" -> `no_gate=false, continue_on_fail=false`; "Run unattended" -> `no_gate=true, continue_on_fail=false`; "Continue past failures" -> `no_gate=true, continue_on_fail=true`.

## Step 1: Seed the run

```
backlog_auto_start(mode="epic", target="<epic-id>", no_gate=<from step 0>, continue_on_fail=<from step 0>)
```

Lists all tasks under the epic with `status in (todo, none)`. Reads each `auto_model` field (defaults `"sonnet"`). Writes `auto/state.json`. Returns first task id and model. If "No todo tasks under epic" — stop, tell the user.

## Step 2: Loop — dispatch one subagent per task

Repeat until `backlog_auto_status` reports `Current: (none)` or a failure halts:

**2b. Dispatch subagent:**
```
Agent(
  subagent_type="general-purpose",
  model=<cursor.model>,
  description="Auto-task <task-id>",
  prompt="You are dispatched as the inner-loop subagent for taskmaster:auto-epic.
Drive task <task-id> through the full lifecycle using taskmaster:auto-task (load via Skill tool).
TDD is the opinionated default. Return ONLY structured JSON:
{ task_id, status, summary, commits, handover_id, fail_reason }"
)
```

Loop sub-steps (2a: read cursor, 2c: verify cursor advanced, 2d: token discipline, 2e: continue or halt) in `references/loop-protocol.md`.

## Step 3: Run-level handover

After loop exits: `backlog_handover_create(tldr="Auto-epic <id>: <N> done, <M> failed.", next_action=..., task_ids=[...], session_kind="end-of-day", body=<run summary with per-task outcomes>)`.

## Step 4: Finish

```
backlog_auto_finish
```

Tell the user: "Auto-epic <id> complete: N done, M failed. Run-level handover: <id>."

Failure recovery and "what this skill does NOT do" in `references/failure-recovery.md`.

## Per-task model selection

Set `auto_model: opus` (or `sonnet`) per task in `backlog.yaml`. Default: `"sonnet"`. Use `"opus"` for novel design problems, multi-system refactors.

## Token-cost estimate

10 tasks: ~300 (init) + ~1,500 (loop) + ~400 (handover) + ~50 (finish) = ~2,250 orchestrator tokens. Independent of task complexity.
