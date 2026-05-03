---
name: end-session
description: "Close out a work session by logging what was accomplished. Invoke when the user says 'end session', 'I'm done for today', 'let's wrap up', 'log this work', 'mark this task done', or 'save progress'. Auto-generates Done/Decisions/Issues summary, transitions task status, commits tracking files. This is the ONLY correct way to mark tasks done or in-review with a session record."
---

# End Session

Log the current work session, transition tasks, and commit tracking files.

## Why This Skill Exists

`backlog_complete_task` atomically writes the changelog entry AND transitions the task status. If you use `backlog_update_task` directly to set status to "done", the PROGRESS.md changelog stays silent — the next `/start-session` will have no "last session" context, and the project loses its work history. This skill ensures every status transition comes with a session record.

**This is the ONLY way to mark tasks as done or in-review with proper session logging.**

## Steps

### v3 pre-steps (skip on v2 backlogs)

Check `backlog_status` for `schema_version`. If `>= 3`, run these BEFORE the existing flow:

**v3-pre-1: Snapshot.** Call `backlog_snapshot(quiet=true)` to capture pre-end-of-session state. This makes the next session's `backlog_recap` show what changed across the session boundary, not just from now to the next snapshot. Cheap (~50ms), no token cost.

**v3-pre-2: Handover offer.** Decide whether to offer a session handover. Auto-offer when ANY of:
   - Session length > 60 turns of conversation.
   - Conversation context estimate > 200k tokens.
   - A task is still in flight (status `in-progress` or `auto/state.json` cursor non-null).
   - User said anything like "for tomorrow", "remind me next time", "context handoff", "pick this up later".

If offering, ask:

   ```
   AskUserQuestion({
     questions: [{
       question: "Write a session handover? It captures decisions, blockers, and where to start next session.",
       header: "Handover",
       multiSelect: false,
       options: [
         { label: "Yes, end-of-day handover", description: "Standard wrap-up" },
         { label: "Yes, context handoff", description: "Near compaction — flag this as such" },
         { label: "Yes, milestone-complete", description: "Chunk done, next chunk ready to dispatch" },
         { label: "Skip", description: "Lightweight session, no handover needed" }
       ]
     }]
   })
   ```

If user picks yes, **invoke the `taskmaster:handover` skill** with the chosen `session_kind`. End-session does NOT draft the body itself — the handover skill owns tier selection, auto-extraction, and supersession chaining. End-session continues regardless of the handover skill's outcome.

### Existing flow

0. **Determine summary mode.** Check the session weight:
   - Count commits this session and files changed
   - If the session was **light** (1-2 commits, single-topic work, or user says "quick wrap"):
     - Use **auto-summary mode**: skip the structured Done/Decisions/Issues template
     - Generate a one-line summary from git: `git diff --stat HEAD~N` and commit messages
     - Call `backlog_complete_task` with `auto_summary=true` and pass the git stats as the `done` field
     - Format: "Files changed: N | +X -Y\nCommits: \"msg1\", \"msg2\""
   - If the session was **substantial** (3+ commits, multiple topics, design decisions made):
     - Use **structured mode** (the existing flow below)
   - The user can always override: "give me the full summary" forces structured mode

1. **Auto-generate session summary** by reviewing the current conversation:
   - **Done:** List of accomplishments (what was built, fixed, configured)
   - **Decisions:** Architectural or design choices made during the session
   - **Issues:** Problems encountered, unresolved items. If none, write "None"
   - **Tasks touched:** IDs of any tasks whose status changed this session

2. **Draft a user-facing patchnote (optional).** If the task has meaningful user impact (new feature, visible UX change, fixed bug a user would notice), draft a 1-2 sentence patchnote in the user's voice — not the internal title. Skip for internal/infra/cleanup/refactor tasks (leave blank). Examples:
   - ✅ "Interactive clarification overlay — multi-question queue with option chips and a single SUBMIT ALL action."
   - ✅ "Release-notes pipeline now aggregates patchnotes per release bucket."
   - ❌ (skip) Refactor of `_load()` helper, CI config tweak, dependency bump.

   Also pick a **release bucket** (`pre-alpha`, `alpha-1.0`, …) if the project uses them — ask the user if unclear. Patchnotes without a release tag are still stored but won't surface in `backlog_release_notes` unless `include_unreleased=true`.

