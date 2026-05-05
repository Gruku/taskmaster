---
name: issue
description: "Log/update/close project issues (bugs, defects) in .taskmaster/issues/. Invoke when the user says 'log a bug', 'found an issue', 'this is broken', 'track this defect', 'log this defect', 'file a bug', 'report a bug', 'this is a bug', 'mark issue fixed', 'close ISS-XX', 'investigating ISS-XX', 'list open issues', 'what bugs are open', or 'triage issues'. Captures severity (P0–P3), components, impact, location (file:line), repro, and lifecycle (open → investigating → fixed/wontfix/duplicate). Auto-offered by end-session when a completed task has open related_issues, and by Claude proactively when a bug-shaped finding surfaces in conversation. This is the only correct way to write or transition an issue — do not call backlog_issue_create or backlog_issue_update directly."
---

# Issue

Structured defect tracking in `.taskmaster/issues/`. Issues are persistent artifacts with lifecycle state, not ad-hoc notes. A logged `ISS-NNN` survives sessions, links to the tasks that caused and fixed it, and appears in the dashboard severity-sorted.

## Why this skill exists

The backend (`backlog_issue_create`, `backlog_issue_update`, `backlog_issue_list`, `backlog_issue_get`, `backlog_issue_resync`) is the storage layer; this skill is the **authoring + lifecycle** layer. Calling `backlog_issue_create` directly skips auto-extraction (severity, impact, location, repro), the user-review gate, and the offer to link a follow-up task. Always go through this skill.

This is the ONLY correct way to write or transition a project issue — do not call backlog_issue_create or backlog_issue_update directly.

## When to invoke

Five entry points:

1. **`log-issue`** — explicit user request to log a defect (`log a bug`, `file a bug`, `this is broken`, …)
2. **`flag-from-conversation`** — Claude proactively offers when a bug-shaped finding surfaces during work
3. **`update-status`** — transitioning open → investigating → fixed / wontfix / duplicate
4. **`close-on-task-complete`** — auto-offered from end-session when a finished task has open `related_issues`
5. **`triage-review`** — list and prioritize open issues by severity

## Severity quick-reference

Full decision rules and examples in [`references/severity-heuristics.md`](references/severity-heuristics.md).

| Severity | Rule |
|---|---|
| P0 / Critical | Data loss, security exposure, crash on startup, prod outage. Fix within 14 days. |
| P1 / High | Blocks core user flow, no workaround, regression in prod. Fix within 30 days. |
| P2 / Medium | Functional issue with a workaround, non-critical regression. Fix within 60 days. |
| P3 / Low | Cosmetic, edge-case, nice-to-have. Fix within 120 days. |

When severity is unclear, ask the user. Never silently assign P0.

---

## Entry point flows

### log-issue

Explicit user request — `log a bug`, `file a bug`, `this is broken`, `report a bug`, `track this defect`, `this is a bug`.

1. **Auto-extract fields** from the conversation. Walk [`references/auto-extraction.md`](references/auto-extraction.md) per-field rules:
   - `title` — first sentence describing the bug, ≤80 chars
   - `severity` — infer from impact language; ask if no signal
   - `impact` — what breaks; follow up "what breaks?" if blank
   - `components` — epic or folder; infer from file path if cited
   - `location` — `file:line` if mentioned; leave empty otherwise
   - `discovered_by` — `"Claude"` if found mid-session; `"user"` if user-reported
   - `related_tasks` — in-progress task ID + any IDs the user mentioned
   - `body` — draft `## Repro` + `## Expected` from the conversation; use placeholder if no repro steps yet

2. **Present the draft** to the user — show all fields plus the body. Ask:

   > "Looks good? I can adjust severity, add repro steps, change components, or link additional tasks."

   Iterate until the user approves.

3. **Write through `backlog_issue_create`**:

   ```
   backlog_issue_create(
       title=...,
       severity="P0"|"P1"|"P2"|"P3",
       impact=...,
       components=[...],
       location=[...],
       related_tasks=[...],
       discovered_by="Claude"|"user",
       body=<approved markdown body>,
   )
   ```

4. **Echo back**: *"Issue logged: `ISS-NNN` (P1) — Short title."*

5. **Offer a follow-up task** if the issue has no `related_tasks` or the user wants to track investigation:

   > "Want me to add a task to investigate this? I can link `ISS-NNN` in `related_issues`."

   On confirm, call `backlog_add_task` with `related_issues=["ISS-NNN"]`.

---

### flag-from-conversation

Claude proactively offers to log a bug when a bug-shaped finding surfaces mid-session — a test failure, an unexpected behavior, a root-cause identified in code, a regression mentioned in passing.

