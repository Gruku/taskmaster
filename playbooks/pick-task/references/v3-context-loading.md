# v3 Context Loading — token budget

How pick-task behaves on v3 backlogs when context-loading sub-steps activate.

## Resuming a task this session holds

After a pause or a compaction in the same session, skip the thread board: call `backlog_context(scope="session", include=["handovers", "issues"])` and `backlog_claim(action="renew", task_id=<id>)`. If you took a cursor before the pause (`backlog_changes_since(epic=<epic>)`), pass it back as `backlog_changes_since(cursor=<cursor>, epic=<epic>)` to learn what moved; `resync_required: true` means re-read, never "nothing changed". Then continue at Step 6.

## Token budget for steps 5a–5b

Steps 5a–5b read the `selected` half of the Step 3 `backlog_context` answer, which is bounded by `budget_bytes` (default 8,000) and reports what it left out in `budget.omitted`. Budget targets:

| Source | Per-item | Cap | Worst case |
|---|---|---|---|
| Related handovers (tldrs) | ~50 tokens | 3 tldrs | ~150 tokens |
| Optional handover full body | ~200 tokens | 1 (only when warranted) | ~200 tokens |
| Related issues | ~50 tokens | top 3 summaries | ~150 tokens |

**Soft target: ~500 tokens additive. Warn at 1,000.**

When a task's context load exceeds the warn threshold, prune in this order:

1. Drop optional handover-body fetches.
2. Never prune `related_issues` — bug context is load-bearing for not re-introducing fixed defects.
