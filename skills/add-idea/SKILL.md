---
name: add-idea
description: Log a lightweight idea (parking-lot thought) into .taskmaster/ideas/ as IDEA-NNN.md. Invoke when the user says "/add-idea …", "save this as an idea", "remember this idea", "log this idea", "that's a good idea, save it", or describes something worth keeping but not yet ready to be a task. Lighter than a task — just a freeform note with optional tags, status, and links to tasks/issues/lessons. The only correct way to write an idea — do not call backlog_idea_create directly.
---

# Add Idea

A lightweight place to record thoughts, parking-lot items, half-baked observations, and "future work" ideas without spawning a task and adding noise to the backlog.

## When to invoke

- User typed `/add-idea ...` (slash form)
- User said: "save this as an idea", "remember this idea", "log this idea", "let's note this", "park this for later"
- User described an idea explicitly: "I want to try X", "we should explore Y", "future work: Z"
- You auto-detected a sharp idea mid-conversation per the heuristics in `taskmaster:start-session` (path C — confidence-threshold auto-log)

## Slash form

```
/add-idea Per-task spike budgets — track effort vs estimate to flag scope drift early
/add-idea Auto-tag from git diff --tags automation,perf --status exploring --related-task v3-release-007
```

Optional flags (any subset):
- `--tags <comma-separated>` — freeform tag strings
- `--status <freeform-string>` — e.g. `exploring`, `parking-lot`, `candidate`
- `--related-task <task-id>` — repeat for multiple
- `--related-issue <issue-id>` — repeat for multiple
- `--related-lesson <lesson-id>` — repeat for multiple

## Natural-language form

The user can also say "save this as an idea: <text>" — treat it the same as a slash call. The full `<text>` becomes the body. Derive the **title** from the first sentence/clause of `<text>` (truncate to ~60 chars if it runs long); the same `<text>` (verbatim) is the body. Pass the derived title to the required `title` parameter of `backlog_idea_create`.

## Procedure

1. **Parse the input.** Title is the first sentence/clause if not separately specified. Body is the full text. Pull any optional flags out of the input.
2. **Search for duplicates.** Call `backlog_idea_list(limit=20)` and check the returned summaries for an existing idea covering the same ground. If you find one, don't create a duplicate — tell the user and offer to update the existing idea via `backlog_idea_update` instead.
3. **Commit.** Call:
   ```
   backlog_idea_create(
       title="<derived or given>",
       body="<full text minus flags>",
       tags=[...],
       status="<freeform>",
       related_tasks=[...],
       related_issues=[...],
       related_lessons=[...],
       created_by="user",   # this skill is user-initiated
   )
   ```
4. **Announce.** Reply with the format:
   > _Logged as IDEA-NNN — "<title>"_

   Optionally include the file path. Do NOT also include a long summary — the announcement is one line.

## Auto-log path (Claude-initiated)

This skill governs **user-initiated** idea logging. For Claude-initiated auto-log — when you detect a sharp idea mid-conversation per the heuristics in `start-session/SKILL.md` — call `backlog_idea_create` directly with `created_by="Claude"` instead of invoking this skill. Then announce inline:

> _Logged as IDEA-NNN — "<title>"_

Both paths go through the same MCP tool. The frontmatter `description` "only correct way to write an idea" applies to user-initiated writes; the auto-log direct call is the Claude-driven complement, not a violation.

## Template

For ideas where the user gives only a one-liner title with no body, you may optionally use `templates/idea-body.md` as a starting structure. Do not force it — freeform is fine.

## What NOT to do

- Don't ask the user to confirm before logging — log it, announce, move on. The whole point is low friction.
- Don't make ideas into tasks. If the user wants a task, they'll ask.
- Don't add detail beyond what the user said. The body is freeform; resist the urge to expand it.
- Don't link `promoted_to`. That field is set when an idea is promoted to a task (via `backlog_idea_update(promoted_to="T-XYZ")`), not at creation.
