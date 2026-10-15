# Issue lifecycle

## States

```
open ──────────────► fixed      (needs fixed_in_task)
  │                ► wontfix
  │                ► duplicate  (needs duplicate_of)
  ▼
investigating ─────► fixed | wontfix | duplicate  (same requirements)
```

Valid statuses: `open`, `investigating`, `fixed`, `wontfix`, `duplicate`. There is no `closed` or `reopened` status; see the regression pattern below.

`backlog_issue_update` takes one `field`/`value` per call and validates the stored row, so set the companion field first, then `status`. A missing required field comes back as a ValueError naming it.

## Transitions

- **open → investigating**: root-cause analysis has started. `backlog_issue_update("ISS-NNN", "status", "investigating")`.
- **→ fixed**: the fix is committed under a task. Set `fixed_in_task` to that task id, then `status` to `fixed`. The store fills `resolved` with today's date.
  ```
  backlog_issue_update("ISS-NNN", "fixed_in_task", "T-NNN")
  backlog_issue_update("ISS-NNN", "status", "fixed")
  ```
- **→ wontfix**: the defect is acknowledged but not worth fixing (scope, cost, acceptable behavior). Note the reason in the body first, then set `status` to `wontfix`.
- **→ duplicate**: the same defect is tracked under another Issue. Set `duplicate_of` to that id, then `status` to `duplicate`.
  ```
  backlog_issue_update("ISS-NNN", "duplicate_of", "ISS-MMM")
  backlog_issue_update("ISS-NNN", "status", "duplicate")
  ```

`severity` must be one of P0, P1, P2, P3.

## Regression pattern

If a `fixed` Issue regresses, leave the original alone: its fix really happened. Create a new Issue with `backlog_issue_create`, say in the title or body that it is a regression ("Regression of ISS-NNN, previously fixed in T-MMM"), list the original's task in `related_tasks`, and describe who found it and when in `discovered_by`.
