# pick-task: bundle protocol

A bundle groups related tasks that touch heavily overlapping files into one shared worktree and branch. Instead of N `feature/<task-id>` worktrees, the members share `.worktrees/<slug>` on `feature/<slug>`, which avoids merge conflicts and split context when the implementation is tightly coupled.

What you see of a bundle: the pick response starts `Picking bundle <slug>: <ids>`; `backlog_get_task` shows a `bundle` field with the slug; `backlog_claim(action="status", task_id=<id>)` returns the `members`.

## Path A: the picked task already has a bundle slug

1. Skip `git worktree add .worktrees/<task-id>`. The pick response ends with the worktree command for the shared worktree; run that, because it knows whether an earlier member already created it.
2. Announce the membership in one block:
   ```
   Bundle: <slug> — <N> tasks sharing this worktree
     • <task-id-1>: <title-1>  ← (this task, now in-progress)
     • <task-id-2>: <title-2>  (also in-progress)
   Worktree: .worktrees/<slug>  Branch: feature/<slug>
   ```
3. Record branch and worktree on this task:
   ```
   backlog_update_task(<task_id>, "branch", "feature/<slug>")
   backlog_update_task(<task_id>, "worktree", ".worktrees/<slug>")
   ```
   The single pick already put every member in progress; don't pick the siblings again.
4. Members with mixed lanes: apply the strictest lane (full, then standard, then express) to the whole shared worktree session.

When the shared worktree path is missing but the branch exists, run `git worktree add .worktrees/<slug> feature/<slug>` (no `-b`). When the branch is gone too, run `git worktree add .worktrees/<slug> -b feature/<slug>` from the repo root.

## Path B: no bundle slug, check for overlap

Run this after the solo worktree exists, only in the cases the playbook names (critical or high task, a task with `anchors` whose context answer lists other todo `siblings` in the same epic, or the user asked to cluster; `siblings` only says which tasks share the epic, and the blast-radius call below is what finds real file overlap).

1. `backlog_blast_radius(<task_id>, mode="predictive", structured=True)` returns `overlapping_tasks: [{task_id, title, status, shared_paths}]`.
2. Keep the tasks with `status == "todo"` and strong overlap: two or more shared paths, or a single shared path that is a core module (not a test fixture or config leaf).
3. Generate one slug for the whole sweep, `<epic-prefix>-bundle-<NNNN>` (the picked task's epic prefix, else `tm`), and set it on every kept task and on the picked task:
   ```
   backlog_update_task(<candidate_id>, "bundle", "<slug>")
   backlog_update_task(<task_id>, "bundle", "<slug>")
   ```
4. Announce each swept task in one line, because that line is the user's chance to veto it: `also sweeping <candidate_id> in, same files (<shared_paths summary>)`. When the user objects, clear it with `backlog_update_task(<candidate_id>, "bundle", "")`; if only the picked task remains, clear its slug too.
5. A swept task picked later carries the slug, so Path A applies.

No overlapping tasks: say nothing; the solo worktree stands.

## Edge cases

- All members already in progress: the pick returns the task normally and lists the members; announce them and don't transition again.
- A mixed-status bundle (some done, some todo): announce the in-progress and todo members, and note the done ones as previously completed.
