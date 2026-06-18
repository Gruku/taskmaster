# Auto-Resolution Hooks

Two mechanisms transition decisions without explicit `/decide` interaction:

## 1. Commit-message resolution

A commit message containing the line:

    Resolves: DEC-NNN with option N

triggers the MCP server's next scan to call `resolve_decision(NNN, resolved_with=N, resolved_in="commit:<sha>")`. The rationale field stays empty; the commit body becomes the de facto rationale via the back-link.

Regex: `^Resolves:\s*(DEC-\d+)\s+with\s+option\s+(\d+)\s*$` (multiline, case-insensitive).

## 2. end-session sweep

`end-session` runs `backlog_decision_list(status="open", task_id=<current>)` before writing the handover. For each result, it asks (via `AskUserQuestion`):

- **Carry forward** — leave open; the decision is recorded under the handover body's "Open decisions" section as `[[DEC-NNN]] — <summary>`.
- **Resolve now** — pick an option inline; `backlog_decision_resolve` is called, and the decision is recorded under "Resolved this session".
- **Drop** — capture reason; `backlog_decision_drop` is called, and the decision is recorded under "Resolved this session" with a `(dropped)` suffix.

The collected lists are embedded directly in the handover **body markdown**. `backlog_handover_create` has no `open_decisions` / `resolved_this_session` kwargs — body sections plus `[[DEC-NNN]]` link syntax are the carrier, and the viewer resolves cross-entity navigation from those links.
