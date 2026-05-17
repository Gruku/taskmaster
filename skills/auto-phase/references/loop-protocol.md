# Auto-Phase — Loop Protocol (Step 2 Sub-Steps)

## Step 2: Epic-by-epic Loop Detail

For each distinct epic represented in the pending list:

1. Read `backlog_auto_status`, note the cursor task id.
2. Look up which epic that task belongs to.
3. Dispatch the auto-epic skill as a subagent with model=opus (the orchestrator-of-orchestrators uses opus):
   ```
   Agent(
     subagent_type="general-purpose",
     model="opus",
     description="Auto-epic <epic-id>",
     prompt="You are the auto-epic orchestrator dispatched by auto-phase. The auto state
   machine is already initialized at mode=phase, target=<phase-id>. The current
   cursor is at task <task-id> in epic <epic-id>.

   Your job: drive every remaining todo task of epic <epic-id> using the
   taskmaster:auto-epic skill (load it via Skill tool). Do NOT re-init the
   state — it's already set up.

   When done with this epic (or halted), return ONLY a structured summary:
   { epic_id, tasks_done, tasks_failed, summary, epic_handover_id }

   Stop when cursor moves to a task in a different epic — that signals this
   epic is done and auto-phase will dispatch the next one."
   )
   ```

4. Capture the epic-level summary.
5. After auto-epic returns, read `backlog_auto_status` again. If cursor is None or halted, exit the loop. Otherwise, the next iteration handles the next epic.

## Why this is not yet auto-roadmap

A roadmap-level skill (auto-roadmap, dispatching auto-phase per planned phase) is **out of scope**. Phase boundaries usually represent meaningful project transitions where human judgment is required (deploy, milestone review, scope change). Auto-phase is the largest unit you should automate without explicit per-step approval.
