# End-Session — Summary Modes and Status Decision

## Step 0: Determine Summary Mode

Check the session weight:

- Count commits this session and files changed.
- If the session was **light** (1-2 commits, single-topic work, or user says "quick wrap"):
  - Use **auto-summary mode**: skip the structured Done/Decisions/Issues template.
  - Generate a one-line summary from git: `git diff --stat HEAD~N` and commit messages.
  - Call `backlog_complete_task` with `auto_summary=true` and pass the git stats as the `done` field.
  - Format: "Files changed: N | +X -Y\nCommits: \"msg1\", \"msg2\""
- If the session was **substantial** (3+ commits, multiple topics, design decisions made):
  - Use **structured mode** (the existing steps 1-9 flow).
- The user can always override: "give me the full summary" forces structured mode.

## Step 2: Draft Patchnote

If the task has meaningful user impact (new feature, visible UX change, fixed bug a user would notice), draft a 1-2 sentence patchnote in the user's voice. Skip for internal/infra/cleanup/refactor tasks (leave blank).

Examples:
- "Interactive clarification overlay - multi-question queue with option chips and a single SUBMIT ALL action."
- "Release-notes pipeline now aggregates patchnotes per release bucket."
- (skip) Refactor of _load() helper, CI config tweak, dependency bump.

Also pick a **release bucket** (pre-alpha, alpha-1.0, ...) if the project uses them - ask the user if unclear.

## Step 4: Target Status Decision Rules

**Default:** silently target `in-review` - the user tests and marks `done` later.

**Override to `done` when conversation context clearly indicates one of:**
- User already confirmed manual testing during the session ("I tested it", "works on my end")
- Task has no testable surface (pure infra/cleanup/docs/config bump)
- User explicitly says "mark done" / "this is done"

Do not ask "does this need manual testing?" - that's a confirmation gate on a decision the default already handles. The user can transition `in-review -> done` later with a one-liner.

## v3-post-complete-1: Issue-Close-on-Task-Complete Hook

After `backlog_complete_task` succeeds, check whether the just-completed task has any `related_issues`. For each issue whose current `status` is `open` or `investigating`, prompt:

> "ISS-XXX is still open - close it as fixed in task_id, or leave for follow-up?"

| Choice | Action |
|---|---|
| Close as fixed | `backlog_issue_update(issue_id="ISS-XXX", status="fixed", fixed_in_task="<task_id>")` |
| Leave for follow-up | No tool call - issue remains open |

If the task has no `related_issues`, or all related issues are already fixed/closed/wont-fix, skip this sub-step silently.

If multiple issues are open, prompt for each in sequence.
