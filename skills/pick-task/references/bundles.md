# pick-task — Bundle Protocol

Reference for the bundle pickup and detection-fallback logic introduced in taskmaster 3.18+.

---

## What is a bundle?

A **bundle** groups related tasks that touch heavily overlapping files into a single shared worktree and branch. Instead of N separate `feature/<task-id>` worktrees, a bundle uses one `feature/<slug>` worktree for all members. This prevents merge conflicts and context fragmentation when implementation is tightly coupled.

A task has a bundle when `backlog_pick_task` returns a `_session_bundle` key (or the slim task body includes a `bundle` field with a non-null slug).

---

## Path A — Bundle pickup (bundle slug already set)

When `backlog_pick_task` returns a response that includes bundle membership:

1. **Do not** run `git worktree add .worktrees/<task-id>`. The backend already provisioned (or will provision) `.worktrees/<slug>` on the shared branch `feature/<slug>`.
2. **Follow the returned `worktree_instruction` verbatim.** The backend message is the canonical source — it reflects whether the worktree already exists for a prior member or needs creating now.
3. Announce membership in one block:
   ```
   Bundle: <slug> — <N> tasks sharing this worktree
     • <task-id-1>: <title-1>  ← (this task, now in-progress)
     • <task-id-2>: <title-2>  (also in-progress)
     • <task-id-3>: <title-3>  (also in-progress)
   Worktree: .worktrees/<slug>  Branch: feature/<slug>
   ```
4. Record branch and worktree on this task:
   ```
   backlog_update_task(<task_id>, "branch", "feature/<slug>")
   backlog_update_task(<task_id>, "worktree", ".worktrees/<slug>")
   ```
   The backend sets all bundle members to in-progress in a single `backlog_pick_task` call — no need to call `backlog_pick_task` again for sibling tasks.
5. Lane: if bundle members have mixed lanes, apply the strictest (full > standard > express) to the entire shared worktree session.

**Orphaned shared worktree:** If the worktree path is missing but the branch exists, run `git worktree add .worktrees/<slug> feature/<slug>` (not `-b`, branch already exists). If the branch is also missing, run `git worktree add .worktrees/<slug> -b feature/<slug>` from repo root.

---

## Path B — Detection fallback (no bundle slug set)

When the picked task has no bundle slug, run the detection fallback **after** the solo worktree is created (Step 8 solo path complete).

### B1. Run blast radius

```
backlog_blast_radius(<task_id>, mode="predictive", structured=True)
```

Returns `overlapping_tasks: [{task_id, title, status, shared_paths}]`.

### B2. Filter candidates

Keep tasks where:
- `status == "todo"` (not in-progress, not done, not archived)
- `shared_paths` overlap is **strong** — defined as ≥2 shared paths OR the single shared path is a core module (not a test fixture or config leaf)

### B3. Act on authority

For each candidate that passes the filter:
1. Generate a slug: `<epic-prefix>-bundle-<NNNN>` (use the picked task's epic prefix if available, else `tm`).
2. Set the bundle slug on the candidate: `backlog_update_task(<candidate_id>, "bundle", "<slug>")`.
3. Also set it on the picked task if not already set: `backlog_update_task(<task_id>, "bundle", "<slug>")`.
4. Announce in one line per swept task — this is the **veto window**:
   ```
   also sweeping <candidate_id> in, same files (<shared_paths summary>)
   ```
5. If the user objects ("don't include that one"), call `backlog_update_task(<candidate_id>, "bundle", null)` to clear it, and if only the picked task remains, clear its slug too.

**Do not ask before acting.** The announcement IS the veto window. Claude acts; user vetoes if needed.

### B4. Subsequent picks of swept tasks

When a swept candidate is later picked via `backlog_pick_task`, it will have the bundle slug set — Path A applies automatically.

---

## Description-trigger mapping

Bundle/cluster phrasing that routes to pick-task:

| User says | Action |
|---|---|
| "pick this bundle" | pick the named or most-recent bundle's first pending task |
| "work on the bundle" | same — resolve bundle from context |
| "cluster these tasks" | detection fallback on named tasks if IDs provided |
| "start the cluster" | pick first pending task in cluster, announce members |

---

## Edge cases

**Bundle with all members already in-progress:** `backlog_pick_task` returns the task normally; the `_session_bundle` block will still include member IDs. Announce but skip re-transitioning.

**Mixed-status bundle (some done, some todo):** Only in-progress + todo members are included in the active announcement. Done/archived members are noted as "previously completed."

**Bundle slug collision:** If two unrelated tasks already have the same bundle slug (should not happen, but defensive), warn: "Bundle slug <slug> already in use by <task_id>. Generating a new slug." Generate a new one with a `-2` suffix.

**No overlapping tasks found in fallback:** Skip silently. Do not announce "no bundle found" — the solo worktree stands.
