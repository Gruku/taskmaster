# Chained Handover Supersession

When a new handover replaces an older one for the same line of work, they are **chained**: the old one is edited in place to point at the new (`superseded_by:`), its status becomes `superseded`, and a `SUPERSEDED` callout is prepended to its body. A line of work then has one open resume point.

Most of this is automatic. `supersedes=` is only for the cases the server cannot infer.

## What `backlog_handover_create` does on its own

Every create supersedes the older **open** handovers of the same line of work, archived ones (outside the 30-entry index) included:

- **`thread` passed explicitly** — every older open handover in that thread.
- **`thread` left empty (derived: bundle → epic → task id → tldr)** — a derived thread is a whole epic or bundle, so only handovers that share a task id with the new one, or where both name no task. A sibling task's handover in the same epic stays open, silently: it is someone else's resume point.

The result names what was superseded:

```
- Auto-superseded (same thread): <id>, <id>, … (+N more)
```

Exceptions, all automatic:

- A handover whose status was set by hand (`backlog_handover_update_status`) is never auto-transitioned. It stays open and the result carries `- WARNING: <id> not auto-superseded — its status was set by hand; …`. **Surface that line to the user**; they can close it with `backlog_handover_update_status` or chain it with `backlog_handover_supersede`. Once it carries a `superseded_by` pointer the warning stops.
- A handover born closed (`auto-stage`) supersedes nothing.
- A handover dated before an open one (backdated) does not supersede the newer one.

The new handover gets no `supersedes:` field from this; the pointer lives on the old ones (`superseded_by`), which is what `backlog_handover_list` shows in `links`.

## When to pass `supersedes=` yourself

Set `supersedes = <prior_id>` only when the prior handover is **not** covered above and the new one replaces it:

1. It is in a different thread (the line of work was renamed, or moved between a derived and a named thread), or
2. the thread is derived and the two handovers share no task id, but this session did take over that work.

The playbook's step 4 case — a `milestone` handover replacing the previous `milestone` for the same `task_ids` — is now covered automatically; passing `supersedes=` there is harmless (it is never applied twice) and additionally records `supersedes:` on the new handover.

## How to find the prior

```
out = backlog_handover_list(task_id=<task>, status="open", format="json")
```

`out["handovers"]` is newest first; each has `id`, `thread`, `session_kind`, `task_ids`, `status`, `superseded_by` and `links`. Pick the newest whose `task_ids` overlap the new handover's. If `out["truncated"]` is true and `out["archived_omitted"]` is non-zero, older handovers were not searched — repeat with `include_archived=True` before concluding there is no prior.

To see each thread's current resume point instead: `backlog_handover_list(latest_per_thread=True, format="json")`.

If nothing matches, omit `supersedes`. The chain starts here.

## What the server does when `supersedes` is set

Inside the same transaction as the create, the old handover:

1. gets `superseded_by: <new_id>` in its frontmatter,
2. has its status set to `superseded` (unless its status was set by hand — then the status is left as is and the result says so: `- Superseded: <id> (superseded_by recorded; its status was set by hand and stays <status>)`),
3. has this callout prepended to its body:
   ```
   > **SUPERSEDED YYYY-MM-DD by [<new_id>](./<new_id>.md).**
   > The next session should read the newer handover instead. This file kept as a checkpoint reference.
   ```

If a SUPERSEDED callout already starts the body, it is **replaced**, not stacked. Idempotent on the same `old_id`.

## Warnings to surface

- `WARNING: supersedes=... not found on disk` — the new handover was written; the id named nothing. Offer `backlog_handover_supersede(old_id=..., new_id=...)` with the right id to repair the chain.
- `WARNING: supersedes=... is this handover's own id; ignored.` — the id named nothing and the new handover was allocated that very id. Nothing was superseded and no pointer was recorded.
- `WARNING: <id> not auto-superseded — its status was set by hand` — see above.
