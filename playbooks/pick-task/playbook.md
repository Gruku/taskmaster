# Pick Task

Put one task in progress for this session and set up its worktree, surfacing anything that blocks it before code is written. The glance load is the default; `--deep` adds the full body, blast radius and handover bodies (`references/deep-mode.md`). The lifecycle and what each status means: `../../references/task-lifecycle.md`.

## Choose the task

- **Task id given:** use it.
- **"Continue" or "resume" with no id:** call `backlog_thread_list()`. With one open thread, resume it with `backlog_thread_resume(<name>)`, take the first id in its handover's `task_ids`, and check it with `backlog_get_task(<id>)`. With several, take the thread whose newest handover's first task is still in progress and name the others in one line; ask which only when none or more than one fits (a pasted thread name or handover id also works). Say which task and thread you're continuing and carry on; the user can redirect. A done or archived task, or no threads, falls through to the next bullet. v2 backlogs have no threads; skip this.
- **No id:** `backlog_next_available` (phase-filtered when a phase is active); present the top few and ask. When it's empty, suggest `backlog_advance_phase` or adding work.
- **A task this session already holds**, resuming after a pause or compaction: `references/v3-context-loading.md`, no re-pick.

## Check, then pick

Call `backlog_claim(action="status")` and `backlog_context(focus=<task_id>, scope="task", include=["handovers", "issues", "siblings"])` together.

- Three or more claims already held: mention it in one line and continue.
- `clear: false`: `mandatory.blockers` lists what stands in the way (dependencies, gates, open bugs, handovers asking for an action, a human action, a peer's claim; `unknown` counts as blocked). When the user asked for this task, report the blockers in one line and carry on with the work. Stop and ask when a blocker protects state the user owns: a peer session's live claim, an in-review task's human action, or a `spec_review` verdict of `fail`. When the pick came from "what should I work on", show the blockers and let the user choose.

Then `backlog_pick_task(<task_id>)`. It sets the task in-progress and locks it to this session, and it is idempotent for a task already in progress. Picking an in-review task moves it back to in-progress, which means its human action is handled, so confirm that first. A blocked task is refused. A task locked to another session takes `force=True`, and only when the user says so.

For a task with a lane whose spec or body is already written, record the spec gate: `backlog_record_gate(<task_id>, "spec", status="done")`.

## Brief

Build it from the context answer, without further calls: open handovers (count and the latest `next_action`; read a substantial one with `backlog_handover_get`), open P0/P1 issues, and linkage such as `depends_on: T-002 · fixes: ISS-007` (add a Linear pill when `tracker_id` starts with `linear-`: the third hyphen-separated segment, uppercased). For critical and high tasks, summarise an existing `spec_review` verdict, suggest `../spec-review/playbook.md` when a spec or plan exists without one, and show `anchors` as the scope to stay inside. Read the files under the task's `docs` before writing code.

## Worktree

The pick response ends with the worktree command. Run it, then record what it made with `backlog_update_task(<id>, "branch", ...)` and `backlog_update_task(<id>, "worktree", ...)`, because end-session cleanup and the merge hooks find the work through those fields. Write the task's code in that worktree, not on the main branch.

- **Solo:** `git worktree add .worktrees/{task-id} -b feature/{task-id}`. A recorded worktree that is gone: `git worktree prune`, then recreate it.
- **Bundle:** the response starts `Picking bundle <slug>: <ids>`. Every member shares `.worktrees/<slug>` on `feature/<slug>`, and that one pick put them all in progress. Announce the members; `references/bundles.md` (Path A) has the details. After a compaction, `backlog_claim(action="status", task_id=<id>)` lists the members, and `backlog_get_task` shows the `bundle` field.
- **Overlap check** (`references/bundles.md`, Path B): run it after a solo pick only when the task is critical or high, it has `anchors` and the context answer's `siblings` lists other todo tasks in its epic (siblings share an epic, not necessarily files; Path B checks the files), or the user asked to cluster.

Change the backlog only through `backlog_*` tools; a native store's coordinator owns the files, and a hand edit takes effect only after `backlog_sync()`.
