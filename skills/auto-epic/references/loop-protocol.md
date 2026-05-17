# Auto-Epic — Loop Protocol (Step 2 Sub-Steps)

This file contains the detailed sub-step prose for each iteration of the auto-epic loop.

## 2a. Read Cursor

```
backlog_auto_status
-> parses out cursor.task_id, cursor.stage, cursor.model
```

If `cursor.stage` is `HANDOVER_STUB` and the task is in `failed`, the previous task halted the run. Stop the loop and proceed to step 3 (failure handover).

## 2c. Verify Cursor Advanced

The subagent should have called `complete_task`. Verify by reading state:

```
backlog_auto_status
```

If the cursor is still on the same task at the same stage, the subagent failed to call `complete_task` — treat this as a `crashed` failure and call `backlog_auto_complete_task(status="failed", fail_reason="crashed", summary="<subagent did not finalize>")` from the orchestrator yourself, then continue per `continue_on_fail`.

## 2d. Token Discipline Check

After each task, your main-context accumulation should be:
- The structured result (JSON) — ~80 tokens.
- Cursor read after — ~50 tokens.

That's ~130 tokens per task. Do **not** read task bodies, handover bodies, or lesson contents in the orchestrator main context. Only call `backlog_auto_status` and the subagent dispatch.

## 2e. Continue or Halt

Loop back to 2a. The auto state machine itself enforces continue/halt logic based on `config.continue_on_fail` — you don't need separate orchestrator branching.
