# End Session

Close the session's work: record what happened against the task, move the task to its next status, leave a handover when work continues, and commit the tracking files.

`backlog_complete_task` writes the session record and the status change in one transaction, which makes it the only correct way to mark a task done or in-review. Setting `status` with `backlog_update_task` leaves no session record.

## Before closing

These feed the handover, so do them first. On a v2 backlog (`backlog_status` shows `Schema: v2`) skip the decision, handover and idea steps.

- **Decisions.** `backlog_decision(action="list", status="open", task_id=<current>)`. Ask once about all of them together; anything unanswered is carried forward (`references/v3-pre-steps.md`). The outcome goes into the handover body under "Open decisions" / "Resolved this session", not into `backlog_handover_create` arguments.
- **Handover.** Write one through `../handover/playbook.md`, without asking, when the task stays in flight (always), the session was long, or the user said "for tomorrow" or "context handoff". Skip it when the user said not to, or when the session was a small one-touch change. Carry on with the close whatever the handover's outcome.
- **Ideas.** Create every `<idea-candidate>` tag from this session with `backlog_idea_create`, without asking per item (`references/v3-pre-steps.md`).
- **Repo rules.** When the session surfaced something every assistant in this repo needs ("Y breaks if you do Z"), propose a 1-3 line addition to CLAUDE.md or AGENTS.md and add it on approval. Session-only insights belong in your own memory. When you found nothing, say nothing.
- **Desk note.** At most one `backlog_note(action="create", text=...)`, for a thought that fits no task, idea, issue or handover, and don't repeat what the handover already says. Usually none.

## Close

**Unfinished work stays in progress.** When the task's work isn't finished ("I'm done for today" in the middle of it; the handover says what is left), don't call `backlog_complete_task` and skip steps 1-6: the task stays `in-progress`, its worktree stays, and the handover is the session's record. Run steps 7 and 8, ending with "Task stays in-progress; resume with `Resume: ...`". The steps below are for finished work.

The order matters: the close is refused while bugs filed against the task are open, and worktree cleanup needs the final status.

1. **Blockers.** `backlog_context(focus=<task_id>, scope="task", include=[])` names every gate and open bug the close would refuse on. Take each open bug through the disposition in `../bug/playbook.md` (fix now, spawn a task, shelve, promote). For a missing gate, say which one and offer `../review-gate/playbook.md`; a refusal on a gate is reported, not worked around.
2. **Status.** For finished work, `done` by default; `in-review` only when a human-only action blocks the task, passed as `human_action`. Rules and examples: `references/summary-modes.md`; lifecycle: `../../references/task-lifecycle.md`.
3. **Summary.** Fill `done`, `decisions`, `issues` and `tasks_touched` from the session, with a title `{Topic}: {Brief description}`. A light session (1-2 commits, one topic) can pass `auto_summary=true` with git stats as `done`. Add a 1-2 sentence `patchnote` only for user-visible changes, and a `release` bucket if the project uses them (`references/summary-modes.md`).
4. **Complete.** `backlog_complete_task(task_id, session_title, done, decisions, issues, tasks_touched, target_status, patchnote, release, human_action)`. Bugs filed against the task and already fixed are archived by the close; say so. A bundle, or several unrelated tasks moved: `references/edge-cases.md`.
5. **Related issues.** Find the open issues linked to the task and ask whether to close each as fixed (`references/summary-modes.md`).
6. **Worktree.** For a done task, offer `git worktree remove .worktrees/{task_id}` (bundle rules: `references/edge-cases.md`). Use plain `git worktree remove`: on Windows, `--force` and `rm -rf` have wiped the parent checkout, `.git/` included.
7. **Commit** the tracking files as `chore: log session - {topic}`. This is the one commit this skill makes, because ending the session is the user's ask; code is committed separately and only on request. Legacy store: stage `.taskmaster/` (backlog, handovers, bugs, issues, ideas, decisions, tasks) and `PROGRESS.md`. Native store (`backlog_store_status` shows an `Exporter lease`): use the managed commit in `references/edge-cases.md`, not plain git.
8. **Report** "Session logged. Task is now `{target_status}`." If a handover was written, end with its `Resume: <thread> — <next_action>` line verbatim.
