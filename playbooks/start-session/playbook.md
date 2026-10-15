# Start Session

Orient the user at the start of a session: where work stands, what waits on them, what to do next. Keep it to a glance, well under a screen; `--deep` adds the full briefing. When the user's opening message already asks for specific work, skip the briefing and route that request (`../taskmaster/playbook.md`); a one-line note of anything waiting on them is enough, because a dashboard in front of a concrete request only delays it.

## Gather

These reads don't depend on each other; call them together.

- `backlog_status()`: dashboard, in-progress and in-review counts, active phase, stale tasks. If it fails, check whether `.taskmaster/backlog.yaml` exists: when it doesn't, offer `../init-taskmaster/playbook.md`; when it does, the MCP server is probably not registered (`.mcp.json`).
- `backlog_thread_list()`: the open lines of work (the name is the resume token, plus tldr and next action). Fetch a thread's newest handover only when the user picks it, with `backlog_thread_resume("<name>")`. When the user already named a thread or pasted a handover id, resume it directly instead of showing the board.
- `backlog_note(action="list")`: the user's notes-to-self. Show them; change or archive one only when the user asks.
- `backlog_list_tasks(status="in-review")`, only when status shows in-review tasks: each row's `waiting-on-human:` is what the user has to do.
- `backlog_linear(action="status")`, only when `.taskmaster/linear.yaml` exists.

## Brief

- **Open threads:** one line each, `name — tldr → next_action`.
- **Resuming:** in-progress tasks.
- **Waiting on you:** in-review tasks with their human action. When the user says one is handled, or you can verify it (the env var now exists), close it with `backlog_complete_task(id, target_status="done", ...)`; that clears the human action and logs the session record.
- **Your desk:** notes, pinned first, one line each.
- Phase progress (done/total), stale tasks, and any epic whose tasks are all done (suggest `backlog_archive_epic`).
- **Suggested next:** the first available task in the active phase.
- A footer line only when there is something to say: the Linear queue or permanent failures (details in `../linear/playbook.md`); legacy `L-*.md` files in `.taskmaster/lessons/` (a directory listing shows them; `references/legacy-lessons.md` converts them).

Close with a short question about what to work on, and mention `--deep` for the full briefing.

An empty backlog (no epics, no tasks): skip the dashboard, ask what the main workstreams are, and create them with `backlog_add_epic`, then tasks.

## Legacy in-review tasks

An in-review task with no human action predates the human-gate semantics (before 4.6.0, in-review meant "user tests"). When any exist, list them (id, title) once and offer to close them all to `done` with `backlog_batch_update` (op `complete`); a task the user says is genuinely blocked gets `backlog_update_task(id, "human_action", "...")` instead. Report any close a gate refuses and leave that task as it is.

## Deep mode (`--deep`)

When the user asks for `--deep`, "full briefing" or "give me everything", do the glance first, then follow `references/deep-mode.md`. Deep mode runs only on that explicit ask.

Apart from the closes the user approves, this playbook only reads.
