# Issue Lifecycle

## State machine

```
open ────────────────────────────────► fixed      (requires fixed_in_task)
  │                                  ► wontfix
  │                                  ► duplicate  (requires duplicate_of)
  ▼
investigating ──────────────────────► fixed       (requires fixed_in_task)
                                    ► wontfix
                                    ► duplicate   (requires duplicate_of)
```

Valid statuses: `open`, `investigating`, `fixed`, `wontfix`, `duplicate`.

There is no `closed` status and no `reopened` status. See the regression pattern below.

## Transitions

### open → investigating

When to use: you have started root-cause analysis but have not landed a fix.

Required field: none.

```
backlog_issue_update(issue_id="ISS-NNN", status="investigating")
```

The most common first transition. Use it to signal that the issue is being actively worked rather than sitting in the queue.

### investigating → fixed (most common path)

When to use: the fix is committed and covered by a task.

Required field: `fixed_in_task` must be set to the task ID that contains the fix. `_validate_issue` raises `ValueError` if `fixed_in_task` is absent when `status="fixed"`.

```
backlog_issue_update(issue_id="ISS-NNN", status="fixed", fixed_in_task="T-NNN")
```

The backend auto-fills `resolved` with today's ISO date if it is not already set. The issue file is rewritten and the index is rebuilt automatically via `sync_issue_index`.

### open → wontfix

When to use: triaged out — the defect is acknowledged but not worth fixing (scope, cost, acceptable behavior, etc.). Add a brief explanation in the body before transitioning.

Required field: none, but a body note is expected by convention.

```
backlog_issue_update(issue_id="ISS-NNN", status="wontfix")
```

### open/investigating → duplicate

When to use: the same defect is already tracked under another `ISS-NNN`.

Required field: `duplicate_of` must be set. `_validate_issue` raises `ValueError` if `duplicate_of` is absent when `status="duplicate"`.

```
backlog_issue_update(issue_id="ISS-NNN", status="duplicate", duplicate_of="ISS-MMM")
```

## What happens on disk

Each issue lives in `.taskmaster/issues/ISS-NNN.md` as a YAML-frontmatter + markdown-body file. After any `backlog_issue_update` call, `sync_issue_index` rebuilds the slim index entry in `backlog.yaml` from disk. The `_ISSUE_INDEX_FIELDS` kept in the index are: `id`, `title`, `status`, `severity`, `components`, `related_tasks`. Full content is in the individual file; the index is only for fast dashboard rendering.

## Regression pattern (re-opening)

The backend has no `reopened` status. If a `fixed` issue regresses — the bug returns in a later release — the correct approach is:

1. Create a new issue with `backlog_issue_create`, noting in the title or body that it is a regression.
2. Set `discovered_by` to describe who found the regression and when.
3. Reference the original issue in the `related_tasks` list or body prose (e.g. "Regression of ISS-NNN, previously fixed in T-MMM").

Do not mutate the status of the original `fixed` issue. The original fix still happened; the regression is a new defect with its own history.

## Validate rules (from `_validate_issue` in `taskmaster_v3.py`)

- `status` must be one of `("open", "investigating", "fixed", "wontfix", "duplicate")` — else `ValueError`.
- `severity` must be one of `("P0", "P1", "P2", "P3")` — else `ValueError`.
- If `status == "fixed"`, `fixed_in_task` must be non-empty — else `ValueError`.
- If `status == "duplicate"`, `duplicate_of` must be non-empty — else `ValueError`.

These rules fire on both create (`write_issue`) and update (`update_issue`). The MCP tools surface the `ValueError` text directly — if you see one, the required field is missing.
