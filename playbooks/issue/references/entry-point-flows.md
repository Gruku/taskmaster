# Issue — Full Entry-Point Flows

This file contains the full per-entry-point subflow prose. The SKILL.md body carries
only the entry-point names and a condensed decision tree.

## log-issue

Explicit user request: `log an issue`, `file an issue`, `this is an issue`.

1. **Walk the bar** — does the description cite recurring / systemic / outstanding? Use `references/issue-bar.md`.
   - **No evidence** → route to `taskmaster:bug` (log-bug entry point). Use the routing-echo from `references/bug-vs-issue.md`.
   - **Evidence present** → continue to step 2.

2. **Auto-extract fields** from the conversation. Walk `references/auto-extraction.md` per-field rules:
   - `title` — first sentence describing the bug, ≤80 chars
   - `severity` — infer from impact language; ask if no signal
   - `evidence` — the criterion + citation in one sentence (mandatory)
   - `impact` — what breaks; follow up "what breaks?" if blank
   - `components` — epic or folder; infer from file path if cited
   - `location` — `file:line` if mentioned; leave empty otherwise
   - `discovered_by` — `"Claude"` if found mid-session; `"user"` if user-reported
   - `related_tasks` — in-progress task ID + any IDs the user mentioned
   - `body` — draft `## Repro` + `## Expected` from the conversation

3. **Present the draft** to the user — show all fields including the evidence sentence. Ask:
   > "Looks good? I can adjust severity, add repro steps, change components, or link additional tasks."
   Iterate until the user approves.

4. **Write through `backlog_issue_create`**:
   ```
   backlog_issue_create(
       title=...,
       severity="P0"|"P1"|"P2"|"P3",
       evidence="<criterion + citation>",
       impact=...,
       components=[...],
       location=[...],
       related_tasks=[...],
       discovered_by="Claude"|"user",
       body=<approved markdown body>,
   )
   ```

5. **Echo back**: "Issue logged: `ISS-NNN` (P1) — Short title. Criterion: <recurring|systemic|outstanding>."

6. **Offer a follow-up task** if the issue has no `related_tasks`:
   > "Want me to add a task to investigate this? I can link `ISS-NNN` in `related_issues`."
   On confirm, call `backlog_add_task` with `related_issues=["ISS-NNN"]`.

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

## promote-from-bug

Invoked when:
- The user says "promote B-XX to an issue" (with optional sibling bug IDs)
- `backlog_bug_pattern_scan` surfaces a candidate group during start-session or end-of-task, AND the user confirms promotion

1. **Collect bug IDs** to promote (single or multiple from a pattern group).

2. **Walk evidence citation** — ask the user (or auto-fill from the matched signature) which criterion is being met. Show the bug titles and their signatures.

3. **Confirm Issue title + severity** — usually a generalization of the matched bug titles.

4. **Call `backlog_bug_promote`:**
   ```
   backlog_bug_promote(
       bug_ids=["B-018", "B-031"],
       title="Path-resolver mismatch across v3 readers",
       severity="P1",
       evidence_text="Recurring: B-018 (handover reader) + B-031 (lesson reader). Same root cause.",
   )
   ```

5. **Echo**: "Promoted to `ISS-NNN`. Source Bugs marked promoted_to: ISS-NNN."

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

3. **Surface aging issues** — call attention to any issue past its severity window's 60% mark (Aging) or 100% (Stale). See `references/issue-bar.md` for window lengths.

4. **Offer actions**:
   - Start investigating a specific issue -> `update-status` flow
   - Add a task to fix the top-priority issue -> `backlog_add_task` with `related_issues`
   - Close a duplicate -> `update-status` with `status=duplicate`
