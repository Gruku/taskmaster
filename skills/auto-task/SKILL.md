---
name: auto-task
description: "Drive a single task through the full lifecycle (PICK → SPEC_REVIEW → IMPLEMENT → REVIEW_GATE → HANDOVER → END_SESSION) using the auto state machine. Invoke when the user says 'auto T-001', 'autopilot this task', 'run task auto', or when called by auto-epic/auto-phase orchestrator skills as a subagent recipe."
---

# Auto-Task — single-task lifecycle driver

This skill drives **one** task through every stage of work, persisting cursor state between stages so a crash or compaction doesn't lose progress. It is the inner loop for `auto-epic` and `auto-phase`.

> **Critical:** This skill works against the auto state machine. Never edit `backlog.yaml` or `auto/state.json` manually — every stage transition must go through `backlog_auto_advance` so the cursor stays consistent.

## Step 0: Establish or resume state

Run `backlog_auto_status`.

- If output is "No auto run in progress" and the user invoked the skill directly with a task id, call `backlog_auto_start(mode="task", target=<id>)` to seed state. The first cursor will be at task `<id>`, stage `PICK`.
- If a run is in progress, read the cursor (`task_id`, `stage`, `model`). Resume from `cursor.stage`. **Do not re-run earlier stages** — they completed in a previous session/process.
- If the cursor is `None`, the run is finished — call `backlog_auto_finish` and stop.

## Step 1: PICK

When `cursor.stage == "PICK"`:

1. Call `backlog_get_task(<task_id>)` and read the task body via `backlog_handover_get` for any linked handovers (`related_handovers` field).
2. Call `backlog_lesson_match(task_title=<title>)` and load matching lessons in full into your working context. (≤3 hits expected.)
3. Set the task status to `in-progress` via `backlog_update_task(status="in-progress")`.
4. Call `backlog_auto_advance("SPEC_REVIEW")`.

## Step 2: SPEC_REVIEW (gated)

When `cursor.stage == "SPEC_REVIEW"`:

1. If the task body has no `## Spec / Plan` section yet, draft one. Use `superpowers:writing-plans` if the task is non-trivial.
2. Read `state.config.no_gate`. If **false** (the default), present the plan to the user via `AskUserQuestion`:
   ```
   - "Approve plan" → continue
   - "Revise" → ask for feedback, redraft, re-ask
   - "Reject" → call backlog_auto_complete_task(status="failed", fail_reason="spec-rejected"), stop.
   ```
3. If `no_gate=true`, skip the user gate (use only when running batch jobs unattended — emit a warning in the handover stub).
4. Save the spec back into the task body with `backlog_update_task(spec="...")` (or however the task body is updated in this codebase — check existing pick-task skill for the canonical write path).
5. Call `backlog_auto_advance("WRITE_TESTS")` if this is a feature/bug task with tests; otherwise skip to `IMPLEMENT`.

## Step 3: WRITE_TESTS (optional, TDD)

When `cursor.stage == "WRITE_TESTS"`:

1. Use the `superpowers:test-driven-development` skill to write failing tests for the new behavior.
2. Run the test suite — confirm only the new tests fail and existing tests still pass.
3. Call `backlog_auto_advance("IMPLEMENT")`.

If the task is a refactor / docs / chore where TDD doesn't apply, skip this stage entirely from `SPEC_REVIEW` to `IMPLEMENT`.

## Step 4: IMPLEMENT

When `cursor.stage == "IMPLEMENT"`:

