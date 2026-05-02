# Chained Handover Supersession

When a new handover replaces an older one for the same task line of work, we **chain** them: the new one points back at the old (`supersedes:`); the old one is edited in place to point at the new (`superseded_by:`) and gets a `SUPERSEDED` callout prepended to its body.

This automates what real handovers like `2026-04-27-viewer-redesign-m1-complete-resume-m2.md` do manually at the top of the file.

## When to chain

Set `supersedes = <prior_id>` if **all** of:

1. The new handover's `session_kind` is `milestone-complete` or `pivot`.
2. There is a prior handover whose `task_ids` overlap with the new handover's `task_ids` (intersection non-empty).
3. That prior handover's `session_kind` is also `milestone-complete` or `pivot`.

If multiple priors qualify, pick the **newest** by `date`.

## How to find the prior

```
candidates = backlog_handover_list(limit=10)
prior = None
for entry in candidates:                     # newest-first
    if entry["session_kind"] not in {"milestone-complete", "pivot"}:
        continue
    if not (set(entry["task_ids"]) & set(new_task_ids)):
        continue
    prior = entry["id"]
    break
```

If no prior matches, do **not** set `supersedes`. The chain starts fresh.

## What the server does when `supersedes` is set

`backlog_handover_create(supersedes=old_id, ...)` calls `apply_supersession(old_id=..., new_id=...)` after writing the new file. That helper:

1. Reads the old handover.
2. Sets `superseded_by: <new_id>` in its frontmatter.
3. Prepends a callout block to the body:
   ```
   > **SUPERSEDED YYYY-MM-DD by [<new_id>](./<new_id>.md).**
   > The next session should read the newer handover instead. This file kept as a checkpoint reference.
   ```
4. Writes the old file back.

If a SUPERSEDED callout already starts the body (the file was previously superseded by an even older handover, and we're now superseding by a newer one), the helper **replaces** the callout in place rather than stacking. Idempotent on the same `old_id`.

## What to do if `supersedes` resolution fails

If `backlog_handover_list` returns nothing useful (e.g., this is the first handover in the project, or no prior matches the task), simply omit `supersedes` from the create call. The chain starts here.

If `backlog_handover_create` returns a `WARNING: supersedes=... not found on disk` line, surface it to the user. The new handover was still written; the old one just didn't get its callout. Offer to fix manually with `backlog_handover_supersede(old_id=..., new_id=...)` if the user wants to repair the chain.
