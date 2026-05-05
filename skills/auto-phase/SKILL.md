---
name: auto-phase
description: "Drive every todo task in every epic of one phase through the full auto lifecycle. Outer orchestrator over auto-epic. Invoke when the user says 'auto-phase development', 'autopilot phase polish', or wants to batch-execute an entire phase of work."
---

# Auto-Phase — orchestrate a phase by iterating its epics

This skill is the outermost orchestrator. It calls `auto-epic` for each epic that has todo tasks under the named phase, then writes a phase-level handover.

> **Scope check:** auto-phase is a *very* large operation. Even a small phase typically contains 3–5 epics × 5–10 tasks each. Confirm strongly before starting.

## Step 0: Strong confirmation

Use `AskUserQuestion`:

```
AskUserQuestion({
  questions: [{
    question: "Run auto-phase on '<phase-id>'? This will execute every todo task in every epic of this phase.",
    header: "Confirm",
    multiSelect: false,
    options: [
      { label: "Run with gates", description: "Stop for plan + review approval on each task — recommended for first run" },
      { label: "Run unattended", description: "Skip user gates, halt only on failure" },
      { label: "Continue past failures", description: "Run unattended AND keep going if individual tasks fail" },
      { label: "Cancel", description: "Don't start" }
    ]
  }]
})
```

Also surface the task count up-front so the user knows what they're approving:

```
backlog_status
→ count tasks under <phase-id> with status in (todo, none)
```

Show: "This will run N tasks across M epics. Estimated time: <rough estimate>."

If N > 20, additionally ask: "That's a lot — are you sure you don't want to start with `auto-epic` on one epic first?"

## Step 1: Seed run

```
backlog_auto_start(
  mode="phase",
  target="<phase-id>",
  no_gate=<from step 0>,
  continue_on_fail=<from step 0>
)
```

This expands phase → all todo tasks across all epics under that phase, in epic-order then task-order. Reads each `auto_model` field. Returns the first cursor.

## Step 2: Epic-by-epic loop

For each distinct epic represented in the pending list:

1. Read `backlog_auto_status`, note the cursor task id.
2. Look up which epic that task belongs to.
3. Dispatch the **auto-epic skill** as a subagent:

```
Agent(
  subagent_type="general-purpose",
  model="opus",                     # the orchestrator-of-orchestrators uses opus
  description="Auto-epic <epic-id>",
  prompt="""
You are the auto-epic orchestrator dispatched by auto-phase. The auto state
machine is already initialized at mode=phase, target=<phase-id>. The current
cursor is at task <task-id> in epic <epic-id>.

Your job: drive every remaining todo task of epic <epic-id> using the
taskmaster:auto-epic skill (load it via Skill tool). Do NOT re-init the
state — it's already set up.

When done with this epic (or halted), return ONLY a structured summary:
{
  "epic_id": "<id>",
  "tasks_done": <count>,
  "tasks_failed": <count>,
  "summary": "<one paragraph aggregate>",
  "epic_handover_id": "<id>"
}

Stop when cursor moves to a task in a different epic — that signals this
epic is done and auto-phase will dispatch the next one.
"""
)
```

The auto-epic subagent runs its own loop dispatching auto-task subagents. **This is two levels of subagent isolation** — the auto-phase context only sees one summary per epic, not per task.

4. Capture the epic-level summary.
5. After auto-epic returns, read `backlog_auto_status` again. If cursor is None or halted, exit the loop. Otherwise, the next iteration handles the next epic.

## Step 3: Phase-level handover

After the loop exits:

```
backlog_handover_create(
  tldr="Auto-phase <phase-id>: <N> epics, <M> tasks done, <K> failed.",
  next_action="<one line — usually 'advance phase' or 'address failures'>",
  task_ids=[],                            # empty — phase scope is too big to enumerate
  session_kind="end-of-day",
  body="""
## Run summary
- Mode: phase
- Target: <phase-id>
- Started: <timestamp>
- Total tasks: <N>
- Outcomes: done=<M>, failed=<K>, blocked=<L>

## Per epic
- <epic-1>: <epic_handover_id> — <one line>
- <epic-2>: <epic_handover_id> — <one line>
- ...

## Phase status check
Should this phase advance to `done`? Inspect `backlog_phase_status` —
if all tasks are now done, prompt the user about `backlog_advance_phase`.

## Failed tasks
- <task-id>: <reason>
- ...

## Next
- <what to do>
"""
)
```

## Step 4: Phase advance suggestion

If all tasks completed successfully, prompt:

```
backlog_phase_status <phase-id>
→ if status would now be "ready to complete":
  AskUserQuestion: "All tasks in <phase-id> done. Advance to next phase?"
    → if yes, call backlog_advance_phase
```

## Step 5: Finish

```
backlog_auto_finish
```

Tell the user: "Auto-phase <id> complete. Phase handover: <id>. Epic handovers: <list>. Use `backlog_recap` to see the project-state delta."

## Token-cost estimate

For a phase with 4 epics × 6 tasks (24 tasks total):

- Step 0 + 1: ~400 tokens.
- Step 2 (4 epic dispatches × ~200 tokens summary): ~800 tokens.
- Step 3 phase handover: ~600 tokens.
- Step 4–5: ~150 tokens.

Total auto-phase orchestrator main context: ~1,950 tokens. Each epic subagent costs ~2,250 in *its* context, each task subagent costs whatever the implementation needs in *its* context. Three-level isolation keeps every level bounded.

## What this skill does NOT do

- Does not pick which phase to run — the user names it.
- Does not implement tasks — that's three levels down (auto-task in a subagent of auto-epic in a subagent of auto-phase).
- Does not auto-advance the phase — only suggests.

## Failure aggregation policy

The auto state machine treats `mode="phase"` as a single flat queue of tasks across all epics in epic-then-task order — there is no per-epic boundary inside `auto/state.json`. Consequence:

- **`continue_on_fail=false` (default in "Run with gates" / "Run unattended"):** the cursor halts on the first failed task. Auto-phase exits the loop and writes the phase-level handover with whatever was completed before the halt. Failures stop the whole phase, not just the current epic.
- **`continue_on_fail=true` ("Continue past failures"):** the cursor advances to the next pending task regardless of the failure. The phase walks every task to completion or failure; the run ends when the queue empties.

There is no separate "halt this epic, advance to next epic" mode — that distinction would need to live in the state machine (it doesn't). If you want that semantic, run `auto-epic` per epic by hand instead of `auto-phase`.

## Why this is not yet auto-roadmap

A roadmap-level skill (auto-roadmap, dispatching auto-phase per planned phase) is **out of scope**. Phase boundaries usually represent meaningful project transitions where human judgment is required (deploy, milestone review, scope change). Auto-phase is the largest unit you should automate without explicit per-step approval.
