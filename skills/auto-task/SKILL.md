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

1. Call `backlog_get_task(<task_id>)` to load the task frontmatter + body.
2. Call `backlog_handover_list(task_id=<task_id>, limit=3)` to get tldrs for prior handovers that referenced this task. Keep all 3 in working context (~150 tokens). Only fetch a full body via `backlog_handover_get <id>` when the latest one's `session_kind` is `context-handoff` AND `next_action` is non-trivial — that's the recovery anchor.
3. Call `backlog_lesson_match(task_title=<title>, touched_files=<task.anchors>)` and fetch the full body of each match via `backlog_lesson_get`. Cap 3 hits, ~900 tokens worst case.
4. Surface open `related_issues` from the task frontmatter — read full body of any P0/P1 entries via `backlog_issue_get` so the implementation knows what defects this work intersects.
5. Set the task status: `backlog_update_task(<task_id>, "status", "in-progress")`. (Don't use `backlog_pick_task` here — that's for interactive picking; auto-task drives status directly so cursor stays consistent.)
6. Call `backlog_auto_advance("SPEC_REVIEW")`.

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
4. Persist the approved spec into `tasks/<task_id>.md` body's `## Spec / Plan` section. The canonical writer is the `taskmaster:spec-review` skill (its step 7a) — invoke it with the drafted plan if it isn't already running. For an unattended path (no_gate=true), use the Edit tool directly on `tasks/<task_id>.md` to insert/replace the `## Spec / Plan` block. There is no `backlog_update_task(spec=...)` MCP field — frontmatter only carries metadata.
5. **Default: advance to `WRITE_TESTS`.** TDD is the opinionated default for this skill — auto runs are unsupervised, so tests are the only thing standing between a green stage transition and a regression that doesn't surface until the next session. Skip directly to `IMPLEMENT` **only when all three of these hold**:
   - Task `kind` is `chore`, `docs`, or `refactor-pure` (pure rename / move / formatting with no behavior change)
   - No observable behavior change in any code path
   - Existing tests already cover the surface being touched
   When in doubt, write the tests. If you skip, record the explicit reason ("skipped TDD: pure-refactor, behavior unchanged, existing coverage at <path>") in the Step 7 handover stub.

## Step 3: WRITE_TESTS

When `cursor.stage == "WRITE_TESTS"`:

1. Invoke `superpowers:test-driven-development`. Write failing tests that pin down the **acceptance criteria from the spec** — one test per observable behavior, not per function. Tests should fail for the right reason (the behavior doesn't exist yet), not because of a missing import or typo.
2. Run the full suite. Confirm:
   - Only the new tests fail.
   - Every previously-passing test still passes.
   If existing tests break before you've written a line of implementation, **stop** — either the spec is wrong about the surface, or the surface is unstable. Halt with `backlog_auto_complete_task(status="blocked", fail_reason="blocked", summary="existing tests broke during test-authoring; spec or surface needs review")`.
3. **Commit the failing tests as their own commit** with message `test: add failing tests for <task_id> <short title>`. The red→green transition must be visible in git history — this is a hard requirement, not a suggestion. Capture this sha; it goes in the final `commits` list at END_SESSION.
4. Call `backlog_auto_advance("IMPLEMENT")`.

## Step 4: IMPLEMENT

When `cursor.stage == "IMPLEMENT"`:

1. Do the work **against the failing tests from Step 3**. Re-run the test suite (or the relevant subset) after each logical chunk; the loop is red → impl → green, repeat until all Step-3 tests pass. Read files before editing, check for matched lessons before writing, follow existing project conventions.
2. **If you find yourself wanting to change a test to make it pass, stop.** Either the test was wrong (fix it deliberately, note the reason in the commit message — e.g. `test: correct expected behavior for X — original assertion contradicted spec line 42`) or the implementation is wrong. Do **not** silently relax assertions to get green.
3. **Reinforce lessons** that you actually applied: `backlog_lesson_reinforce(<lesson_id>)`. Skip lessons that didn't end up being relevant.
4. Commit logically — small, scoped commits per change. Capture all commit shas (including the test commit from Step 3) in a list (you'll pass them to `complete_task`).
5. Call `backlog_auto_advance("TEST")`.

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

1. Transition the task: `backlog_complete_task(<task_id>, ...)`. Pass session_title, done, decisions, issues, tasks_touched, target_status, patchnote, release per the end-session skill's signature. The session record is what the next start-session reads.
2. Call `backlog_snapshot(quiet=true)` to refresh the snapshot for the next session's `recap`. (The PreCompact hook covers compaction-triggered snapshots; this one covers task-completion-triggered ones.)
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
