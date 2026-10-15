# Issue: entry-point flows

## log-issue

Trigger: "log an issue", "file an issue", "this is an issue".

1. Walk the bar (`issue-bar.md`). With no evidence, route to `taskmaster:bug` (`../../bug/playbook.md`) and use the routing echo in `../../bug/references/bug-vs-issue.md`.
2. Draft the fields from the conversation, using `auto-extraction.md`: `title`, `severity`, `evidence` (the criterion plus its citation, one sentence), `impact`, `components`, `location`, `discovered_by`, `related_tasks`, `body`.
3. Show the draft with the evidence sentence and write it once the user agrees. Severity and evidence carry weight on an Issue, so the user sees them first. When the user already supplied severity and evidence themselves, write it and show the result instead.
4. Write it:
   ```
   backlog_issue_create(
       title=..., severity="P0"|"P1"|"P2"|"P3", evidence="<criterion + citation>",
       impact=..., components=[...], location=[...], related_tasks=[...],
       discovered_by=..., body=<markdown body>,
   )
   ```
5. Reply: `Logged ISS-NNN (P1) — <title>. Criterion: <recurring|systemic|outstanding>.`
6. When the Issue has no `related_tasks`, offer a follow-up task. On yes, add it with `backlog_add_task` (the finish condition goes in `tldr`), then record the link with `backlog_link(action="create", source="T-NNN", target="ISS-NNN", type="fixes")`. That link shows on the task and on the Issue; `backlog_add_task` has no argument for it.

## update-status

Trigger: "mark issue fixed", "close ISS-XX", "investigating ISS-XX".

1. Take the Issue id from the user's message, or ask "Which issue? (ISS-NNN)".
2. Check the target status and its required field:

   | Target | Required field | Use when |
   |---|---|---|
   | `investigating` | none | root-cause analysis has started |
   | `fixed` | `fixed_in_task` | the fix is in a committed task |
   | `wontfix` | none (note the reason in the body) | triaged out |
   | `duplicate` | `duplicate_of` (ISS-NNN) | same defect filed under another id |

3. `backlog_issue_update` takes one `field`/`value` per call. Set the companion field first, then the status:
   ```
   backlog_issue_update("ISS-NNN", "fixed_in_task", "T-NNN")
   backlog_issue_update("ISS-NNN", "status", "fixed")
   ```
   The store fills `resolved` when `status=fixed`.
4. Reply: `ISS-NNN marked fixed (T-NNN).`

## promote-from-bug

Trigger: "promote B-XX to an issue" (optionally with sibling Bug ids), or a `backlog_bug_pattern_scan` group the user confirms.

1. Collect the Bug ids.
2. Get the evidence citation from the user, or fill it from the matched signature and show the Bug titles beside it.
3. Settle the Issue title (usually a generalization of the Bug titles) and severity.
4. Call:
   ```
   backlog_bug_promote(
       bug_ids=["B-018", "B-031"],
       title="Path-resolver mismatch across v3 readers",
       severity="P1",
       evidence_text="Recurring: B-018 (handover reader) + B-031 (idea reader), same root cause.",
   )
   ```
5. Reply: `Promoted to ISS-NNN; source Bugs are marked promoted.`

## close-on-task-complete

end-session raises this when a finished task fixes an unresolved Issue. The Issues come from the `fixes` entries under `links` in `backlog_get_task(T-NNN)`, plus any unresolved Issue that lists the task in `related_tasks` (`backlog_issue_get` shows that field; the task side doesn't). For each one, ask: "T-NNN is done. ISS-XXX (P1 — <title>) is still open. Mark it fixed, linked to T-NNN?" On yes:

```
backlog_issue_update("ISS-XXX", "fixed_in_task", "T-NNN")
backlog_issue_update("ISS-XXX", "status", "fixed")
```

On no, leave the Issue open. Confirm each Issue separately, because one task may only partly address a related Issue.

## triage-review

Trigger: "list open issues", "triage issues".

1. `backlog_issue_list(status="open")`.
2. Summarize by severity, for example:
   ```
   P0 (1): ISS-003 — Crash on startup when config missing
   P1 (2): ISS-007 — Multi-tab fanout drops pending dispatch
           ISS-012 — Orphan route silently swallows welcome dispatch
   ```
3. Flag Issues past 60% of their window (aging) or 100% (stale); window lengths are in `issue-bar.md`.
4. Offer next steps: start investigating one (`update-status`), add a fix task (`log-issue` step 6 links it), or close a duplicate.
