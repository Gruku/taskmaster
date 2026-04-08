# Task Lifecycle

## State Machine

```
todo → in-progress → in-review → done → archived
         ↑              ↑                   ↑
    (pick-task)    (review-gate)      (archive after
                                      user confirms)
```

## What Each Status Means

| Status | Who's Active | What's Happening |
|--------|-------------|-----------------|
| **todo** | Nobody | Task is defined and waiting. May have unmet dependencies. |
| **in-progress** | Claude | Actively being worked on. Locked to a session. Has a worktree. |
| **in-review** | The user | Implementation is complete and automated checks passed. The user needs to manually test and confirm it works. |
| **done** | Nobody | User tested and confirmed. Session summary has been logged to PROGRESS.md. |
| **blocked** | Nobody | Cannot proceed — external dependency, missing info, or upstream task not done. |
| **archived** | Nobody | Permanently cleared from the active board. Still in YAML but hidden from listings. |

## Why In-Review Exists

The `in-review` stage is a **user-testing gate**. It exists because automated tests can't catch everything — UI behavior, tool output quality, integration feel. When a task moves to `in-review`, it means "Claude did everything it can; now the human needs to verify this actually works the way they want."

Tasks should NOT skip from `in-progress` directly to `done`. The in-review checkpoint ensures the user has explicitly signed off.

## Transitions

| From | To | How | What Happens |
|------|----|-----|-------------|
| todo | in-progress | `pick-task` skill or `backlog_pick_task` | Sets `started` timestamp, locks to session, creates worktree |
| in-progress | in-review | `review-gate` skill | Runs quality checks, then transitions if gates pass |
| in-progress | done | `end-session` skill | Skips review (warned). Logs session summary. |
| in-review | done | `end-session` skill | User confirmed it works. Logs session summary. |
| in-review | in-progress | `pick-task` skill | Demotes back (user found issues during testing) |
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
| **Staleness** | `last_referenced` is auto-updated by get/pick/update/complete. Todo tasks stale for 14+ days are flagged in dashboards. |

### Phase + Task Workflow

1. Create phases in order: `backlog_add_phase("foundation", "Phase 1: Foundation", order=1)`
2. Assign tasks: `backlog_update_task("auth-001", "phase", "foundation")`
3. Work through the active phase's tasks
4. When done: `backlog_advance_phase` — archives done tasks, activates next
5. Repeat until all phases complete
