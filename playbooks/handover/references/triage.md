# Handover triage

Triage walks open handovers older than 14 days and offers to close each one, so stale resume points stop competing with live ones. The playbook's "Status changes" section points here for the loop.

## Loop

1. List open handovers older than 14 days, newest first, at most 20 per run (`backlog_handover_list(status="open", until=<date 14 days ago>, format="json")`).
2. For each, offer `closed` (the work shipped or its thread moved on) or skip. Close with `backlog_handover_update_status(<id>, "closed", reason)`.
3. To park a whole line of work rather than one handover, use `backlog_thread_update(<name>, "parked", reason)`.

Skip `superseded` and `closed` handovers; they are already resolved.

## Why these limits

- **14 days:** a younger handover may still be the live context for an ongoing task. An older one has either been superseded by a newer handover in its thread or should be closed once its work shipped or moved on. 14 days catches stale stragglers without churning recent ones.
- **20 per run:** a user with 50 stale handovers loses patience halfway. 20 is about five minutes of decisions; the rest surface on the next run.
- **Newest first:** recent context is the most likely to still matter, so if the user stops partway, the most valuable decisions are already made.

## Triage and supersession

A `milestone` handover that a newer milestone replaces is chained automatically (`references/supersession.md`), so it never reaches triage. When you meet one that was not chained, prefer chaining it to the newer handover with `backlog_handover_supersede(old_id, new_id)` over closing it: that keeps the chain and the SUPERSEDED callout in the old file.
