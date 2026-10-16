# Add Idea: slash form details

```
/add-idea Per-task spike budgets — track effort vs estimate to flag scope drift early
/add-idea Auto-tag from git diff --tags automation,perf --status exploring --related-task v3-release-007
```

## Optional flags (any subset)

- `--tags <comma-separated>`: freeform tag strings (for example `automation,perf`)
- `--status <string>`: for example `exploring`, `parking-lot`, `candidate`
- `--related-task <task-id>`: repeat for several
- `--related-issue <issue-id>`: repeat for several

## Parse rules

1. Strip every `--flag value` pair from the input. What remains is the idea text.
2. `title`: the first sentence or clause of the idea text, cut to about 60 characters if it runs long.
3. `body`: the full idea text, verbatim, minus flags.
4. `tags`: the `--tags` value split on commas.
5. `status`: the `--status` value; `"candidate"` when not given. This default is canonical.
6. `related_tasks`, `related_issues`: each `--related-*` flag appends one id to its list.

The natural-language form "save this as an idea: <text>" is the same call with no flags: `title` and `body` come from `<text>`, and `tags`, `related_*` stay empty with `status` `"candidate"`.
