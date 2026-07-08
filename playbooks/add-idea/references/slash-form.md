# Add Idea — Slash Form Details

The slash form is the primary invocation pattern for `taskmaster:add-idea`.

## Slash Form Syntax

```
/add-idea Per-task spike budgets — track effort vs estimate to flag scope drift early
/add-idea Auto-tag from git diff --tags automation,perf --status exploring --related-task v3-release-007
```

## Optional Flags (any subset)

- `--tags <comma-separated>` — freeform tag strings (e.g. `automation,perf`)
- `--status <freeform-string>` — e.g. `exploring`, `parking-lot`, `candidate`
- `--related-task <task-id>` — repeat for multiple
- `--related-issue <issue-id>` — repeat for multiple

## Parse Rules

1. Strip all `--flag value` pairs from the input string. Everything remaining is the idea text.
2. Extract `title`: first sentence/clause of the idea text, truncated to ~60 chars if too long.
3. `body`: full idea text (verbatim, minus flags).
4. `tags`: parsed from `--tags` value, split on commas.
5. `status`: parsed from `--status` value; default to `"candidate"` if not provided.
6. `related_tasks`, `related_issues`: each `--related-*` flag appends one ID to the respective list.

## Field Extraction for Natural-Language Form

When user says "save this as an idea: <text>":
- `title` = first sentence/clause of `<text>` (truncated to ~60 chars if runs long)
- `body` = the full `<text>` verbatim
- No flags expected — `tags`, `status`, `related_*` are all empty lists/defaults

## Commit Call

```
backlog_idea_create(
    title="<derived or given>",
    body="<full text minus flags>",
    tags=[...],
    status="<freeform>",
    related_tasks=[...],
    related_issues=[...],
    created_by="user",
)
```

## Template

For ideas where the user gives only a one-liner title with no body, you may optionally use `templates/idea-body.md` as a starting structure. Do not force it — freeform is fine.
