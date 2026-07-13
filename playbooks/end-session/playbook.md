# End Session

Log the current work session, transition tasks, and commit tracking files.

## Why This Skill Exists

`backlog_complete_task` atomically writes the changelog entry AND transitions the task status. Calling `backlog_update_task` directly to set status to "done" leaves PROGRESS.md silent. **This is the ONLY correct way to mark tasks done or in-review with backlog_complete_task.**

## Steps

### v3 Pre-Steps (skip on v2)

Check schema: `backlog_status` first line shows `Schema: v<N>`.

**v3-pre-1: Decision sweep** (before handover write). Call `backlog_decision(action="list", status="open", task_id=<current>)`. For each open decision: ask to carry forward / resolve now / drop. The resulting open / resolved sets are folded into the handover **body** under "Open decisions" / "Resolved this session" sections — they are not separate kwargs to `backlog_handover_create`. Full flow: `references/v3-pre-steps.md`.

**v3-pre-2: Handover auto-write.** Write automatically (no prompt) when: session >60 turns, context >200k tokens, task still in-flight, or user said "for tomorrow" / "context handoff". Infer `session_kind` and invoke `taskmaster:handover`. Full flow: `references/v3-pre-steps.md`.

**v3-pre-2a: Instruction-file candidate check.** Ask once: did this session surface knowledge that must bind ALL assistants here ("we always do X", "Y breaks if you do Z")? If yes, propose a 1-3 line addition to the repo's instruction file (CLAUDE.md / AGENTS.md) on user approval. Session-only insights go to your own memory instead. Most sessions produce neither; skip silently.

**v3-pre-2b: Handover archive sweep.** `backlog_handover_resync()` quietly.

**v3-pre-2c: Idea-candidate sweep.** Scan for `<idea-candidate>` tags; commit each via `backlog_idea_create`. Full flow: `references/v3-pre-steps.md`.

**v3-pre-2d: Loose thoughts → desk note (optional, max one).** If the session leaves a genuinely situational thought that fits no entity (not a task, idea, issue, or the handover's next_action) — write at most ONE consolidated note via `backlog_note(action="create", text=...)`. Default is none. Never duplicate handover content into a note; the note is for what would otherwise be lost.

### Existing Flow

**0. Summary mode.** Light session (1-2 commits, single-topic) -> auto-summary. Substantial (3+ commits) -> structured. See `references/summary-modes.md`.

**1. Auto-generate summary.** Done / Decisions / Issues / Tasks touched from conversation context.

**2. Patchnote (optional).** 1-2 sentences for user-visible changes. Skip for internal tasks. See `references/summary-modes.md`.

**3. Session title.** `{Topic}: {Brief Description}`.

**4. Target status.** Default `done` — Claude complete + gates passed. Target `in-review` ONLY when an action that only the human can perform blocks the task (API key, LLM config, account access); pass it as `human_action` (short imperative, e.g. "add OPENAI_API_KEY to .env") — `backlog_complete_task` rejects in-review without it. See `references/summary-modes.md`.

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

Note: `backlog_complete_task` enforces this server-side too.

**6. Call `backlog_complete_task`.** Two paths:

- **Single task (default):** `backlog_complete_task(task_id, session_title, done, decisions, issues, tasks_touched, target_status, patchnote, release, human_action)`.
- **Bundle:** Call `_get_session_bundle()` first. If a bundle is active (slug, members, …), call `backlog_complete_task(member, …)` for **each passing member** — every member gets its own completion record. Do NOT use `backlog_update_task` status-only for bundle members (see `references/edge-cases.md` §Bundle).

**6b. Merge fan-out (bundle only).** On a single merge event, loop `backlog_record_merge(member, rung, sha)` over all bundle members from `_get_session_bundle()` — same rung and sha for each.

**v3-post-complete-1.** For each `related_issues` that is open/investigating: ask user "Close as fixed or leave for follow-up?"

**7. Worktree cleanup (done tasks only).** Skip for in-review.

- **Single task:** Offer `git worktree remove .worktrees/{task_id}`.
- **Bundle:** Offer `git worktree remove .worktrees/{slug}` **only when all members are `done` or descoped** — check `_get_session_bundle()` membership first. Never use `--force` or `rm -rf` (guard-hooks blocks `--force` by default).

**8. Commit tracking files.** Stage backlog.yaml, PROGRESS.md, .taskmaster/handovers/, issues/, tasks/. Commit with `chore: log session - {topic}`.

**9. Confirm.** "Session logged. Task is now `{target_status}`." If a handover was written this session, end with its resume line verbatim: `Resume: <thread> — <next_action>`.

## Task Lifecycle

`todo -> in-progress -> in-review -> done -> archived`. In-review = blocked on a human-only action (`human_action` says what). Done = Claude complete + gates passed. Human review of shipped work happens downstream, not on the board.

## Additional Resources

- `references/v3-pre-steps.md` - full v3 pre-step flows (handover auto-write, idea sweep).
- `references/summary-modes.md` - light vs structured mode, patchnote format, status decision rules.
- `references/edge-cases.md` - no in-progress task, not in git repo, multiple tasks changed.
