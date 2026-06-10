---
name: bug
description: "Log/update/close project bugs in .taskmaster/bugs/. Invoke for: 'log a bug', 'this is a bug', 'track this defect', 'file a bug', 'I found a bug', 'list open bugs', 'what bugs are open', 'promote B-XX', 'close B-XX', 'fixed B-XX in commit', 'shelve this for later'. Also invoke for ambiguous 'issue' phrasing when the finding lacks recurring/systemic/outstanding evidence — the router falls back here. Only correct way to write or transition a project Bug."
---

# Bug

Lightweight, project-wide defect tracking in `.taskmaster/bugs/`. Bugs are the user-flagged sink — every defect the user wants tracked but that doesn't clear the Issue bar (recurring / systemic / outstanding). No aging window, no fix-by date — Bugs are a sink, not a commitment.

## Why this skill exists

The backend (`backlog_bug_create`, `backlog_bug_list`, `backlog_bug_get`, `backlog_bug_update`, `backlog_bug_archive`, `backlog_bug_pattern_scan`, `backlog_bug_promote`) is the storage layer; this skill is the authoring + lifecycle layer. Calling `backlog_bug_create` directly skips the disposition prompt and the user-confirmation gate on AI-detected findings.

This is the ONLY correct way to write or transition a project Bug — do not call `backlog_bug_create` or `backlog_bug_update` directly.

## Five entry points

1. **`log-bug`** — explicit user request to log
2. **`offer-on-explicit-finding`** — AI may offer ONLY when a substantive finding surfaces and the user might want to track it; one-shot per session
3. **`disposition`** — fix-now / spawn-task / shelve / promote selector, used at create time and at the task-close gate
4. **`update-status`** — open ↔ shelved; transitions to adopted, promoted, fixed
5. **`triage-review`** — list open / shelved Bugs, surface aging shelved candidates

Full per-entry-point subflows in `references/entry-point-flows.md`. Boundary with Issue in `references/bug-vs-issue.md`.

## Bug-vs-Issue at a glance

| Symptom | Bug | Issue |
|---|---|---|
| One-off cosmetic | ✅ | ❌ |
| Single-component defect with workaround | ✅ | ❌ |
| Recurring across ≥2 prior occurrences | ❌ | ✅ |
| Systemic (≥2 components / class-of-defect) | ❌ | ✅ |
| P0 with prod outage / data loss / security | ❌ | ✅ |
| Found and fixed in 30 seconds during a task | ❌ (just fix it) | ❌ (just fix it) |

When in doubt, file a Bug — promotion to Issue is one cheap step (`backlog_bug_promote`), demotion is not.

## State machine

```
open ─► fixed     (+fix_commit; archived on parent-task close)
     ─► adopted   (+adopted_into: T-NNN)
     ─► promoted  (+promoted_to: ISS-NNN)
     ─► shelved   (revisit via start-session)

shelved ─► open | adopted | promoted
```

Task close-gate: a task cannot transition to `done` while any linked Bug has `status: open`.
