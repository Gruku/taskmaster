# Smoke-test checklist — taskmaster 3.0.0 (v3)

This is the user-driven companion to `tests/test_e2e_v3_smoke.py`. The test
exercises the programmatic surface; this checklist covers what only a real
install + Claude Code session + browser can verify.

**Pre-requisite:** taskmaster 3.0.0 installed via `/plugin install gruku-tools/taskmaster`
(or marketplace install). Restart Claude Code after install.

## 1. Fresh project, v2 init → v3 migration

In a fresh empty directory:

- [ ] Open Claude Code in the directory
- [ ] Say "set up taskmaster" → `init-taskmaster` skill triggers
- [ ] Pick `Hidden` location, `v2 (Default)` schema, `Clean start` mode
- [ ] Verify `.claude/backlog.yaml` exists and is v2 (no `schema_version` key)
- [ ] Add an epic + 3 tasks via the skill flow
- [ ] Say "migrate to v3" → `migrate-v3` skill triggers
- [ ] Verify pre-flight summary shows correct task counts and reversibility note
- [ ] Pick `Migrate` in the AskUserQuestion
- [ ] Verify `.taskmaster/tasks/` exists with per-task files
- [ ] Verify post-flight prompts about `.gitignore` additions for `.taskmaster/snapshots/` and `.taskmaster/auto/`

## 2. Fresh v3 init (no migration)

In a fresh empty directory:

- [ ] Say "set up taskmaster"
- [ ] Pick `Hidden`, `v3 (Narrative continuity)`, `Clean start`
- [ ] Verify backlog is v3 from the start
- [ ] Verify `.taskmaster/snapshots/` and `.taskmaster/auto/` are in `.gitignore`
- [ ] Verify the v3 capabilities tour message appears

## 3. Handover skill — write/read/continue

- [ ] Say "write a handover" → `handover` skill triggers
- [ ] Confirm draft is shown with auto-extracted file list, decisions, next-action
- [ ] Approve write → file appears under `.claude/handovers/<id>.md`
- [ ] In a NEW Claude Code session, say "what should I work on" → `start-session` triggers
- [ ] Verify the briefing leads with "Where you left off:" referencing the handover
- [ ] Say "continue this task" → `pick-task` auto-resolves to the handover's first task

## 4. Lesson skill — write/match/reinforce

- [ ] Say "remember this: never edit auto/state.json directly" → `lesson` skill triggers
- [ ] Approve the lesson with kind=`gotcha`, trigger_files glob like `auto/*.json`
- [ ] Verify lesson file appears under `.taskmaster/lessons/`
- [ ] Pick a task whose anchors include `auto/`
- [ ] Verify `pick-task` step 5c surfaces the matching lesson
- [ ] Mid-work, emit `<lesson-candidate scope="session">` inline → end-session offers candidate review

## 5. Issue skill — log/transition/close

- [ ] Say "log a bug: login form accepts whitespace password, P1" → `issue` skill triggers
- [ ] Verify severity decision flow + frontmatter capture (impact, components, location)
- [ ] Approve write → file at `.taskmaster/issues/ISS-NNN.md`
- [ ] Say "investigating ISS-NNN" → status moves to `investigating`
- [ ] Try "mark ISS-NNN fixed" without naming a task → skill prompts for `fixed_in_task`
- [ ] Pick a task that has `related_issues: [ISS-NNN]`, complete it
- [ ] Verify end-session's v3-post-complete-1 hook prompts to close the issue

## 6. Recap + snapshot

- [ ] Run `backlog_snapshot` once
- [ ] Add a new task, transition another to in-progress
- [ ] Run `backlog_recap` → verify diff shows new task + status change
- [ ] Trigger context compaction (if possible) → verify a snapshot was written by the PreCompact hook (check timestamp on `.taskmaster/snapshots/last.json`)

## 7. Auto-mode — single-task lifecycle

- [ ] Pick a small low-risk task
- [ ] Say "auto this task" → `auto-task` skill drives PICK → SPEC_REVIEW → IMPLEMENT → TEST → REVIEW_GATE → HANDOVER_STUB → END_SESSION
- [ ] Verify gates fire (plan approval, review approval) when `no_gate=false`
- [ ] Verify auto-stage handover is written and linked in the auto state record
- [ ] Verify task transitions to `done` and PROGRESS.md gets a session entry

## 8. Viewer — kanban + edit-in-UI

Open `backlog_open_viewer`:

- [ ] Kanban renders with todo / in-progress / in-review / done columns; tasks visible
- [ ] Drag a task between columns → status updates server-side, `started`/`completed` auto-stamp
- [ ] Click `+ Task` → entity modal opens with all 20 editable fields
- [ ] Click `✎ Edit` on a task detail → modal pre-fills with current values
- [ ] Click an inline field (title, status, priority, spec, plan, notes) on Task Detail → 600ms autosave
- [ ] Open the same task in two tabs, edit both → verify conflict banner with Keep-mine / Use-server choices
- [ ] Try to set status to "doneish" (invalid) → verify 422 with structured error message

## 9. Migration safety

- [ ] On a v3 backlog, run `backlog_migrate_v3` again → verify "already on v3" no-op response
- [ ] On a v3 backlog, `git restore .claude/backlog.yaml && rm -rf .taskmaster/tasks/` → verify backlog is back to v2 form (rollback works)

## 10. No regressions vs 1.11

- [ ] Existing v2 backlogs (any project still on the `1.x` line) continue to work without changes
- [ ] `start-session`, `pick-task`, `end-session`, `review-gate` all behave as in 1.11 on v2
- [ ] No new MCP errors, no missing tools, no stack traces in the MCP server log

---

## Reporting

If any item fails, file as a `taskmaster:issue` (severity per impact) with
the failure details. The 3.0.0 release should be considered "verified
operational" only when all 10 sections check out.
