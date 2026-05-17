---
name: auto-phase
description: "Drive every todo task in every epic of one phase through the full auto lifecycle. Outer orchestrator over auto-epic. Invoke when the user says 'auto-phase development', 'autopilot phase polish', or wants to batch-execute an entire phase of work."
---

# Auto-Phase — orchestrate a phase by iterating its epics

This skill is the outermost orchestrator. It calls `auto-epic` for each epic that has todo tasks under the named phase, then writes a phase-level handover.

> **Scope check:** auto-phase is a *very* large operation — even a small phase typically contains 3-5 epics x 5-10 tasks each. Confirm strongly before starting.

## Step 0: Strong confirmation

Use `AskUserQuestion` with options: "Run with gates" / "Run unattended" / "Continue past failures" / "Cancel". Also surface the task count via `backlog_status`. If N > 20, ask if they want to start with `auto-epic` on one epic first.

Map: "Run with gates" -> `no_gate=false, continue_on_fail=false`; "Run unattended" -> `no_gate=true, continue_on_fail=false`; "Continue past failures" -> `no_gate=true, continue_on_fail=true`.

## Step 1: Seed run

```
backlog_auto_start(mode="phase", target="<phase-id>", no_gate=<from step 0>, continue_on_fail=<from step 0>)
```

Expands phase -> all todo tasks across all epics in epic-then-task order. Returns the first cursor.

## Step 2: Epic-by-epic loop

For each distinct epic in the pending list: dispatch the auto-epic skill as a subagent (model=opus). The subagent drives every todo task of that epic using `taskmaster:auto-epic`. Returns structured epic-level summary `{ epic_id, tasks_done, tasks_failed, summary, epic_handover_id }`. Loop sub-steps and epic-boundary semantics in `references/loop-protocol.md`.

## Step 3: Phase-level handover

```
backlog_handover_create(
  tldr="Auto-phase <phase-id>: <N> epics, <M> tasks done, <K> failed.",
  next_action="...",
  task_ids=[],
  session_kind="end-of-day",
  body=<run summary with per-epic outcomes and failed tasks>
)
```

## Step 4: Phase advance suggestion

If all tasks completed: `backlog_phase_status <phase-id>`. If ready: `AskUserQuestion: "Advance to next phase?" -> backlog_advance_phase`.

## Step 5: Finish

```
backlog_auto_finish
```

Tell the user: "Auto-phase <id> complete. Phase handover: <id>. Use `backlog_recap` to see the project-state delta."

Failure aggregation policy and "what this skill does NOT do" in `references/failure-aggregation.md`.

## Token-cost estimate

4 epics x 6 tasks: ~400 (init) + ~800 (4 epic dispatches) + ~600 (handover) + ~150 (finish) = ~1,950 orchestrator tokens. Three-level isolation keeps every level bounded.
