# Init Taskmaster: analysis mode

Used when the user chose "Analyze project". The backlog is proposed first and created only after approval, so a wrong guess costs nothing.

1. Call `backlog_init(project_name)` to create the backlog (the playbook asks for the mode before this point).
2. Gather existing work:
   - Run the scan in `../check-todos/references/scan-flow.md` (task files and inline TODO/FIXME/HACK/XXX markers). Its priority mapping is the one to use here.
   - Read README.md for a roadmap, planned features or task lists.
   - Run `git log --oneline -20` to see where work has been active.
3. Synthesize a proposed backlog:
   - Group related markers into candidate areas by directory or domain; these become `backlog_area_create` calls for long-lived subsystems, not epics.
   - Within each area, propose finite epics that carry a `done_when` (a real completion condition, not "ongoing work on X").
   - Convert markers into tasks using scan-flow's priorities, and README roadmap items into tasks. Put the source `file:line` in each task's notes.
4. Show the proposal as a short text summary and ask: Create it / Adjust first / Cancel (structured-question tool if you have one). On Create, use `backlog_area_create`, `backlog_add_epic` (with `done_when`), `backlog_add_task` (a phase is required) and `backlog_add_phase`.

## Edge cases

- Hundreds of markers: group into areas by directory or domain, then create finite epics for the top areas' near-term goals rather than one task per marker.
- No markers found: proceed like a clean start.
- Monorepo: the project root is the top-level directory; tasks can point at sub-repos with the `sub_repo` option of `backlog_add_task`.