3. **Generate session title:** `{Topic}: {Brief Description}` (don't include the date — the server auto-prefixes with today's date)

4. **Determine target status.** Ask the user:

   > "Does this task need manual testing before it's considered done?"
   > - **Yes → `in-review`** (implementation complete, you need to test/confirm)
   > - **No → `done`** (no manual testing needed, or you've already confirmed)

   Default to `in-review` when unsure — it's better to have the user explicitly confirm than to silently skip testing.

5. **Present the draft summary** for review. Include the target status AND the drafted patchnote (if any). Ask: "Does this look right? Edit anything or say 'looks good'."

6. **After user approval — call `backlog_complete_task` with all session fields:**

   ```
   backlog_complete_task(
       task_id="...",
       session_title="Topic: Brief Description",
       done="item 1\nitem 2\nitem 3",
       decisions="decision 1\ndecision 2",
       issues="None",
       tasks_touched="task-001, task-002",
       target_status="in-review",  # or "done"
       patchnote="Interactive clarification overlay — ...",  # omit or "" for internal tasks
       release="pre-alpha",  # omit or "" if project doesn't use release buckets
   )
   ```

   For OTHER tasks that also need transitions (not the primary task):
   - Use `backlog_update_task` for individual status changes — these won't get changelog entries, which is fine for secondary tasks.

7. **Worktree cleanup (done tasks only):**
   - If the task was marked **done** and has a worktree, offer cleanup:
     "Clean up the worktree for `{task_id}`? This removes the isolated working directory."
   - If confirmed: `git worktree remove .worktrees/{task-id}`, then `backlog_update_task(task_id, "worktree", "")`
   - If declined: leave it — the user may want to reference it.
   - **Skip for `in-review` tasks** — the user still needs the worktree for testing.

8. **Commit tracking files:**
   ```bash
   git add backlog.yaml PROGRESS.md
   git commit -m "chore: log session — {brief topic}

   {1-line summary}"
   ```

   **(v3) Also stage these directories if they have changes:**
   - `.claude/handovers/` (handovers written this session)
   - `.taskmaster/issues/` (issues created or updated)
   - `.taskmaster/lessons/` (lessons reinforced — last_reinforced field updates)
   - `.taskmaster/tasks/` (per-task body updates from spec-review or notes)

   Do NOT stage `.taskmaster/snapshots/` or `.taskmaster/auto/` — both are gitignored. If git complains they're tracked, that's a misconfiguration the user should fix; do not work around it.

9. **Confirm:** "Session logged and committed. Task is now `{target_status}`."

## Edge Cases

- **No in-progress task:** If the session was exploratory (planning, research, no task picked), you can still log a session by calling `backlog_complete_task` on any task that changed, or skip the tool and just manually append to PROGRESS.md. Ask the user what they'd prefer.
- **Not in a git repo:** Skip step 8 (commit) and tell the user the tracking files were updated but not committed.
- **Multiple tasks changed:** Use `backlog_complete_task` for the primary task (gets the full changelog entry), and `backlog_update_task` for secondary status changes.

## Task Lifecycle

See `references/task-lifecycle.md` for the full state machine. Key point: `in-review` means "Claude is done, user tests now." `done` means "user confirmed it works."

## Auto-mode interaction (v3)

If `backlog_auto_status` reports an active run, do NOT call `backlog_complete_task` directly — that's the auto-task skill's job at its END_SESSION stage. Instead, defer to the auto run:
- If auto-task is currently driving the session, end-session is being called as part of that flow. Proceed with the v3-pre-steps above (snapshot + handover) and otherwise let auto-task handle the task transition.
- If the user invokes /end-session manually mid-auto-run, ask: "There's an active auto run on `<target>`. Pause and write a handover, or abort the run?" — `backlog_auto_abort` clears the state, invoking `taskmaster:handover` with `session_kind="context-handoff"` preserves it.
