# Scope boundaries and token discipline

What auto-task explicitly does NOT do, and the per-stage context budget that keeps long runs from blowing out.

## What this skill does NOT do

Auto-task is the inner loop — one task, one cursor, one stage at a time. It deliberately stays out of the surrounding orchestration:

- **Does not pick which task to work on.** The orchestrator (auto-epic / auto-phase) or `backlog_auto_start` decides which task seeds the cursor.
- **Does not dispatch subagents.** That's the orchestrator's job; auto-task runs *as* the subagent.
- **Does not write run-level handovers** (epic-complete / phase-complete summaries). Only per-task stage handovers, capped at ~200 words.
- **Does not call `backlog_auto_finish`** on the run. The orchestrator owns run lifecycle. Auto-task may set the cursor to `None` (single-task run done), but the wrap-up call is the orchestrator's.

If a stage encounters a problem that belongs to one of the above (e.g., "this task shouldn't have been picked"), halt with `fail_reason="blocked"` and let the orchestrator decide. Do not silently expand scope to handle it.

## Token discipline

Long auto runs accumulate context. These limits keep PICK and HANDOVER from drifting into hundreds-of-tokens territory:

- **Handovers at PICK:** load only the 3 tldrs from `backlog_handover_list`. Fetch a full body only when the latest is `session_kind="context-handoff"` with a non-trivial `next_action`.
- **Lessons at PICK:** load only `backlog_lesson_match` results (cap 3). Do not preload the full lesson library.
- **Handover stub at Step 7:** cap at ~200 words. Long narrative goes in the `tasks/<id>.md` body, not the stub.
- **File contents in handover bodies:** never inline file contents — reference paths instead. The next session can read the file if needed.

Soft target additive cost at PICK: ~1.5k tokens. Hard warn: ~3k. When budget exceeds the warn threshold, prune lessons (lowest `reinforce_count` first), then drop optional handover-body fetches. Never prune `related_issues` — bug context is load-bearing for not re-introducing fixed defects.
