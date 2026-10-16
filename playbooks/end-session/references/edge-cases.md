# End-Session Edge Cases

Situations where the default close needs adjusting.

## No in-progress task

The session was exploratory: planning, research, no task picked. Don't complete a task just to give the session record an owner; the handover (`task_ids: []`) is the record. A task the session actually finished goes through the normal close. Record nothing else, and don't edit `PROGRESS.md` by hand: the backlog changes only through the tools, and on a native store `PROGRESS.md` is generated.

## Not in a git repo

Skip the commit step. The tracking files were updated locally; tell the user they are uncommitted.

## Native store

A native store shows an `Exporter lease` line in `backlog_store_status`. There, the commit step does not stage `.taskmaster/` by hand: a plain `git commit` of those files bypasses the coordinator. Commit them with Taskmaster's managed Git command, run in Taskmaster's Python environment from the project directory:

```
python -m taskmaster.coordinator.git_cli commit -m "chore: log session - {topic}"
```

It syncs first, then commits exactly the exported `.taskmaster/` files and nothing else you have staged. When the user asks you to commit code, do it in a separate plain Git commit with `.taskmaster/` left out. If it refuses with `projections are not synchronized` or `Git lock present`, report the listed paths to the user rather than working around it. `PROGRESS.md` lives under `.taskmaster/local/` and is not committed there. Details: Taskmaster's `docs/native-store.md`, section *Git*.

## Several tasks changed

Pick one primary task for `backlog_complete_task`; it gets the full session record. For each secondary task whose status also moved, call `backlog_update_task` to flip the status only. Secondary status changes get no session record, because the record belongs to the primary task.

**Bundle exception.** When the session belongs to a bundle, the status-only shortcut does not apply. Find the members from the pick response's `Picking bundle <slug>: ids` line, or `backlog_claim(action="status", task_id=<id>)` and read `members`. Then:

1. `backlog_complete_task(member, ...)` for every passing member; each gets its own full record.
2. On a single merge event, loop `backlog_record_merge(member, rung, sha)` over all members with the same rung and sha.
3. Offer `git worktree remove .worktrees/<slug>` only when every member is `done` or descoped. The shared worktree is keyed by bundle slug, not by any one task id. Use plain `git worktree remove`, as in the playbook's Worktree step.
