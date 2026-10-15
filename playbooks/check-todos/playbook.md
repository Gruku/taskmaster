# Check TODOs

Find work that lives only in the code or in loose task files, and say which of it the backlog already tracks. This is the bridge from "I left a TODO" to "it's in the task system".

Scan task files (TODO.md, TODOS.md, TASKS.md, ROADMAP.md, BACKLOG.md, any depth) and inline `TODO|FIXME|HACK|XXX` markers (case-insensitive). Skip dependency, build and VCS directories and the task files themselves. Load the backlog with `backlog_search` on key terms and `backlog_list_tasks`. Sort every item into tracked, untracked, or stale candidate (a task pointing at a TODO that's gone; flag it, don't assert it). Matching rules, report template, priorities and edge cases are in `references/scan-flow.md`.

Report coverage first, then the untracked table (file:line, marker, text, suggested priority), tracked items, and stale candidates. Then ask one question: create tasks for all untracked items (grouped into epics by area, into the active phase if there is one), pick some, review the stale ones, or stop at the report. New tasks go through `backlog_add_task` (`title`, `epic` and `phase` are required; say what done looks like in `tldr`) with `Source: <file>:<line> - <comment>` in notes.
