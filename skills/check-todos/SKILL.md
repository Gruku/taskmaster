---
name: check-todos
description: "Scan the codebase for TODO/FIXME/HACK/XXX comments and cross-reference with the backlog. Invoke when the user says 'check todos', 'are my todos tracked', 'scan for todos', 'todo audit', 'what's untracked', or wants to make sure inline code comments are captured in the task system."
---

# Check TODOs

Scan the codebase for inline work markers (TODO, FIXME, HACK, XXX) and cross-reference them with the backlog to find what's tracked, what's missing, and what's stale.

This is the bridge between "I left a TODO in the code" and "it's actually in the task system."

## Steps

1. **Scan for task files.** Use Glob to find TODO.md, TODOS.md, TASKS.md, ROADMAP.md, BACKLOG.md (including subdirectories). Parse each as a structured task list.

2. **Scan for inline markers.** Grep pattern: `TODO|FIXME|HACK|XXX` (case-insensitive). Exclude: node_modules, .git, vendor, dist, build, __pycache__, .next, coverage, .claude. Also exclude task files from step 1 — no double-counting. If >50 results, group by directory and report counts.

3. **Load the backlog.** `backlog_search` with key terms + `backlog_list_tasks` for full task list.

4. **Cross-reference.** For each TODO: Tracked (task references file/line or content) / Untracked (no match) / Stale candidate (task references file/line where TODO no longer exists).

5. **Present the report.** Coverage summary + Untracked TODOs table (file:line, type, comment, suggested priority) + Tracked TODOs list + Stale candidates.

6. **Offer actions.** Create tasks for all untracked / Create selectively / Review stale candidates / Just the report.

Full detailed scan logic, report format, and edge cases in `references/scan-flow.md`.
