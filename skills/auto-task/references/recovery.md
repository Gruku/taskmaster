# Recovery — failure halts and resume semantics

How to halt cleanly when a stage cannot proceed, and how to resume after a crash, compaction, or restart.

## Failure escape hatches

At any stage, halt with `backlog_auto_complete_task(status="failed", fail_reason=..., summary=...)`. The cursor advances no further until a new run starts. Valid reasons:

| Reason | When |
|---|---|
| `tests-failed` | Test suite went red and recovery wasn't possible in the same stage. |
| `spec-rejected` | User rejected the plan at SPEC_REVIEW, or rejected the diff at REVIEW_GATE. |
| `blocked` | External dependency, missing access, decision the user has to make before work continues. |
| `crashed` | Tooling or environment broke; the failure isn't a logic problem and needs investigation. |
| `user-aborted` | User explicitly said stop. |

After halting, invoke `taskmaster:handover` with `session_kind="context-handoff"`, describing where the run stopped and what's broken. The next session reads that handover at start-session and can resume from a known anchor. Do **not** attempt the next stage after a failure halt — that's what the halt is for.

## Resume semantics

Auto-task is designed to wake up mid-run. After a `/compact`, a crash, a process restart, or the orchestrator handing control back, the first action is always:

```
backlog_auto_status
```

The output names the current `task_id`, `stage`, and `model`. Jump directly to the matching step in the main SKILL — do **not** re-run earlier stages.

The cursor is trustworthy because:

- Every `backlog_auto_advance` call writes atomically to `auto/state.json`.
- The PreCompact hook snapshots state right before compaction, so cursor + last-known-good context survive the boundary.
- Stage transitions only happen at the end of a stage's work, never speculatively.

If the cursor says `IMPLEMENT`, the implement work is **in progress**, not done. If it says `TEST`, the implement commits are already in git. Use this as the source of truth for "what have I already done?" — never re-derive it from conversation memory after a wake-up.
