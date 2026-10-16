# pick-task: context loading and resuming

How pick-task reads a task's context, and how a session that already holds a task picks its work back up.

## Resuming a task this session holds

After a pause or a compaction in the same session, skip the thread board: call `backlog_context(scope="session", include=["handovers", "issues"])` and `backlog_claim(action="renew", task_id=<id>)`. If you took a cursor before the pause (`backlog_changes_since(epic=<epic>)`), pass it back as `backlog_changes_since(cursor=<cursor>, epic=<epic>)` to learn what moved; `resync_required: true` means re-read, never "nothing changed". Then continue with the Brief in `../playbook.md`.

## How big the context answer is

The `selected` half of the `backlog_context` answer is bounded by `budget_bytes` (default 8,000) and reports what it left out in `budget.omitted`. When something you need was omitted, raise `budget_bytes` or read that item directly. The blockers in `mandatory` are never trimmed.

Related issues are the part to keep when you do trim, because they stop a fixed defect from being reintroduced; optional handover bodies go first.
