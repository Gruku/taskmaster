---
name: auto-task
description: "Drive a single task through its LANE-specific lifecycle (express / standard / full) using the auto state machine. Invoke when the user says 'auto T-001', 'autopilot this task', 'run task auto', or when called by auto-epic/auto-phase as a subagent recipe."
---

# Auto-Task — single-task lifecycle driver

This skill drives **one** task through every stage of work, persisting cursor state between stages so a crash or compaction doesn't lose progress. It is the inner loop for `auto-epic` and `auto-phase`.

The stage sequence is **lane-specific** — do NOT walk a fixed list. Each lane has its own pipeline (read it from `backlog_auto_status` → **Pipeline**): **express** is lean (PICK → IMPLEMENT → TEST → REVIEW_GATE → …, no spec/reviews/tests gate); **standard** (default) adds SPEC + DESIGN_REVIEW + WRITE_TESTS (blocking gate is **design-review**, not spec-review); **full** adds SPEC_REVIEW + PLAN + PLAN_REVIEW (adds **plan-review**).

> **Critical:** Never edit `backlog.yaml` or `auto/state.json` manually — every transition must go through `backlog_auto_advance` so the cursor stays consistent.

> **Gate recording:** `backlog_auto_advance` auto-records each stage's gate. Do NOT record gates manually. Walking the lane sequence in order records exactly the gates that lane requires, so completion is unblocked.

## Step 0: Establish or resume state

Run `backlog_auto_status`. If "No auto run in progress" and user gave a task id: `backlog_auto_start(mode="task", target=<id>)`. The status/start output lists the lane **Pipeline** and the **Next** stage. If run in progress: read cursor and resume from `cursor.stage`. If cursor is None: `backlog_auto_finish` and stop.

## The walk: step the lane pipeline

Drive the cursor with **`backlog_auto_advance()` (NO stage argument)** — this moves to the NEXT stage in the task's lane Pipeline and auto-records its gate. Repeat until the response says `Pipeline complete`, then do END_SESSION (Step 8). Re-read `backlog_auto_status` whenever unsure which stage is current. Use an explicit `backlog_auto_advance("STAGE")` only to deviate (e.g. an early halt).

For each stage the walk lands on, run the matching handler below (stages a lane skips simply never appear):

## PICK

1. `backlog_get_task(<task_id>)` — load frontmatter + body.
2. `backlog_handover_list(task_id=<task_id>, limit=3)` — get tldrs; only fetch full body if `session_kind=context-handoff` AND `next_action` is non-trivial.
3. `backlog_lesson_match(task_title=<title>, touched_files=<task.anchors>)` + `backlog_lesson_get` per match. Cap 3 hits.
4. Surface open `related_issues` P0/P1 entries via `backlog_issue_get`.
5. `backlog_update_task(<task_id>, "status", "in-progress")`, then `backlog_auto_advance()`.

## SPEC / PLAN (standard & full)

Draft the `## Spec / Plan` section if missing (use `superpowers:writing-plans` for non-trivial tasks); persist to `tasks/<task_id>.md`. PLAN (full lane only) is the implementation plan. `backlog_auto_advance()`.

## SPEC_REVIEW / PLAN_REVIEW / DESIGN_REVIEW (gated)

The lane's blocking review gates (full → spec-review + plan-review; standard → design-review). Run the matching review (`taskmaster:spec-review` for SPEC_REVIEW/DESIGN_REVIEW, `taskmaster:plan-review` for PLAN_REVIEW). If `no_gate=false` (default): present via `AskUserQuestion` (Approve / Revise / Reject); Reject → `backlog_auto_complete_task(status="failed", fail_reason="spec-rejected")`. If `no_gate=true`: skip, warn in the handover stub. Then `backlog_auto_advance()`.

## WRITE_TESTS

1. Invoke `superpowers:test-driven-development`. Write failing tests for acceptance criteria — one test per observable behavior.
2. Run full suite. Confirm only new tests fail; all prior tests pass. If prior tests break: halt with `backlog_auto_complete_task(status="blocked", fail_reason="blocked", summary="...")`.
3. Commit failing tests: `test: add failing tests for <task_id> <short title>`. Capture this sha.
4. `backlog_auto_advance()`.

(express skips SPEC/reviews/WRITE_TESTS — its walk is PICK → IMPLEMENT.)

## IMPLEMENT

1. Work against the failing tests. Loop: red → impl → green. Read files before editing; check matched lessons.
2. Never change a test to make it pass. Fix deliberately if a test was wrong; note reason in the commit message.
3. Reinforce lessons that applied: `backlog_lesson_reinforce(<lesson_id>)`.
4. Commit logically. Capture all commit shas. `backlog_auto_advance()`.

## TEST

1. Run full test suite. Capture stdout/stderr.
2. If tests fail: `backlog_auto_complete_task(status="failed", fail_reason="tests-failed", summary="...")`. Stop.
3. If tests pass: `backlog_auto_advance()`.

## REVIEW_GATE (gated)

1. Run `taskmaster:review-gate` on the diff.
2. If `no_gate=false`: gate on user approval. Rejected → `backlog_auto_complete_task(status="failed", ...)`.
3. Approved: `backlog_auto_advance()`.

## HANDOVER_STUB

Write a brief recovery-anchor handover (<200 words):
```
backlog_handover_create(
  tldr="<what was completed>", next_action="<what comes next>",
  task_ids=[<task_id>], session_kind="auto-stage",
  body="## Decisions\n- ...\n## Notes\n- commits, files touched, lesson reinforcements"
)
```
Capture the returned handover id. Then `backlog_auto_advance()`.

## END_SESSION

1. `backlog_complete_task(<task_id>, session_title, done, decisions, issues, tasks_touched, target_status, patchnote, release)`.
2. `backlog_snapshot(quiet=true)`.
3. `backlog_auto_complete_task(status="done", summary="...", commits=[...], handover_id="...")`. Advances cursor to next task.
4. If cursor is now None: run is complete. The orchestrator calls `backlog_auto_finish` — do NOT call it from inside this skill.
5. Return: `{ task_id, status, summary, commits, handover_id, model_used }`.

## Additional Resources

- `references/recovery.md` — failure escape hatches and resume semantics.
- `references/scope-and-budget.md` — what auto-task does NOT do and per-stage token discipline limits.
