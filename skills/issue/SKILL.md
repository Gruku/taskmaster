---
name: issue
description: "Log/update/close project issues in .taskmaster/issues/ — the elevated tier for recurring (≥2 occurrences), systemic (≥2 components), or P0/P1 outstanding defects. Invoke for: 'log an issue', 'file an issue', 'promote to issue', 'promote B-XX to an issue', 'is this an issue', 'list open issues', 'triage issues', 'mark issue fixed', 'close ISS-XX', 'investigating ISS-XX'. NOT for one-off bugs — those route to taskmaster:bug. Colloquial 'issue' without recurring/systemic/outstanding evidence → route to taskmaster:bug instead. Only correct way to write or transition a project Issue."
---

# Issue

Structured defect tracking in `.taskmaster/issues/` — the **elevated tier**. Issues are persistent artifacts with lifecycle state and an aging window. A logged `ISS-NNN` survives sessions, links to tasks, and appears in the dashboard severity-sorted.

## The bar (new — 2026-05-20 redesign)

An Issue requires **at least one** of:

- **Recurring** — ≥2 prior occurrences cited (Bug IDs, task IDs, sessions)
- **Systemic** — ≥2 affected components OR class-of-defect
- **Outstanding** — P0/P1 with concrete blast-radius (data loss / security / prod block)

Severity alone is no longer a path. P2/P3 only qualify if recurring or systemic.

The mandatory `evidence:` frontmatter field must cite the criterion. `backlog_issue_create` rejects empty evidence.

If you can't cite evidence, **file a Bug** (`taskmaster:bug`) — Bugs are the sink. Promotion B → ISS is one cheap step (`backlog_bug_promote`).

See `references/issue-bar.md` for the bar criteria, evidence examples, and the canonical anti-example (`ISS-015`-shaped).

## Why this skill exists

The backend (`backlog_issue_create`, `backlog_issue_update`, `backlog_issue_list`, `backlog_issue_get`) is the storage layer; this skill is the authoring + lifecycle layer. Calling `backlog_issue_create` directly skips the bar-check + evidence-citation step.

This is the ONLY correct way to write or transition a project Issue — do not call `backlog_issue_create` or `backlog_issue_update` directly.

## Five entry points

1. **`log-issue`** — explicit user request to log; walks bar-check + evidence prompt
2. **`update-status`** — open → investigating → fixed / wontfix / duplicate
3. **`close-on-task-complete`** — auto-offered from end-session when a finished task has open `related_issues`
4. **`promote-from-bug`** — invoked by pattern scanner or by user saying "promote B-XX to an issue"
5. **`triage-review`** — list open issues by severity, surface aging items

Full per-entry-point subflows in `references/entry-point-flows.md`. Bar criteria + anti-examples in `references/issue-bar.md`.

## Entry point decision tree

| Trigger | Entry |
|---|---|
| `log an issue`, `file an issue`, `this is an issue` (with evidence) | `log-issue` |
| `promote B-XX to an issue`, scanner finding | `promote-from-bug` |
| `mark issue fixed`, `close ISS-XX`, `investigating ISS-XX` | `update-status` |
| End-session with open `related_issues` | `close-on-task-complete` |
| `list open issues`, `triage issues` | `triage-review` |
| `log a bug`, no evidence on "issue" wording | **route to `taskmaster:bug`** |

## References

- `references/issue-bar.md` — bar criteria, evidence-shape examples, ISS-015 anti-example
- `references/lifecycle.md` — state machine, transition rules, `_validate_issue` constraints
- `references/auto-extraction.md` — per-field extraction sources and fallbacks
- `references/entry-point-flows.md` — full subflow per entry point
- `templates/issue-body.md` — Repro / Expected / Investigation notes skeleton
