# Add Idea

Ideas are a parking lot: thoughts, half-formed observations and future work that shouldn't become tasks yet. Keep friction low, because the user asked to save something, not to start a conversation about it.

## Logging an idea

Take the title from the first sentence or clause (about 60 characters at most) and use the user's full text, minus flags, as the body. Don't expand or edit it. The slash form `/add-idea <text> [--tags a,b] [--status s] [--related-task T-1] [--related-issue ISS-1]` follows the parse rules in `references/slash-form.md`; "save this as an idea: <text>" is the same call without flags.

Before creating, check for an existing idea on the same subject with `backlog_search(query, kinds=["idea"])`. If one exists, tell the user and offer to update it with `backlog_idea_update(idea_id, field, value)` instead of making a duplicate.

Then call `backlog_idea_create(title, body, tags, status, related_tasks, related_issues, created_by="user")` without asking for confirmation (`status` defaults to `candidate`; see `references/slash-form.md`), and reply in one line: _Logged as IDEA-NNN — "<title>"_.

## Ideas you notice yourself

When you spot an idea worth keeping mid-conversation, don't interrupt the user's flow:

- A fuzzy or ambient idea goes into an inline `<idea-candidate title="...">text</idea-candidate>` tag in your reply (optional attributes: `tags`, `status`, `related-task`, `related-issue`). end-session sweeps these tags into ideas, so nothing is lost.
- A concrete idea is created right away with `backlog_idea_create(..., created_by="Claude")` and announced with the same one-line reply.

## Store facts

`promoted_to` is set only when an idea becomes a task (`backlog_idea_update(idea_id, "promoted_to", "T-NNN")`), never at creation. For a bare one-line idea, `templates/idea-body.md` is an optional starting structure.
