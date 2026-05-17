# Check-TODOs — Full Scan Flow

This file contains the detailed scan and cross-reference logic. The SKILL.md body carries only
the purpose, markers to scan for, and the output format summary.

## Step 1: Scan for Task Files

Use Glob to find: TODO.md, TODOS.md, TASKS.md, ROADMAP.md, BACKLOG.md (also in subdirectories).

If found, read each file and parse:
- Markdown checkboxes `- [ ] item` / `- [x] done item` -> individual work items
- Headings -> potential epic/category groupings
- Bullet points without checkboxes -> also work items
- Already-checked items (`[x]`) -> note as completed (may need backlog task for cleanup)

Treat each unchecked item as a potential task.

## Step 2: Scan for Inline Markers

Use Grep pattern: `TODO|FIXME|HACK|XXX` (case-insensitive).

Exclude: `node_modules`, `.git`, `vendor`, `dist`, `build`, `__pycache__`, `.next`, `.nuxt`, `coverage`, `.claude`.

Also exclude the task files found in step 1 — do not double-count them.

Collect: file path, line number, marker type (TODO/FIXME/HACK/XXX), text, context (1-2 surrounding lines).

If >50 inline results, group by directory first and report counts. Show individual items for top areas only.

## Step 3: Load the Backlog

Call `backlog_search` with key terms. Also `backlog_list_tasks` for full task list.

## Step 4: Cross-Reference

For each TODO found, determine status:

**Tracked** — A task exists that references this TODO by:
- File path appearing in task's `notes` or `docs` field
- The TODO's content matching a task title closely (semantic match, not just keyword)
- A task explicitly mentioning the file:line in its notes

**Untracked** — No matching task found.

**Stale candidates** — Tasks that reference files/lines where the TODO no longer exists. Flag for review; do not assert stale — the task may have evolved beyond the original TODO.

## Step 5: Present the Report

Structure the output:

```
## TODO Audit — {project name}

**Task files found:** {list or "none"}
**Inline markers:** {M} across {N} files

### Coverage
- Tracked: {X} ({pct}%) — already have matching tasks
- Untracked: {Y} ({pct}%) — not in the backlog
- Stale candidates: {Z} — tasks that may reference resolved TODOs

### Untracked TODOs (need tasks)

**{directory}/**
| File:Line | Type | Comment | Suggested Priority |
|---|---|---|---|
| src/api/routes.ts:45 | FIXME | Race condition in auth check | high |

### Tracked TODOs (already in backlog)
- `auth-003` <- src/auth/middleware.ts:22 TODO: refresh token handling

### Stale Candidates (review these)
- `api-002` references src/api/old-handler.ts:15 — but no TODO found there anymore
```

Priority suggestions: FIXME/HACK/XXX -> high; TODO -> medium.

## Step 6: Offer Actions

After the report, offer:
1. Create tasks for all untracked TODOs (group by area into epics)
2. Create tasks selectively
3. Review stale candidates
4. Just the report — no changes

If creating tasks:
- Group TODOs by directory/domain into epics
- `backlog_add_task` with source `file:line` in notes: "Source: {file}:{line} — {original TODO comment}"
- Ask if new tasks should be assigned to the active phase
- Assign priorities: FIXME/HACK/XXX -> high; TODO -> medium

## Edge Cases

- TODOs in tests: often intentional test stubs; flag separately and default to lower priority.
- TODOs in third-party/generated code: skip vendor/, node_modules/, dist/, generated/.
- TODO.md with checkboxes: parse `- [ ]` as open and `- [x]` as completed.
- Multiple TODO.md files: find all, group results by location. Directory name hints at which epic.
- TODO.md items that are already tasks: add "Source: TODO.md line N" in task notes for future scans.
- Hundreds of TODOs: don't create individual tasks for everything. Group by area, create epic-level tasks like "Address TODOs in src/api/ (23 items)".

## Running as a Health Check

- Run at the start of a new phase to catch untracked work
- Run before ending a session to make sure new TODOs got captured
- Run after a big refactor to find stale task references
