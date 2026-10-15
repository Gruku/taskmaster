# Check-TODOs — Full Scan Flow

Detailed scan and cross-reference logic behind the playbook. Init's analysis mode reuses this scan.

## Step 1: Scan for Task Files

Find files named: TODO.md, TODOS.md, TASKS.md, ROADMAP.md, BACKLOG.md (also in subdirectories).

If found, read each file and parse:
- Markdown checkboxes `- [ ] item` / `- [x] done item` -> individual work items
- Headings -> potential epic/category groupings
- Bullet points without checkboxes -> also work items
- Already-checked items (`[x]`) -> note as completed (may need backlog task for cleanup)

Treat each unchecked item as a potential task.

## Step 2: Scan for Inline Markers

Search the codebase for the pattern: `TODO|FIXME|HACK|XXX` (case-insensitive).

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

Priority suggestions (the one mapping, reused by init's analysis mode): FIXME/HACK/XXX -> high; TODO -> medium.

## Step 6: Offer Actions

Ask one question that covers the whole choice: create tasks for all untracked TODOs (state the phase they would go into, the active phase if there is one), create selectively, review stale candidates, or stop at the report.

When creating tasks:
- Group TODOs by directory/domain into epics.
- `backlog_add_task` with `notes` = "Source: {file}:{line} — {original TODO comment}" and priorities from the mapping above.

## Edge Cases

- TODOs in tests: often intentional test stubs; flag separately and default to lower priority.
- TODOs in third-party/generated code: skip vendor/, node_modules/, dist/, generated/.
- TODO.md with checkboxes: parse `- [ ]` as open and `- [x]` as completed.
- Multiple TODO.md files: find all, group results by location. Directory name hints at which epic.
- TODO.md items that are already tasks: add "Source: TODO.md line N" in task notes for future scans.
- Hundreds of TODOs: don't create individual tasks for everything. Group by area, create epic-level tasks like "Address TODOs in src/api/ (23 items)".
