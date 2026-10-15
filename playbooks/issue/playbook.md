# Issue

Issues are the elevated defect tier: persistent records with lifecycle state and an aging window, linked to tasks and sorted by severity on the dashboard. Write and transition them through this playbook rather than calling `backlog_issue_create` / `backlog_issue_update` cold, because it runs the bar check and gets the evidence citation the store requires.

## The bar

An Issue needs at least one of: recurring (2+ prior occurrences cited), systemic (2+ components or a defect class), or outstanding (P0/P1 with concrete blast radius). Severity alone doesn't qualify. The criteria, evidence examples and aging windows are in `references/issue-bar.md`.

`backlog_issue_create` rejects empty `evidence`, so cite the criterion in one sentence. If you can't, file a Bug with `taskmaster:bug`; promoting a Bug later is one `backlog_bug_promote` call.

## Entry points

Subflows are in `references/entry-point-flows.md`.

- **`log-issue`**: the user asks to log an Issue. Run the bar check, draft the fields, confirm, and write it.
- **`promote-from-bug`**: the user says "promote B-XX to an issue", or a `backlog_bug_pattern_scan` group is confirmed for promotion.
- **`update-status`**: open, investigating, fixed, wontfix or duplicate; states and required fields are in `references/lifecycle.md`.
- **`close-on-task-complete`**: end-session offers to close open `related_issues` of a finished task.
- **`triage-review`**: list open Issues by severity and flag the aging ones.

"Issue" wording with no evidence of the bar goes to `taskmaster:bug`.

Per-field sources for drafting are in `references/auto-extraction.md`; the body skeleton is `templates/issue-body.md`.

## Writes

A mutating result ending in `[seq N]` is committed; `(export pending: …)` means committed with the file export still queued; `No change … already …` without `[seq]` means the value was already stored. `backlog_store_status` shows dirty or quarantined files.
