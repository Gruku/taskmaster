# Auto-Mode Interaction (v3)

How end-session behaves when an auto run is active.

Call `backlog_auto_status` at the top of the skill. If a run is in progress, do **not** call `backlog_complete_task` directly — that responsibility belongs to the `auto-task` skill at its `END_SESSION` stage.

## auto-task is driving this session

End-session is being invoked as part of the auto flow. Proceed with the v3-pre-steps (snapshot + handover) so the next session has continuity, then let auto-task handle the task transition itself. Do not duplicate the `backlog_complete_task` call.

## User invoked /end-session mid-auto-run manually

The user explicitly typed /end-session while an auto run is mid-flight. Ask:

> *"There's an active auto run on `<target>`. Pause and write a handover, or abort the run?"*

| Choice | Action |
|---|---|
| Pause + handover | Invoke `taskmaster:handover` with `session_kind="context-handoff"` — preserves the cursor so the run resumes next session. |
| Abort the run | `backlog_auto_abort` — clears `auto/state.json`, the run is over. |

Never mix these two — aborting after writing a handover wastes the handover; pausing after aborting leaves a dangling reference.
