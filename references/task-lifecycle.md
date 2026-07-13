# Task Lifecycle

## State Machine

```
todo → in-progress → in-review → done → archived
         ↑              ↑                   ↑
    (pick-task)  (end-session, when      (archive)
                    human-blocked)
```

## What Each Status Means

| Status | Who's Active | What's Happening |
|--------|-------------|-----------------|
| **todo** | Nobody | Task is defined and waiting. May have unmet dependencies. |
| **in-progress** | Claude | Actively being worked on. Locked to a session. Has a worktree. |
| **in-review** | The user | Exception state: AI work complete, but an action only the human can perform blocks it. The task's `human_action` states it. Entering in-review without one is rejected. |
| **done** | Nobody | Claude complete + required gates passed. Session summary logged to PROGRESS.md. |
| **blocked** | Nobody | Cannot proceed — external dependency, missing info, or upstream task not done. |
| **archived** | Nobody | Permanently cleared from the active board. Still in YAML but hidden from listings. |

## Why In-Review Exists

`in-review` is an **exception state**, not a pipeline stage. It means the AI work is complete but an action only the human can perform blocks true completion — adding an API key, changing LLM config, granting access. The blocking action is recorded on the task as `human_action`; every write path rejects in-review without it.

Most tasks never touch in-review: they go `in-progress → done` once the lane's gates pass. Human review of done work happens downstream (review sweeps), not on the board.

## Transitions

| From | To | How | What Happens |
|------|----|-----|-------------|
| todo | in-progress | `pick-task` skill or `backlog_pick_task` | Sets `started` timestamp, locks to session, creates worktree |
| in-progress | done | `end-session` skill | Gates passed; session summary logged |
| in-progress | in-review | `end-session` skill | A human-only action blocks the task; `human_action` recorded |
| in-review | done | `start-session` / `end-session` | Human action handled (verified or user-confirmed); `human_action` cleared |
| in-review | in-progress | `pick-task` skill | Resume work after the human action is handled |
| done | archived | `backlog_archive_task` | Clears from board. Reason recorded. |
| todo/blocked | archived | `backlog_archive_task` | Requires non-"done" reason (deprecated, duplicate, wont-fix, superseded) |

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
| **Advancing** | When a phase's work is complete, `backlog_advance_phase` marks it done, archives its done tasks, and activates the next planned phase by order. |
| **Cross-cutting** | A phase can contain tasks from multiple epics. Epics are thematic (auth, api, ux); phases are temporal (foundation, core features, polish). |
| **Anchors** | Tasks can declare `anchors` — glob patterns or URLs — to say what files/systems they touch. Displayed prominently on pick. |
| **Staleness** | `last_referenced` is updated by pick/update/complete (mutations); reads do not bump it. Todo tasks stale for 14+ days are flagged in dashboards. |

### Phase + Task Workflow

1. Create phases in order: `backlog_add_phase("foundation", "Phase 1: Foundation", order=1)`
2. Assign tasks: `backlog_update_task("auth-001", "phase", "foundation")`
3. Work through the active phase's tasks
4. When done: `backlog_advance_phase` — archives done tasks, activates next
5. Repeat until all phases complete