Heuristics for offer (any one is enough): test output shows unexpected failure; user says "wait that's wrong" or "that shouldn't happen"; Claude identifies a defect that is not the focus of the current task; a known issue is mentioned that has no `ISS-NNN`.

Offer pattern (inline, non-blocking):

> "This looks like a defect worth tracking. Want me to log it as an issue? I'd file it as P2 — [one-line symptom]."

If the user confirms, run the same flow as `log-issue` starting from step 1, using the mid-session context as intake. If the user declines, note it and continue — do not re-offer the same bug in the same session.

---

### update-status

Transitioning an existing issue: open → investigating, investigating → fixed, open/investigating → wontfix, open/investigating → duplicate.

Trigger: `mark issue fixed`, `close ISS-XX`, `investigating ISS-XX`, explicit status mention.

1. **Identify the issue ID** from the user's message or ask: "Which issue? (ISS-NNN)"

2. **Confirm the target status and required fields**:

   | Target status | Required field | When to use |
   |---|---|---|
   | `investigating` | none | You've started root-cause analysis |
   | `fixed` | `fixed_in_task` (task ID) | The fix is in a committed task |
   | `wontfix` | none (add a note in body) | Triaged out — not worth fixing |
   | `duplicate` | `duplicate_of` (ISS-NNN) | Same defect filed under another ID |

   See [`references/lifecycle.md`](references/lifecycle.md) for full state machine and `_validate_issue` rules.

3. **Call `backlog_issue_update`**:

   ```
   backlog_issue_update(
       issue_id="ISS-NNN",
       status="fixed",
       fixed_in_task="T-NNN",   # required for fixed
       # duplicate_of="ISS-MMM", # required for duplicate
   )
   ```

   The backend auto-fills `resolved` date when `status=fixed`. The index rebuilds automatically via `sync_issue_index`.

4. **Echo back**: *"ISS-NNN marked fixed (T-NNN)."*

---

### close-on-task-complete

Auto-offered by end-session when a task being closed has one or more open `related_issues`.

End-session detects this via the task's `related_issues` list. For each open issue, it surfaces:

> "Task T-NNN is done. ISS-XXX (P1 — Short title) is still open. Mark it fixed? It would link to T-NNN."

On confirm for each:

```
backlog_issue_update(
    issue_id="ISS-XXX",
    status="fixed",
    fixed_in_task="T-NNN",
)
```

On decline: leave the issue open, no action. The user may close it in a future session or mark it `wontfix`.

Do not batch-close without per-issue confirmation — a task may only partially address a related issue.

---

### triage-review

List and prioritize open issues by severity.

Trigger: `list open issues`, `what bugs are open`, `triage issues`.

1. **Fetch the open list**:

   ```
   backlog_issue_list(status="open")
   ```

2. **Summarize by severity group**:

   ```
   P0 (1): ISS-003 — Crash on startup when config missing
   P1 (2): ISS-007 — Multi-tab fanout drops pending dispatch
            ISS-012 — Orphan route silently swallows welcome dispatch
   P2 (1): ISS-009 — Parallel tool-call orphans on Init Project
   P3 (0):
   ```

3. **Surface aging issues** — call attention to any issue past its severity window's 60% mark (Aging tier) or 100% (Stale). See [`references/severity-heuristics.md`](references/severity-heuristics.md) for window lengths.

4. **Offer actions**:
   - Start investigating a specific issue → `update-status` flow
   - Add a task to fix the top-priority issue → `backlog_add_task` with `related_issues`
   - Close a duplicate → `update-status` with `status=duplicate`

---

## Edge cases

- **No backlog** — `backlog_issue_create` returns `Error: no backlog found`. Tell the user to run `backlog_init` first.
- **Severity ambiguous** — if the user's language could be P0 or P1, ask: "Is this blocking all users (P0) or just this specific flow (P1)?" Never guess P0 silently.
- **Regression on a fixed issue** — the backend has no `reopened` status. Create a new issue with `discovered_by` noting the regression; link the original `ISS-NNN` in `related_tasks` and body. See [`references/lifecycle.md`](references/lifecycle.md) for the recommended pattern.
- **Duplicate detection** — if the proposed title is similar to an existing issue, ask: "This looks like ISS-MMM — `duplicate_of` that instead?" Route to `update-status` if they confirm.
- **Location not known** — leave `location` empty; do not fabricate a file path. A non-localized issue is still valid.

## References

- [`references/severity-heuristics.md`](references/severity-heuristics.md) — P0–P3 decision rules, examples, aging tiers
- [`references/lifecycle.md`](references/lifecycle.md) — state machine, transition rules, `_validate_issue` constraints
- [`references/auto-extraction.md`](references/auto-extraction.md) — per-field extraction sources and fallbacks
- [`templates/issue-body.md`](templates/issue-body.md) — Repro / Expected / Investigation notes skeleton
