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

2. **Generate session title:** `{Topic}: {Brief Description}` (don't include the date — the server auto-prefixes with today's date)

3. **Determine target status.** Ask the user:

   > "Does this task need manual testing before it's considered done?"
   > - **Yes → `in-review`** (implementation complete, you need to test/confirm)
   > - **No → `done`** (no manual testing needed, or you've already confirmed)

   Default to `in-review` when unsure — it's better to have the user explicitly confirm than to silently skip testing.

4. **Present the draft summary** for review. Include the target status. Ask: "Does this look right? Edit anything or say 'looks good'."

5. **After user approval — call `backlog_complete_task` with all session fields:**

   ```
   backlog_complete_task(
       task_id="...",
       session_title="Topic: Brief Description",
       done="item 1\nitem 2\nitem 3",
       decisions="decision 1\ndecision 2",
       issues="None",
       tasks_touched="task-001, task-002",
       target_status="in-review"  # or "done"
   )
   ```

   For OTHER tasks that also need transitions (not the primary task):
   - Use `backlog_update_task` for individual status changes — these won't get changelog entries, which is fine for secondary tasks.

6. **Worktree cleanup (done tasks only):**
   - If the task was marked **done** and has a worktree, offer cleanup:
     "Clean up the worktree for `{task_id}`? This removes the isolated working directory."
   - If confirmed: `git worktree remove .worktrees/{task-id}`, then `backlog_update_task(task_id, "worktree", "")`
   - If declined: leave it — the user may want to reference it.
   - **Skip for `in-review` tasks** — the user still needs the worktree for testing.

7. **Commit tracking files:**
   ```bash
   git add backlog.yaml PROGRESS.md
   git commit -m "chore: log session — {brief topic}

   {1-line summary}"
   ```

8. **Confirm:** "Session logged and committed. Task is now `{target_status}`."

## Edge Cases

- **No in-progress task:** If the session was exploratory (planning, research, no task picked), you can still log a session by calling `backlog_complete_task` on any task that changed, or skip the tool and just manually append to PROGRESS.md. Ask the user what they'd prefer.
- **Not in a git repo:** Skip step 7 (commit) and tell the user the tracking files were updated but not committed.
- **Multiple tasks changed:** Use `backlog_complete_task` for the primary task (gets the full changelog entry), and `backlog_update_task` for secondary status changes.

## Task Lifecycle

See `references/task-lifecycle.md` for the full state machine. Key point: `in-review` means "Claude is done, user tests now." `done` means "user confirmed it works."
