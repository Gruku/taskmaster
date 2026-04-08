---
name: check-todos
description: "Scan the codebase for TODO/FIXME/HACK/XXX comments and cross-reference with the backlog. Invoke when the user says 'check TODOs', 'are my TODOs tracked', 'scan for TODOs', 'todo audit', 'what's untracked', or wants to make sure inline code comments are captured in the task system."
---

# Check TODOs

Scan the codebase for inline work markers (TODO, FIXME, HACK, XXX) and cross-reference them with the backlog to find what's tracked, what's missing, and what's stale.

This is the bridge between "I left a TODO in the code" and "it's actually in the task system where it won't be forgotten."

## Steps

### 1. Scan for task files

First, look for dedicated task/planning files. Use Glob to find:

```
TODO.md, TODOS.md, TASKS.md, ROADMAP.md, BACKLOG.md
```

Also check subdirectories — some projects keep these per-module (e.g., `src/api/TODO.md`).

If found, **read each file** and parse it as a structured task list:
- Markdown checkboxes (`- [ ] item`, `- [x] done item`) → individual work items
- Headings → potential epic/category groupings
- Bullet points without checkboxes → also work items
- Already-checked items (`[x]`) → note as "completed in TODO.md but may need a backlog task for cleanup"

These files are a rich source of planned work — treat each unchecked item as a potential task.

### 2. Scan for inline markers

Use Grep to find all inline work markers in code files:

```
Grep pattern: "TODO|FIXME|HACK|XXX" (case-insensitive)
```

Exclude noise directories: `node_modules`, `.git`, `vendor`, `dist`, `build`, `__pycache__`, `.next`, `.nuxt`, `coverage`, `.claude`.

**Also exclude the task files found in step 1** — they're already parsed structurally, don't double-count them as grep hits.

Collect results as a list of:
- **File path** and **line number**
- **Marker type** (TODO / FIXME / HACK / XXX)
- **Text** — the comment content after the marker
- **Context** — the surrounding code (1-2 lines) to understand what it relates to

If there are more than 50 inline results, group by directory first and report counts. Only show individual items for the top areas.

### 3. Load the backlog

Call `backlog_search` with key terms from each item to check if it's already tracked. Also call `backlog_list_tasks` to get the full task list for matching.

### 4. Cross-reference

For each TODO found, determine its status:

**Tracked** — A task exists that references this TODO, either by:
- File path appearing in a task's `notes` or `docs` field
- The TODO's content matching a task title closely (semantic match, not just keyword)
- A task explicitly mentioning the file:line in its notes

**Untracked** — No matching task found. This is work that exists in the code but isn't in the task system.

**Stale candidates** — Tasks that reference files/lines where the TODO no longer exists (the TODO may have been resolved but the task wasn't updated). Flag these for review, don't assert they're stale — the task may have evolved beyond the original TODO.

### 5. Present the report

Structure the output as:

```
## TODO Audit — {project name}

**Task files found:** {list of TODO.md etc., or "none"}
**Inline markers:** {M} across {N} files

### Coverage
- Tracked: {X} ({pct}%) — already have matching tasks
- Untracked: {Y} ({pct}%) — not in the backlog
- Stale candidates: {Z} — tasks that may reference resolved TODOs

### Untracked TODOs (need tasks)

**{directory}/**
| File:Line | Type | Comment | Suggested Priority |
|-----------|------|---------|-------------------|
| src/api/routes.ts:45 | FIXME | Race condition in auth check | high |
| src/api/routes.ts:120 | TODO | Add rate limiting | medium |

**{another directory}/**
...

### Tracked TODOs (already in backlog)
- `auth-003` ← src/auth/middleware.ts:22 TODO: refresh token handling
- `api-007` ← src/api/routes.ts:88 FIXME: validate input

### Stale Candidates (review these)
- `api-002` references src/api/old-handler.ts:15 — but no TODO found there anymore
```

**Priority suggestions:**
- FIXME → high (something is broken or wrong)
- HACK → high (fragile workaround that needs proper fix)
- XXX → high (needs urgent attention)
- TODO → medium (planned work, not broken)

### 6. Offer actions

After the report, offer:

> **What would you like to do?**
> 1. **Create tasks** for all untracked TODOs (I'll group them by area into epics)
> 2. **Create tasks selectively** — pick which ones to track
> 3. **Review stale candidates** — check if those tasks should be updated or closed
> 4. **Just the report** — no changes needed right now

If the user chooses to create tasks:
- Group TODOs by directory/domain into epics (create new epics if needed)
- Create tasks with the source `file:line` in the notes field so future scans can match them
- Format notes as: `Source: {file}:{line} — {original TODO comment}`
- If a phase is active, ask if new tasks should be assigned to it or left unassigned
- Assign priorities based on the marker type

## Running as a health check

This skill works well as a periodic check-in. Suggest to the user:
- Run at the start of a new phase to catch untracked work
- Run before ending a session to make sure new TODOs got captured
- Run after a big refactor to find stale task references

## Edge Cases

- **Hundreds of TODOs** — Don't create individual tasks for everything. Group by area, create epic-level tasks like "Address TODOs in src/api/ (23 items)", and list the individual TODOs in the task notes.
- **TODOs in tests** — These are often intentional test stubs. Flag them separately and default to lower priority (low).
- **TODOs in third-party/generated code** — Skip files in vendor/, node_modules/, dist/, generated/ directories.
- **No backlog exists** — Suggest running `/init-taskmaster` first.
- **TODO.md with checkboxes** — Parse `- [ ]` as open items and `- [x]` as completed. Completed items may still need backlog tasks if follow-up work remains (cleanup, tests, docs).
- **Multiple TODO.md files** — Some projects keep per-module TODO files (e.g., `src/api/TODO.md`, `src/auth/TODO.md`). Find all of them and group results by location. The directory name is a strong hint for which epic the items belong to.
- **TODO.md items that are already tasks** — When creating tasks from TODO.md items, add `Source: TODO.md line N` in the task notes. On future scans, this enables matching.
