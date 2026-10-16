# Init Taskmaster: analysis mode

Used when the user chose "Analyze project". The playbook has already checked the root and called `backlog_init`, so the backlog exists and is empty. The seed is proposed first and created only after approval, so a wrong guess costs nothing.

1. Gather existing work:
   - Run the scan in `../check-todos/references/scan-flow.md` (task files and inline TODO/FIXME/HACK/XXX markers). Its priority mapping is the one to use here.
   - Read README.md for a roadmap, planned features or task lists.
   - Run `git log --oneline -20` to see where work has been active.
2. Synthesize a proposed backlog:
   - Group related markers into candidate areas by directory or domain; these become areas for long-lived subsystems, not epics.
   - Within each area, propose finite epics that carry a `done_when` (a real completion condition, not "ongoing work on X") and one phase to hold the first tasks.
   - Convert markers into tasks using scan-flow's priorities, and README roadmap items into tasks. Put the source `file:line` in each task's `notes`.
3. Show the proposal as a short text summary and ask: Create it / Adjust first / Cancel (structured-question tool if you have one). On Create, call in this order, because each names the ones before it: `backlog_area_create(area_id, name)`, `backlog_add_epic(epic_id, name, done_when, area=...)`, `backlog_add_phase(phase_id, name)`, then `backlog_add_task(title, epic, phase, priority=..., tldr=..., notes=..., options={"area": ...})`. On Cancel, the empty backlog stays; say so.

## Edge cases

- Hundreds of markers: group into areas by directory or domain, then create finite epics for the top areas' near-term goals rather than one task per marker.
- No markers found: proceed like a clean start.
- Monorepo: the project root is the top-level repository; tasks can point at a sub-repo with `options={"sub_repo": ...}`, and the manifest's `repos` list can name them.
