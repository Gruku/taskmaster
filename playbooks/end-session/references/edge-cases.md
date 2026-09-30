# End-Session Edge Cases

Situations where the default flow needs adjustment.

## No in-progress task

The session was exploratory — planning, research, no task picked. Two options, ask the user which they'd prefer:

- Call `backlog_complete_task` on any task that changed status this session (the changelog still wants an owner).
- Skip the tool entirely and append a free-form entry to PROGRESS.md by hand.

## Not in a git repo

Skip step 8 (commit). The tracking files were updated locally; tell the user they're staged but uncommitted.

## Native store

A native store shows an `Exporter lease` line in `backlog_store_status`. There, step 8 does not stage `.taskmaster/` by hand: a plain `git commit` of those files bypasses the coordinator. Commit them with Taskmaster's managed Git command, run in Taskmaster's Python environment from the project directory:

```
python -m taskmaster.coordinator.git_cli commit -m "chore: log session - {topic}"
```

It syncs first, then commits exactly the exported `.taskmaster/` files and nothing else you have staged. Commit code changes separately with plain Git, leaving `.taskmaster/` out. If it refuses with `projections are not synchronized` or `Git lock present`, report the listed paths to the user rather than working around it. `PROGRESS.md` lives under `.taskmaster/local/` and is never committed there. Details: Taskmaster's `docs/native-store.md`, section *Git*.

## Multiple tasks changed

Pick one **primary** task for `backlog_complete_task` — that one gets the full changelog entry with done/decisions/issues. For each secondary task whose status also moved, call `backlog_update_task` to flip the status only. Secondary status changes do not get changelog entries, which is correct: the session record belongs to the primary task.

**Exception — Task Bundle.** When the active session belongs to a bundle (`_get_session_bundle()` returns non-None), the status-only shortcut above does NOT apply. Instead:

1. Call `backlog_complete_task(member, …)` for every passing bundle member — each member gets its own full completion record.
2. On a single merge event, loop `backlog_record_merge(member, rung, sha)` over all members (same rung + sha).
3. Offer `git worktree remove .worktrees/<slug>` only when ALL members are `done` or descoped; the shared worktree is keyed by bundle slug, not by any individual task-id. Never use `--force` or `rm -rf`.
