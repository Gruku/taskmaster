---
name: issue
description: "Log/update/close project issues in .taskmaster/issues/. Invoke when the user says 'log a bug', 'found an issue', 'this is broken', 'track this defect', 'log this defect', 'file a bug', 'report a bug', 'this is a bug', 'mark issue fixed', 'close iss-xx', 'investigating iss-xx', 'list open issues', 'what bugs are open', or 'triage issues'. This is the only correct way to write or transition an issue."
---

# Issue

Structured defect tracking in `.taskmaster/issues/`. Issues are persistent artifacts with lifecycle state. A logged `ISS-NNN` survives sessions, links to tasks, and appears in the dashboard severity-sorted.

## Why this skill exists

The backend (`backlog_issue_create`, `backlog_issue_update`, `backlog_issue_list`, `backlog_issue_get`) is the storage layer; this skill is the authoring + lifecycle layer. Calling `backlog_issue_create` directly skips auto-extraction, the user-review gate, and the offer to link a follow-up task.

This is the ONLY correct way to write or transition a project issue — do not call backlog_issue_create or backlog_issue_update directly.

## Five entry points

1. **`log-issue`** — explicit user request to log a defect
2. **`flag-from-conversation`** — Claude proactively offers when a bug-shaped finding surfaces
3. **`update-status`** — transitioning open -> investigating -> fixed / wontfix / duplicate
4. **`close-on-task-complete`** — auto-offered from end-session when a finished task has open `related_issues`
5. **`triage-review`** — list and prioritize open issues by severity

Full per-entry-point subflows in `references/entry-point-flows.md`.

## Entry point decision tree

| Trigger | Entry |
|---|---|
| `log a bug`, `file a bug`, `this is broken`, `report a bug`, `track this defect`, `this is a bug` | `log-issue` |
| Bug-shaped finding surfaces mid-session | `flag-from-conversation` |
| `mark issue fixed`, `close ISS-XX`, `investigating ISS-XX` | `update-status` |
| End-session with open `related_issues` | `close-on-task-complete` |
| `list open issues`, `what bugs are open`, `triage issues` | `triage-review` |

## Severity quick-reference

| Severity | Rule |
|---|---|
| P0 / Critical | Data loss, security exposure, crash on startup, prod outage. Fix within 14 days. |
| P1 / High | Blocks core user flow, no workaround, regression in prod. Fix within 30 days. |
| P2 / Medium | Functional issue with a workaround, non-critical regression. Fix within 60 days. |
| P3 / Low | Cosmetic, edge-case, nice-to-have. Fix within 120 days. |

Full decision rules and examples in `references/severity-heuristics.md`. When severity is unclear, ask the user. Never silently assign P0.

## References

- `references/severity-heuristics.md` — P0–P3 decision rules, examples, aging tiers
- `references/lifecycle.md` — state machine, transition rules, `_validate_issue` constraints
- `references/auto-extraction.md` — per-field extraction sources and fallbacks
- `references/entry-point-flows.md` — full subflow for each of the 5 entry points
- `templates/issue-body.md` — Repro / Expected / Investigation notes skeleton
