---
name: end-session
description: "Close out a work session. Invoke when the user says 'end session', 'I'm done for today', 'let's wrap up', 'mark this task done', or 'save progress'. This is the ONLY correct way to mark tasks done or in-review with a session record."
---

# End Session

Log the current work session, transition tasks, and commit tracking files.

## Why This Skill Exists

`backlog_complete_task` atomically writes the changelog entry AND transitions the task status. Calling `backlog_update_task` directly to set status to "done" leaves PROGRESS.md silent. **This is the ONLY correct way to mark tasks done or in-review with backlog_complete_task.**

## Steps

### v3 Pre-Steps (skip on v2)

Check schema: `backlog_status` first line shows `Schema: v<N>`.

**v3-pre-1: Snapshot.** `backlog_snapshot(quiet=true)`.

**v3-pre-2a: Lesson candidate sweep.** Auto-offer when: `<lesson-candidate>` tags visible, or `backlog_lesson_candidates_list` returns entries, or 2+ feedback-memory entries for this project. Full flow: `references/v3-pre-steps.md`.

**v3-pre-1b: Decision sweep** (before handover write). Call `backlog_decision_list(status="open", task_id=<current>)`. For each open decision: ask to carry forward / resolve now / drop. The resulting open / resolved sets are folded into the handover **body** under "Open decisions" / "Resolved this session" sections — they are not separate kwargs to `backlog_handover_create`. Full flow: `references/v3-pre-steps.md`.

**v3-pre-2: Handover auto-write.** Write automatically (no prompt) when: session >60 turns, context >200k tokens, task still in-flight, or user said "for tomorrow" / "context handoff". Infer `session_kind` and invoke `taskmaster:handover`. Full flow: `references/v3-pre-steps.md`.

**v3-pre-2b: Handover archive sweep.** `backlog_handover_resync()` quietly.

**v3-pre-2c: Idea-candidate sweep.** Scan for `<idea-candidate>` tags; commit each via `backlog_idea_create`. Full flow: `references/v3-pre-steps.md`.

**v3-pre-2d: Loose thoughts → desk note (optional, max one).** If the session leaves a genuinely situational thought that fits no entity (not a task, idea, issue, or the handover's next_action) — write at most ONE consolidated note via `backlog_note_create(text=...)`. Default is none. Never duplicate handover content into a note; the note is for what would otherwise be lost.

### Existing Flow

**0. Summary mode.** Light session (1-2 commits, single-topic) -> auto-summary. Substantial (3+ commits) -> structured. See `references/summary-modes.md`.

**1. Auto-generate summary.** Done / Decisions / Issues / Tasks touched from conversation context.

**2. Patchnote (optional).** 1-2 sentences for user-visible changes. Skip for internal tasks. See `references/summary-modes.md`.

**3. Session title.** `{Topic}: {Brief Description}`.

**4. Target status.** Default `in-review`. Override to `done` when: user confirmed testing, pure infra task, or user says "mark done". See `references/summary-modes.md`.

**5. Skip review gate.** Call `backlog_complete_task` directly. Only ask on genuine ambiguity.

**5b. Bug close-gate.** Before transitioning the task, query open Bugs linked via `found_in`:

```
backlog_bug_list(status="open", found_in="<active-task-id>")
```

If the result is non-empty:

> "We're wrapping up **T-XXX** but it still has **N** open bug(s) linked via `found_in`:
> - B-NNN — title
> - B-MMM — title
>
> Resolve each before closing. For each open bug, pick a disposition: fix-now / spawn-task / shelve / promote."

Walk the disposition entry point in `taskmaster:bug` for each open bug. Only proceed to task transition when all linked bugs are non-`open`.

If the task has any `fixed` linked bugs (status=fixed, found_in=task), mention that N bug(s) will be archived to `bugs/archive/` automatically on task close.

Note: `backlog_complete_task` enforces this server-side too — the skill just gives the user the chance to resolve interactively before hitting the server gate.

**6. Call `backlog_complete_task`** with all session fields (task_id, session_title, done, decisions, issues, tasks_touched, target_status, patchnote, release).

**v3-post-complete-1.** For each `related_issues` that is open/investigating: ask user "Close as fixed or leave for follow-up?"

**7. Worktree cleanup (done tasks only).** Offer `git worktree remove .worktrees/{task_id}`. Skip for in-review.

**8. Commit tracking files.** Stage backlog.yaml, PROGRESS.md, .taskmaster/handovers/, issues/, lessons/, tasks/. Commit with `chore: log session - {topic}`.

**9. Confirm.** "Session logged. Task is now `{target_status}`."

## Task Lifecycle

`todo -> in-progress -> in-review -> done -> archived`. In-review = Claude done, user tests. Done = user confirmed.

## Additional Resources

- `references/v3-pre-steps.md` - full v3 pre-step flows (lesson sweep, handover auto-write, idea sweep).
- `references/summary-modes.md` - light vs structured mode, patchnote format, status decision rules.
- `references/edge-cases.md` - no in-progress task, not in git repo, multiple tasks changed.
- `references/auto-mode.md` - behavior when `backlog_auto_status` reports an active run.
