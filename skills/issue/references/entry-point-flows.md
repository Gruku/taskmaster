# Issue — Full Entry-Point Flows

This file contains the full per-entry-point subflow prose. The SKILL.md body carries
only the entry-point names and a condensed decision tree.

## log-issue

Explicit user request: `log a bug`, `file a bug`, `this is broken`, `report a bug`, `track this defect`, `this is a bug`.

1. **Auto-extract fields** from the conversation. Walk `references/auto-extraction.md` per-field rules:
   - `title` — first sentence describing the bug, <=80 chars
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

4. **Echo back**: "Issue logged: `ISS-NNN` (P1) — Short title."

5. **Offer a follow-up task** if the issue has no `related_tasks`:
   > "Want me to add a task to investigate this? I can link `ISS-NNN` in `related_issues`."
   On confirm, call `backlog_add_task` with `related_issues=["ISS-NNN"]`.

## flag-from-conversation

Claude proactively offers to log a bug when a bug-shaped finding surfaces mid-session — a test failure, an unexpected behavior, a root-cause identified in code, a regression mentioned in passing.

Heuristics for offer (any one is enough): test output shows unexpected failure; user says "wait that's wrong" or "that shouldn't happen"; Claude identifies a defect not the focus of the current task; a known issue mentioned has no `ISS-NNN`.

Offer pattern (inline, non-blocking):
> "This looks like a defect worth tracking. Want me to log it as an issue? I'd file it as P2 — [one-line symptom]."

If the user confirms, run the same flow as `log-issue` starting from step 1, using the mid-session context as intake. If the user declines, note it and continue — do not re-offer the same bug in the same session.

## update-status

Transitioning an existing issue: open -> investigating, investigating -> fixed, open/investigating -> wontfix, open/investigating -> duplicate.

Trigger: `mark issue fixed`, `close ISS-XX`, `investigating ISS-XX`, explicit status mention.

1. **Identify the issue ID** from the user's message or ask: "Which issue? (ISS-NNN)"

2. **Confirm the target status and required fields**:

   | Target status | Required field | When to use |
   |---|---|---|
   | `investigating` | none | You've started root-cause analysis |
   | `fixed` | `fixed_in_task` (task ID) | The fix is in a committed task |
   | `wontfix` | none (add a note in body) | Triaged out — not worth fixing |
   | `duplicate` | `duplicate_of` (ISS-NNN) | Same defect filed under another ID |

   See `references/lifecycle.md` for full state machine and `_validate_issue` rules.

3. **Call `backlog_issue_update`**:
   ```
   backlog_issue_update(
       issue_id="ISS-NNN",
       status="fixed",
       fixed_in_task="T-NNN",   # required for fixed
       # duplicate_of="ISS-MMM", # required for duplicate
   )
   ```
   The backend auto-fills `resolved` date when `status=fixed`. The index rebuilds automatically.

4. **Echo back**: "ISS-NNN marked fixed (T-NNN)."

## close-on-task-complete

Auto-offered by end-session when a task being closed has one or more open `related_issues`.

End-session detects this via the task's `related_issues` list. For each open issue, it surfaces:
> "Task T-NNN is done. ISS-XXX (P1 — Short title) is still open. Mark it fixed? It would link to T-NNN."

On confirm for each:
```
backlog_issue_update(issue_id="ISS-XXX", status="fixed", fixed_in_task="T-NNN")
```

On decline: leave the issue open, no action. The user may close it in a future session or mark it `wontfix`.

Do not batch-close without per-issue confirmation — a task may only partially address a related issue.

## triage-review

List and prioritize open issues by severity.

Trigger: `list open issues`, `what bugs are open`, `triage issues`.

1. **Fetch the open list**: `backlog_issue_list(status="open")`

2. **Summarize by severity group**:
   ```
   P0 (1): ISS-003 — Crash on startup when config missing
   P1 (2): ISS-007 — Multi-tab fanout drops pending dispatch
            ISS-012 — Orphan route silently swallows welcome dispatch
   P2 (1): ISS-009 — Parallel tool-call orphans on Init Project
   P3 (0):
   ```

3. **Surface aging issues** — call attention to any issue past its severity window's 60% mark (Aging) or 100% (Stale). See `references/severity-heuristics.md` for window lengths.

4. **Offer actions**:
   - Start investigating a specific issue -> `update-status` flow
   - Add a task to fix the top-priority issue -> `backlog_add_task` with `related_issues`
   - Close a duplicate -> `update-status` with `status=duplicate`
