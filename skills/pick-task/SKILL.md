---
name: pick-task
description: "Select a task to work on. Invoke when the user says 'pick a task', 'let's work on X', 'start task auth-003', 'what should I tackle next', 'continue this task', 'continue where we left off', or names a specific task ID. Sets status to in-progress, checks dependencies, creates a git worktree, and loads task context."
---

# Pick Task

Select a task to work on and set it to in-progress.

## Arguments

- `task_id` (optional) — specific task ID (e.g., `auth-003`). If omitted, presents top priorities to choose from — or, on v3 backlogs with a continue-style trigger, auto-resolves via the latest handover (see step 0).

## Steps

0. **(v3) "Continue this task" auto-resolve.** If the user said "continue this task", "continue where we left off", "resume the work", "pick up from yesterday", or similar, AND no explicit `task_id` was given, attempt to auto-resolve before prompting:
   - Call `backlog_handover_latest()`. If it returns "No handovers yet.", fall through to step 1 (treat as a regular pick).
   - From the latest handover frontmatter, take the first id in `task_ids`. If none, fall through to step 1.
   - Call `backlog_get_task(<that id>)`. If status is `done` or `archived`, fall through to step 1 (the handover's task is already finished — better to surface the picker than reopen it).
   - Otherwise: confirm with the user once — "Continuing `<task_id>` from the `<YYYY-MM-DD>` handover (\"<tldr>\"). Right task?" Default Yes. On confirmation, jump straight to step 5 (call `backlog_pick_task`).
   - On v2 backlogs (no handover index), skip this step silently.

1. **If no task ID provided:**
   - Call `backlog_next_available` to get tasks ready to work on. When a phase is active, this only returns tasks from that phase — keeping you focused on the current block of work.
   - If no available tasks: the phase may be complete (suggest `/advance-phase`), or suggest adding work.
   - Present the list and ask the user to pick one.

2. **Check for parallel task overload:**
   - Call `backlog_status` to see current in-progress count.
   - If 3+ tasks are already in-progress: "It looks like you already have N tasks in-flight. Do you want to switch focus to one of those, or pick this up in parallel?" — the user is probably asking because they're blocked or bored with another task, not because they have infinite bandwidth.

3. **Dependency check (if task ID is known):**
   - Call `backlog_dependencies(task_id)` to check upstream dependencies.
   - If any dependencies are NOT done, warn: "This task depends on `{dep_id}` which is still `{status}`. Proceed anyway?"
   - Let the user decide. Do not silently skip.

4. **Show anchors, spec-review status, and predicted blast radius:**
   - If the task has `anchors`, display them prominently:
     "This task is anchored to `src/auth/**`. Expected at `localhost:3000/api/auth`."
   - Remind: "If you find yourself editing files outside these anchors, double-check you're working on the right target."
   - **Spec-review check (critical/high tasks only):**
     - If `task.spec_review` is present, summarize: "Spec reviewed {timestamp} — verdict: {verdict} (codex: {yes/no}, critical: N, important: N)." If verdict is `fail`, escalate: "Spec-review FAILED — implementation should not start until critical findings are addressed. Override?"
     - If `task.spec_review` is absent and the task has a `docs.spec` or `docs.plan`: WARN — "No spec-review on record for this {priority} task. Run `taskmaster:spec-review` first?" Don't block; let the user decide.
     - For medium/low tasks: skip silently (spec-review isn't expected at those priorities).
   - **Predicted blast radius:**
     - If `task.spec_review` exists, the predictive analysis was already done during spec-review. Show the one-line summary from `backlog_blast_radius(task_id, mode="predictive")` and reference the prior review: "Full predictive analysis in spec-review record."
     - Otherwise, call `backlog_blast_radius(task_id, mode="predictive")` and display the full structured block for critical/high (single-line for medium/low).
   - If overlapping in-progress tasks are found in either path, highlight them: "Heads up — `{task_id}` is actively being worked on in the same area. Coordinate to avoid conflicts."

5. **Once a task is selected:**
   - Call `backlog_pick_task(task_id)` — this sets status to `in-progress`, records `started` date, locks to session, and regenerates context + dashboard.
   - The tool returns task details, epic context, and recently completed tasks in the same epic.
   - **Note the Schema line** at the top of `backlog_status` output (`**Schema:** v<N>`). Steps 5a–5c below activate only when `Schema: v3` (or higher). On v2 backlogs, skip them and proceed to step 6. The Schema line is the *effective* version — a backlog with v3 entity content reports v3 even when the `schema_version` marker is missing.

5a. **(v3) Related handovers.** Call `backlog_handover_list(task_id=<task_id>, limit=3)` to get tldrs (not full bodies) for up to the 3 most recent handovers that referenced this task. The list output is one line per handover with id + tldr — keep all 3 in working context (~150 tokens total).

   Surface to user briefly: "3 prior handovers reference this task. Latest: 2026-04-25 — `<tldr>`. Fetch a full body with `backlog_handover_get <id>` if you need the decisions in detail."

   Only fetch a full body via `backlog_handover_get` when (a) the user asks for it, or (b) the latest handover's `session_kind` is `context-handoff` AND `next_action` is non-trivial (in which case load that one body so you know exactly where to resume). Don't preload all 3 bodies — that's the 600-token foot-gun the budget block guards against.

5b. **(v3) Related issues.** If the task's frontmatter has `related_issues: [...]`, surface them so the user knows what bugs this task is intended to fix or interacts with:
   ```
   Linked issues:
   - ISS-014 (P1, open) Login accepts whitespace password
   - ISS-019 (P2, fixed) — already resolved by features-007
   ```
   Read body of any open P0/P1 entries via `backlog_issue_get` to inform implementation.

5c. **(v3) Trigger-matched lessons.** Call `backlog_lesson_match(task_title=<title>, touched_files=<files>)` where `touched_files` is informed by `task.anchors` (file globs the task is expected to touch). The tool returns up to 3 best-match lessons (sorted by reinforce_count desc).
   - For each match, fetch the full body via `backlog_lesson_get <id>` and keep it in working context for the duration of this task.
   - Surface to user briefly: "3 lessons match this task: L-007 (gotcha) auth/session.ts read-before-edit, L-014 (anti-pattern) avoid raw SQL, L-022 (pattern) test names format. Loaded."
   - **Call `backlog_lesson_reinforce <id>` only on successful application** during work — not on load, and not for lessons that didn't end up being relevant.

6. **Read linked docs:**
   - If the task has a `docs` field (plan, spec, etc.), read those files to understand the existing context before writing any code.
   - This prevents agents from ignoring existing specs and plans.

7. **Git worktree creation (REQUIRED):**

   **Why worktrees?** When multiple tasks are in-flight, work on different branches can bleed together if everything happens in the same working tree. A dedicated worktree per task means switching tasks is instant, the review gate can diff the correct branch cleanly, and there's no risk of committing task B's changes onto task A's branch. This is the foundation of safe parallel task work.

   - The `backlog_pick_task` response includes worktree instructions. Follow them.
   - If a worktree already exists: verify the directory exists on disk and contains a `.git` file. If not, the worktree is orphaned — `git worktree prune` and recreate.
   - If no worktree exists: create one before writing any code.

   **Creating a worktree:**
   1. Determine the repo root. If the task has a `sub_repo` field, use that directory. Otherwise use the project root.
   2. Create: `git worktree add .worktrees/{task-id} -b feature/{task-id}` (run from the repo root).
   3. Call `backlog_update_task(task_id, "branch", "feature/{task-id}")` to record the branch.
   4. Call `backlog_update_task(task_id, "worktree", ".worktrees/{task-id}")` to record the worktree path.

   **Submodules:** A PostToolUse hook (`worktree-submodule-init.sh`) automatically initialises submodules and fetches from the main checkout after `git worktree add`. No manual init needed here. However, **before removing the worktree or merging**, submodule commits must be fetched back to the main checkout:
   ```
   git -C <main-checkout>/<submodule> fetch <worktree>/<submodule>
   ```
   The hook prints a reminder of this command at remove-time. If there are no submodules, the hook is a no-op.

   **If `git worktree add` fails:**
   - "branch already exists" — the branch was left behind from a previous attempt. Either check it out in a new worktree (`git worktree add .worktrees/{task-id} feature/{task-id}` without `-b`), or ask the user to confirm deleting the stale branch and restart with `-b`.
   - Other errors — report verbatim to the user and ask how to proceed. Never silently skip worktree creation; that breaks the isolation invariant the rest of the flow depends on.

## Task Lifecycle

```
todo → in-progress → in-review → done → archived
```

`in-review` means "Claude is done, user tests now." `done` means "user confirmed it works."

## Reclaiming a locked task

When `backlog_pick_task` returns a lock conflict (task locked by another session), the previous session likely ended without releasing the lock. **Do not** manually edit `backlog.yaml` or call `backlog_update_task` to change `locked_by` — that bypasses the atomic guarantee. Instead:

1. Call `backlog_pick_task(task_id, force=true)` — this reclaims the lock in a single atomic call.
2. Verify the existing worktree is still valid (directory exists, `.git` file present). If orphaned, `git worktree prune` and recreate.

## Notes

- `backlog_pick_task` is idempotent for already in-progress tasks in the same session.
- When a task is `in-review`, picking it moves it back to `in-progress` — confirm this demotion with the user first, since it means they found issues during testing and want to reopen the work.
- When a task is `blocked`, the tool rejects it. Help the user resolve blockers or change status first.

## Additional Resources

- **`references/v3-context-loading.md`** — token budget for steps 5a–5c, and how this skill composes with active auto-mode runs.