1. Do the work. Read files before editing, check for matched lessons before writing, follow existing project conventions.
2. **Reinforce lessons** that you actually applied: `backlog_lesson_reinforce(<lesson_id>)`. Skip lessons that didn't end up being relevant.
3. Commit logically — small, scoped commits per change. Capture the commit shas in a list (you'll pass them to `complete_task`).
4. Call `backlog_auto_advance("TEST")`.

## Step 5: TEST

When `cursor.stage == "TEST"`:

1. Run the project's full test suite (or the relevant subset). Capture stdout/stderr.
2. **If tests fail** that should pass: this is a regression. Either fix it now and re-run, or escalate:
   - Call `backlog_auto_complete_task(status="failed", fail_reason="tests-failed", summary="<what failed and why>")`.
   - Stop. The orchestrator decides whether to halt the batch.
3. **If tests pass**: call `backlog_auto_advance("REVIEW_GATE")`.

## Step 6: REVIEW_GATE (gated)

When `cursor.stage == "REVIEW_GATE"`:

1. Run the existing `taskmaster:review-gate` skill flow on the diff.
2. If `state.config.no_gate == false`, gate on user approval before continuing.
3. If approved: `backlog_auto_advance("HANDOVER_STUB")`.
4. If rejected: `backlog_auto_complete_task(status="failed", fail_reason="spec-rejected", summary="<reviewer feedback>")` and stop.

## Step 7: HANDOVER_STUB

When `cursor.stage == "HANDOVER_STUB"`:

Write a brief, single-task handover capturing what was done. Keep it under ~200 words — this isn't an end-of-day handover, it's a recovery anchor.

```
backlog_handover_create(
  tldr="<one line — what was completed>",
  next_action="<one line — what comes next, often 'continue auto run'>",
  task_ids=[<current task id>],
  session_kind="auto-stage",
  body="""
## Decisions
- <key non-obvious choices made>

## Notes
- <commits, files touched, lesson reinforcements>
"""
)
```

Capture the returned handover id; pass it to `complete_task` below so the run record links it.

Then `backlog_auto_advance("END_SESSION")`.

## Step 8: END_SESSION

When `cursor.stage == "END_SESSION"`:

1. Transition the task: `backlog_complete_task(<task_id>)` (or whatever the canonical close path is — see end-session skill).
2. Optionally call `backlog_snapshot --quiet` to update the snapshot for `recap`.
3. Call:
   ```
   backlog_auto_complete_task(
     status="done",
     summary="<one paragraph: what shipped, why it mattered>",
     commits=[<sha1>, <sha2>, ...],
     handover_id="<from step 7>"
   )
   ```
   This advances the cursor to the next pending task (or sets it to None if the run is single-task).
4. If the cursor is now `None`, the run is complete. The **orchestrator** (auto-epic/auto-phase, or the user directly) calls `backlog_auto_finish` after writing a run-level handover. Do NOT call `backlog_auto_finish` from inside this skill — let the caller decide.
5. Stop. Return a structured summary to the orchestrator:
   ```
   {
     "task_id": "<id>",
     "status": "done",
     "summary": "<paragraph>",
     "commits": [...],
     "handover_id": "<id>",
     "model_used": "<sonnet|opus>"
   }
   ```

## Failure escape hatches

At any stage, you may halt with `backlog_auto_complete_task(status="failed", fail_reason=..., summary=...)`. Valid reasons:

| Reason | When |
|---|---|
| `tests-failed` | Test suite went red and you couldn't fix it |
| `spec-rejected` | User rejected plan or review |
| `blocked` | External dependency, missing access, decision needed |
| `crashed` | Tooling/environment broke; investigation needed |
| `user-aborted` | User explicitly said stop |

After failure, invoke `taskmaster:handover` with `session_kind="context-handoff"` describing where you stopped and what's broken, so the next session can resume. Halt — don't attempt the next stage.

## What this skill does NOT do

- Does not pick which task to work on. The orchestrator (or `backlog_auto_start`) decides.
- Does not dispatch subagents. That's the auto-epic / auto-phase orchestrator's job.
- Does not write run-level (epic/phase) handovers — only per-task stage handovers.
- Does not call `backlog_auto_finish` on the run — orchestrator owns lifecycle.

## Token discipline

- Do not load all handovers at PICK — only those listed in `task.related_handovers`.
- Do not load all lessons — only `match_lessons_for_task` results.
- Do not include full file contents in handover bodies — reference paths.
- Stage handover stubs cap at ~200 words; the *task body* in `tasks/<id>.md` is where long narrative goes.

## Resume semantics

If you wake up mid-run (e.g., after compaction), `backlog_auto_status` tells you exactly where to pick up. Read it first, then jump to the matching step above. Do not redo earlier stages. The PreCompact hook + atomic state writes mean the cursor is always trustworthy — if it says `IMPLEMENT`, the implement work is in progress, not done.
