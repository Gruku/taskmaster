# Taskmaster Router

Taskmaster keeps the backlog, handovers and gate records current so the next session can pick up where this one stopped. Route the request, do the bookkeeping that route needs, and get on with the work.

Each playbook below is also a native skill on Claude Code/ZCode: `taskmaster:<name>`, where `<name>` is the playbook directory name.

## Direct or tracked

Make the change directly (no playbook, no backlog writes) when the user asked for this specific change, it fits in one sitting, it has no open design decision, and it touches roughly three files or fewer. Everything else is tracked. If a direct change grows (a design question appears, follow-ups pile up, or it won't finish this session), say so in one line and continue in the tracked lane; the work done so far carries over.

Git-only operations (commit, push, branch) and dedicated PR security reviews don't go through taskmaster. Process skills (brainstorming, TDD, debugging) still apply inside the tracked lane; this router only decides what gets recorded.

## Tracked routes

| The user wants | Go to |
|---|---|
| Orientation: "orient me", "show the backlog", "what's going on" | `../start-session/playbook.md` |
| A specific task, "what should I work on", "continue" | `../pick-task/playbook.md` |
| Tracked work while a task is in progress | Work in that task's worktree |
| Tracked work with no task in progress | Find the matching task (`backlog_search`) and pick it. If none fits, add one with `backlog_add_task` (title, epic, phase, and a `tldr` that says when it is done), say which epic and id in one line, and pick it |
| A spec or design challenged | `../spec-review/playbook.md` |
| "Is this ready?", "review gate" | `../review-gate/playbook.md` |
| To close a task or end the session | `../end-session/playbook.md` |
| A handover, "for tomorrow", without closing anything | `../handover/playbook.md` |
| To log a defect | `../bug/playbook.md`; `../issue/playbook.md` only when recurring, systemic or P0/P1-outstanding evidence is cited (`../bug/references/bug-vs-issue.md`) |
| To save an idea | `../add-idea/playbook.md` |
| A choice between options | `../decision/playbook.md` |
| Setup, v3 migration, TODO audit, Linear | `../init-taskmaster/`, `../migrate-v3/`, `../check-todos/`, `../linear/` (each `playbook.md`) |
| A status, search, phase or single-entity read | The `backlog_*` tool directly, e.g. `backlog_get_task("T-001", sections=["plan"])`, `backlog_handover_get("HND-012")`, `backlog_issue_get("ISS-014", verbose=True)` |

Only when the table doesn't settle the route: rarer rows in `references/routing-table.md`; close calls (handover vs end-session, bug vs issue, idea vs note) in `references/disambiguation.md`. Several intents in one message: handle them in order.

**Verifying writes.** `[seq N]` = committed; `(export pending: …)` = committed, file pending; `No change … already …` (no seq) = already stored. `backlog_store_status` shows dirty or quarantined files; hand edits need `backlog_sync()`.
