# v3 Context Loading — token budget

How pick-task behaves on v3 backlogs when context-loading sub-steps activate.

## Token budget for steps 5a–5c

The v3 sub-steps (related handovers, related issues, trigger-matched lessons) add context on top of the base `backlog_pick_task` response. Budget targets:

| Source | Per-item | Cap | Worst case |
|---|---|---|---|
| Related handovers (tldrs) | ~50 tokens | 3 tldrs | ~150 tokens |
| Optional handover full body | ~200 tokens | 1 (only when warranted) | ~200 tokens |
| Related issues | ~50 tokens | top 3 summaries | ~150 tokens |
| Trigger-matched lessons | ~300 tokens | 3 bodies | ~900 tokens |

**Soft target: ~1,500 tokens additive. Warn at 3,000.**

When a task's context load exceeds the warn threshold, prune in this order:

1. Drop the lowest `reinforce_count` lesson match.
2. Drop optional handover-body fetches.
3. Never prune `related_issues` — bug context is load-bearing for not re-introducing fixed defects.
