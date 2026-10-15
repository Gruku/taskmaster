# Task Lifecycle

## State Machine

```
todo → in-progress → done → archived
          ↕   ↘
     in-review  blocked → todo | in-progress | in-review | archived
```

`done` can also go back to `in-review`, an in-progress task can be unpicked back to `todo`, and an archived task can only go back to `todo`. Status moves go through the skills below; the server enforces the legal moves on tasks that have a lane, and a task with no lane is exempt except when leaving `archived`.

## What Each Status Means

| Status | Who's Active | What's Happening |
|--------|-------------|-----------------|
| **todo** | Nobody | Task is defined and waiting. May have unmet dependencies. |
| **in-progress** | The assistant | Actively being worked on. Claimed by a session. Has a worktree. |
| **in-review** | The user | Exception state: the AI work is complete, but an action only the human can perform blocks it. The task's `human_action` states it. Entering in-review without one is rejected. |
| **done** | Nobody | Work complete and the lane's required gates passed. The session summary is in the session log (`PROGRESS.md`, kept under `.taskmaster/local/` on a native store and not committed). |
| **blocked** | Nobody | Cannot proceed: external dependency, missing info, or an upstream task not done. |
| **archived** | Nobody | Cleared from the active board. Still stored, but hidden from listings. |

## Why In-Review Exists

`in-review` is an exception state, not a pipeline stage. It means the AI work is complete but an action only the human can perform blocks true completion: adding an API key, changing LLM config, granting access. The blocking action is recorded on the task as `human_action`; every write path rejects in-review without it.

Most tasks never touch in-review: they go `in-progress → done` once the lane's gates pass. Human review of done work happens downstream (review sweeps), not on the board.

## Transitions

| From | To | How | What Happens |
|------|----|-----|-------------|
| todo | in-progress | `pick-task` skill or `backlog_pick_task` | Sets `started`, claims the task for the session, creates the worktree |
| in-progress | done | `end-session` skill (`backlog_complete_task`) | Gates passed; session summary logged |
| in-progress | in-review | `end-session` skill (`backlog_complete_task`, `target_status="in-review"`) | A human-only action blocks the task; `human_action` recorded |
| in-review | done | `end-session` skill (`backlog_complete_task`) | The human action is handled (verified or user-confirmed); `human_action` cleared |
| in-review | in-progress | `pick-task` skill | Resume work after the human action is handled |
| todo / in-progress / in-review | blocked | `backlog_update_task(id, "status", "blocked")`, with the reason in `blockers` | Records that the task cannot proceed |
| blocked | todo / in-progress | `backlog_update_task(id, "status", ...)` once the blocker clears (a blocked task is not directly pickable) | Work can resume |
| blocked | done / in-review | `backlog_complete_task` | A blocked task can still be completed |
| done / blocked / todo | archived | `backlog_archive_task` | Clears the task from the board; a `todo` task needs a non-"done" reason (deprecated, duplicate, wont-fix, superseded) |
| in-progress / in-review | archived | `backlog_update_task(id, "status", "archived")` | `backlog_archive_task` refuses these two statuses |
| in-progress | todo | `backlog_update_task(id, "status", "todo")` | Unpick: the task goes back to waiting |
| archived | todo | `backlog_update_task(id, "status", "todo")` | Unarchive; the only legal move out of archived |

## Phases

Phases are sequential blocks of work that cut across epics. They provide focus by limiting what's visible and actionable.

```
planned → active → done → archived
            ↑
     (only one at a time)
```

| Concept | How It Works |
|---------|-------------|
| **One active at a time** | Only one phase can be `active`. `backlog_next_available` only shows tasks from the active phase. |
| **Tasks belong to phases** | Each task has an optional `phase` field. Tasks without a phase are "unassigned" and shown separately. |
| **Advancing** | When a phase's work is complete, `backlog_advance_phase` marks it done, archives its done tasks, and activates the next planned phase by order. It refuses while the phase has unchecked deliverables or unfinished tasks; `force=True` advances anyway and leaves those tasks in their status. |
| **Cross-cutting** | A phase can contain tasks from multiple epics. Epics are thematic (auth, api, ux); phases are temporal (foundation, core features, polish). |
| **Anchors** | Tasks can declare `anchors` — glob patterns or URLs — to say what files/systems they touch. Displayed prominently on pick. |
| **Staleness** | `last_referenced` is updated by pick/update/complete (mutations); reads do not bump it. Todo tasks stale for 14+ days are flagged in dashboards. |

### Phase + Task Workflow

1. Create phases in order: `backlog_add_phase("foundation", "Phase 1: Foundation", order=1)`
2. Assign tasks: `backlog_update_task("auth-001", "phase", "foundation")`
3. Work through the active phase's tasks
4. When done: `backlog_advance_phase` — archives done tasks, activates next (refused while tasks are unfinished; finish or move them, or pass `force=True`)
5. Repeat until all phases complete
