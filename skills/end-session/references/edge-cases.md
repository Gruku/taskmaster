# End-Session Edge Cases

Situations where the default flow needs adjustment.

## No in-progress task

The session was exploratory — planning, research, no task picked. Two options, ask the user which they'd prefer:

- Call `backlog_complete_task` on any task that changed status this session (the changelog still wants an owner).
- Skip the tool entirely and append a free-form entry to PROGRESS.md by hand.

## Not in a git repo

Skip step 8 (commit). The tracking files were updated locally; tell the user they're staged but uncommitted.

## Multiple tasks changed

Pick one **primary** task for `backlog_complete_task` — that one gets the full changelog entry with done/decisions/issues. For each secondary task whose status also moved, call `backlog_update_task` to flip the status only. Secondary status changes do not get changelog entries, which is correct: the session record belongs to the primary task.
