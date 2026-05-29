---
name: auto-task
description: "Drive a single task through the full lifecycle (PICK -> SPEC_REVIEW -> WRITE_TESTS -> IMPLEMENT -> TEST -> REVIEW_GATE -> HANDOVER_STUB -> END_SESSION) using the auto state machine. Invoke when the user says 'auto T-001', 'autopilot this task', 'run task auto', or when called by auto-epic/auto-phase as a subagent recipe."
---

# Auto-Task — single-task lifecycle driver

This skill drives **one** task through every stage of work, persisting cursor state between stages so a crash or compaction doesn't lose progress. It is the inner loop for `auto-epic` and `auto-phase`.

> **Critical:** Never edit `backlog.yaml` or `auto/state.json` manually — every stage transition must go through `backlog_auto_advance` so the cursor stays consistent.

> **Gate recording:** `backlog_auto_advance` auto-records the gate for each stage (as of the lane-gate foundation). Do NOT record gates manually inside this skill. On each stage transition the response includes `gate_state`; surface `backlog_task_pipeline(task_id)` if a gate is outstanding or blocked.

## Step 0: Establish or resume state

Run `backlog_auto_status`. If "No auto run in progress" and user gave a task id: `backlog_auto_start(mode="task", target=<id>)`. If run in progress: read cursor (`task_id`, `stage`, `model`) and resume from `cursor.stage`. If cursor is None: `backlog_auto_finish` and stop.

## Step 1: PICK

1. `backlog_get_task(<task_id>)` — load task frontmatter + body.
2. `backlog_handover_list(task_id=<task_id>, limit=3)` — get tldrs; only fetch full body if `session_kind=context-handoff` AND `next_action` is non-trivial.
3. `backlog_lesson_match(task_title=<title>, touched_files=<task.anchors>)` + `backlog_lesson_get` for each match. Cap 3 hits.
4. Surface open `related_issues` P0/P1 entries via `backlog_issue_get`.
5. `backlog_update_task(<task_id>, "status", "in-progress")`.
6. `backlog_auto_advance("SPEC_REVIEW")`.

## Step 2: SPEC_REVIEW (gated)

1. If no `## Spec / Plan` section: draft one (use `superpowers:writing-plans` for non-trivial tasks).
2. If `no_gate=false` (default): present via `AskUserQuestion` (Approve / Revise / Reject). Reject -> `backlog_auto_complete_task(status="failed", fail_reason="spec-rejected")`.
3. If `no_gate=true`: skip gate, emit warning in handover stub.
4. Persist approved spec to `tasks/<task_id>.md` `## Spec / Plan` section via `taskmaster:spec-review` step 7a.
5. **Default: advance to WRITE_TESTS.** Skip to IMPLEMENT only when: task `kind` is `chore`/`docs`/`refactor-pure` AND no behavior change AND existing tests cover the surface. When skipping, record explicit reason in Step 7 handover stub.

## Step 3: WRITE_TESTS

1. Invoke `superpowers:test-driven-development`. Write failing tests for acceptance criteria — one test per observable behavior.
2. Run full suite. Confirm only new tests fail; all prior tests pass. If prior tests break: halt with `backlog_auto_complete_task(status="blocked", fail_reason="blocked", summary="...")`.
3. Commit failing tests: `test: add failing tests for <task_id> <short title>`. Capture this sha.
4. `backlog_auto_advance("IMPLEMENT")`.

## Step 4: IMPLEMENT

1. Work against the failing tests. Loop: red -> impl -> green. Read files before editing; check matched lessons.
2. Never change a test to make it pass. Fix deliberately if test was wrong; note reason in commit message.
3. Reinforce lessons that applied: `backlog_lesson_reinforce(<lesson_id>)`.
4. Commit logically. Capture all commit shas.
5. `backlog_auto_advance("TEST")`.

## Step 5: TEST

1. Run full test suite. Capture stdout/stderr.
2. If tests fail: `backlog_auto_complete_task(status="failed", fail_reason="tests-failed", summary="...")`. Stop.
3. If tests pass: `backlog_auto_advance("REVIEW_GATE")`.

## Step 6: REVIEW_GATE (gated)

1. Run `taskmaster:review-gate` on the diff.
2. If `no_gate=false`: gate on user approval.
3. Approved: `backlog_auto_advance("HANDOVER_STUB")`. Rejected: `backlog_auto_complete_task(status="failed", ...)`.

## Step 7: HANDOVER_STUB

Write a brief recovery-anchor handover (<200 words):
```
backlog_handover_create(
  tldr="<what was completed>", next_action="<what comes next>",
  task_ids=[<task_id>], session_kind="auto-stage",
  body="## Decisions\n- ...\n## Notes\n- commits, files touched, lesson reinforcements"
)
```
Capture the returned handover id. Then `backlog_auto_advance("END_SESSION")`.

## Step 8: END_SESSION

1. `backlog_complete_task(<task_id>, session_title, done, decisions, issues, tasks_touched, target_status, patchnote, release)`.
2. `backlog_snapshot(quiet=true)`.
3. `backlog_auto_complete_task(status="done", summary="...", commits=[...], handover_id="...")`. Advances cursor to next task.
4. If cursor is now None: run is complete. The orchestrator calls `backlog_auto_finish` — do NOT call it from inside this skill.
5. Return: `{ task_id, status, summary, commits, handover_id, model_used }`.

## Additional Resources

- `references/recovery.md` — failure escape hatches and resume semantics.
- `references/scope-and-budget.md` — what auto-task does NOT do and per-stage token discipline limits.
