# End-Session: summary mode, status and related issues

Read this when filling the close summary (playbook Close steps 2-3) and after `backlog_complete_task` succeeds (step 5).

## Summary mode

Judge the session's weight from the commits made and files changed:

- **Light** (1-2 commits, single-topic work, or the user says "quick wrap"): pass `auto_summary=true` to `backlog_complete_task` and the git stats as `done`, taken from `git diff --stat HEAD~N` and the commit messages. Format: "Files changed: N | +X -Y" then a line of the commit messages.
- **Substantial** (3+ commits, multiple topics, design decisions made): fill `done`, `decisions`, `issues` and `tasks_touched` yourself.
- "Give me the full summary" from the user forces the substantial form.

## Patchnote and release

When the task has user impact (a new feature, a visible UX change, a fixed bug a user would notice), draft a 1-2 sentence `patchnote` in the user's voice. Leave it blank for internal, infra, cleanup and refactor tasks.

- "Interactive clarification overlay - multi-question queue with option chips and a single SUBMIT ALL action." (patchnote)
- A refactor of a helper, a CI tweak, a dependency bump: no patchnote.

Pass a `release` bucket (pre-alpha, alpha-1.0, ...) if the project uses them; when it is unclear which, ask.

## Target status

Default `done`: the work is finished and the lane's gates passed, so there is nothing to ask. Target `in-review` only when a human-only action blocks the task: add an API key, set LLM config, grant account access, a physical-world step. Pass it as `human_action` (a short imperative: "add OPENAI_API_KEY to .env"); `backlog_complete_task` rejects in-review without one. "The user should look at this" is not in-review, because done work gets reviewed off the board and the user can reopen it with a one-liner.

## Related issues

After `backlog_complete_task` succeeds, find the open issues linked to the task. The link lives on the Issue side (`related_tasks`, or a `fixes` link made with `backlog_link`), and this query returns Issues linked by either:

```
backlog_query("SELECT i.id,i.status,json_extract(i.doc,'$.title') AS title FROM links l JOIN entities i ON i.kind=l.dst_kind AND i.id=l.dst_id WHERE l.src_kind='task' AND l.src_id='<task_id>' AND l.dst_kind='issue' AND i.deleted=0 AND i.status IN ('open','investigating')")
```

For each row, ask:

> "ISS-XXX is still open - close it as fixed in task_id, or leave for follow-up?"

| Choice | Action |
|---|---|
| Close as fixed | `backlog_issue_update("ISS-XXX", "fixed_in_task", "<task_id>")` then `backlog_issue_update("ISS-XXX", "status", "fixed")` |
| Leave for follow-up | No tool call; the issue stays open |

The question guards a state the user owns, so it stays. With no rows, skip this. With several open, ask for each in turn.
