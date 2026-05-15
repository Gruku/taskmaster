# Auto-Resolution Hooks

Three mechanisms transition decisions without explicit `/decide` interaction:

## 1. Commit-message resolution

A commit message containing the line:

    Resolves: DEC-NNN with option N

triggers the MCP server's next scan to call `resolve_decision(NNN, resolved_with=N, resolved_in="commit:<sha>")`. The rationale field stays empty; the commit body becomes the de facto rationale via the back-link.

Regex: `^Resolves:\s*(DEC-\d+)\s+with\s+option\s+(\d+)\s*$` (multiline, case-insensitive).

## 2. auto-task block

`auto-task` will not transition a task to `done` while `backlog_decision_list(status="open", task_id=<current>)` is non-empty. The user can override with `--override-open-decisions`.

## 3. end-session sweep

`end-session` runs `backlog_decision_list(status="open", task_id=<current>)` before writing the handover. For each result, it asks (via `AskUserQuestion`):

- **Carry forward** — leave open, list in handover frontmatter under `open_decisions`.
- **Resolve now** — pick an option inline.
- **Drop** — capture reason.

The handover write receives the final `open_decisions` and `resolved_this_session` arrays.
