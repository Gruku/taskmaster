---
name: pick-task
description: "Select a task to work on. Invoke when the user says 'pick a task', 'let's work on X', 'start task auth-003', 'what should I tackle next', or names a specific task ID. Sets status to in-progress, checks dependencies, creates a git worktree for isolation, and loads task context."
---

# Pick Task

Select a task to work on and set it to in-progress.

## Arguments

- `task_id` (optional) — specific task ID (e.g., `auth-003`). If omitted, presents top priorities to choose from.

## Steps

1. **If no task ID provided:**
   - Call `backlog_next_available` to get tasks ready to work on. When a milestone is active, this only returns tasks from that milestone — keeping you focused on the current block of work.
   - If no available tasks: the milestone may be complete (suggest `/advance-milestone`), or suggest adding work.
   - Present the list and ask the user to pick one.

2. **Check for parallel task overload:**
   - Call `backlog_status` to see current in-progress count.
   - If 3+ tasks are already in-progress: "It looks like you already have N tasks in-flight. Do you want to switch focus to one of those, or pick this up in parallel?" — the user is probably asking because they're blocked or bored with another task, not because they have infinite bandwidth.

3. **Dependency check (if task ID is known):**
   - Call `backlog_dependencies(task_id)` to check upstream dependencies.
   - If any dependencies are NOT done, warn: "This task depends on `{dep_id}` which is still `{status}`. Proceed anyway?"
   - Let the user decide. Do not silently skip.

4. **Once a task is selected:**
   - Call `backlog_pick_task(task_id)` — this sets status to `in-progress`, records `started` date, locks to session, and regenerates context + dashboard.
   - The tool returns task details, epic context, and recently completed tasks in the same epic.

5. **Read linked docs:**
   - If the task has a `docs` field (plan, spec, etc.), read those files to understand the existing context before writing any code.
   - This prevents agents from ignoring existing specs and plans.

6. **Git worktree creation (REQUIRED):**

   **Why worktrees?** When multiple tasks are in-flight, work on different branches can bleed together if everything happens in the same working tree. A dedicated worktree per task means you can switch tasks instantly, the review gate can diff the correct branch cleanly, and there's no risk of committing task B's changes onto task A's branch. This is the foundation of safe parallel task work.

   - The `backlog_pick_task` response includes worktree instructions. Follow them.
   - If a worktree already exists: verify the directory actually exists on disk and contains a `.git` file. If not, the worktree is orphaned — delete the stale reference with `git worktree prune` and recreate.
   - If no worktree exists: create one before writing any code.

   **Creating a worktree:**
   1. Determine the repo root. If the task has a `sub_repo` field, use that directory. Otherwise use the project root.
   2. Create: `git worktree add .worktrees/{task-id} -b feature/{task-id}` (run from the repo root)
   3. Call `backlog_update_task(task_id, "branch", "feature/{task-id}")` to record the branch.
   4. Call `backlog_update_task(task_id, "worktree", ".worktrees/{task-id}")` to record the worktree path.

   **If `git worktree add` fails:**
   - "branch already exists" — the branch was left behind from a previous attempt. Either check it out in a new worktree (`git worktree add .worktrees/{task-id} feature/{task-id}` without `-b`), or ask the user if they want to delete the stale branch and start fresh.
   - Other errors — report to the user and ask how to proceed. Don't silently skip worktree creation.

## Task Lifecycle

See `references/task-lifecycle.md` for the full state machine and transition rules. The key flow:

```
todo → in-progress → in-review → done → archived
```

## Notes

- `backlog_pick_task` is idempotent for already in-progress tasks in the same session.
- If a task is `in-review`, picking it moves it back to `in-progress` — confirm this demotion with the user first, as it means they found issues during testing and want to reopen the work.
- If a task is `blocked`, the tool will reject it. Help the user resolve blockers or change status first.
