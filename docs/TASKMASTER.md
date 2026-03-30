# Taskmaster

**AI-powered task and backlog management for Claude Code projects.**

Taskmaster is a plugin that turns Claude Code into a disciplined project manager. It provides kanban-style task tracking, session logging, phase-based sequential planning, git worktree isolation, and a live browser-based board — all driven from a single `backlog.yaml` file.

**Version:** 1.1.0
**Author:** gruku
**Requires:** `uv` (Python package runner)

---

## Philosophy

All work in a Taskmaster-enabled project flows through the task system. If work happens outside the system, session history is lost, worktree isolation is skipped, and the backlog drifts out of sync with reality.

The only exceptions are pure git operations (commit/push) and dedicated PR security reviews — everything else routes through Taskmaster.

---

## Table of Contents

- [Getting Started](#getting-started)
- [Core Concepts](#core-concepts)
- [Task Lifecycle](#task-lifecycle)
- [Skills (Workflows)](#skills-workflows)
- [MCP Tools (API)](#mcp-tools-api)
- [Data Model](#data-model)
- [Worktree Integration](#worktree-integration)
- [Viewer](#viewer)
- [PROGRESS.md](#progressmd)
- [Hook Integration](#hook-integration)
- [Per-Project Files](#per-project-files)

---

## Getting Started

Initialize Taskmaster in any project:

```
> set up taskmaster
```

This invokes the `taskmaster:init-taskmaster` skill, which:

1. Asks where to store the backlog — **hidden** (`.claude/backlog.yaml`) or **tracked** (`.taskmaster/backlog.yaml`)
2. Asks how to initialize — **analyze** the project (scan TODOs, README, git log) or **clean start**
3. Creates the backlog, config, and `PROGRESS.md`
4. Opens the kanban board in your browser

From there, a typical workflow looks like:

```
> what should I work on?       → start-session: shows dashboard + suggestions
> pick task auth-003            → pick-task: locks task, creates worktree
> [... do the work ...]
> is this ready?                → review-gate: runs code review, tests, spec check
> I'm done                      → end-session: logs summary, transitions status
```

---

## Core Concepts

### Epics

Thematic workstreams that group related tasks. Each epic has a kebab-case ID (e.g., `auth`, `api`, `frontend`) that serves as the prefix for all its task IDs.

- **Status:** `active` | `planned` | `done`
- Only tasks in `active` epics appear in the "next available" queue

### Tasks

The atomic unit of work. Each task belongs to one epic and optionally references one phase.

- **ID format:** `{epic-id}-{NNN}` (auto-generated, e.g., `auth-003`)
- **Priority:** `P0` (critical) | `P1` (high) | `P2` (medium) | `P3` (low)
- **Estimate:** `S` | `M` | `L`
- Tasks track timestamps (created, started, completed, archived), git branch, worktree path, dependencies, docs references, and review instructions

### Phases

Temporal blocks of work that cut across epics — like sprints or release phases.

- **Only one phase can be active at a time** (enforced server-side)
- `backlog_next_available` filters to the active phase, keeping focus narrow
- `backlog_advance_phase` completes the active phase, archives its done tasks, and activates the next one by order
- Have optional `target_date` for deadline tracking

### Task Budget

Each epic has a soft cap of 8 active (non-archived, non-done) tasks. When `backlog_add_task` pushes an epic past this cap, a warning is returned. The cap is configurable per epic via the `max_tasks` field. Tasks should represent work you'd pick up in different sessions — use plan documents for detailed step breakdowns.

### Staleness

Tasks track when they were last referenced (`last_referenced` field). Todo tasks not referenced in 14+ days are flagged as stale during `start-session` and in `backlog_status` output. Archive stale tasks or interact with them to refresh the timestamp.

### Sessions

A session is one Claude conversation process. The MCP server generates a unique `SESSION_ID` (`hostname-pid-uuid8`) on startup.

- `backlog_pick_task` locks the task to the current session via `locked_by`
- `backlog_complete_task` clears the lock
- Session summaries are persisted as changelog entries in `PROGRESS.md`

---

## Task Lifecycle

```
todo → in-progress → in-review → done → archived
         (pick-task)  (review-gate)      (archive)
```

| Status | Meaning |
|--------|---------|
| `todo` | Defined, waiting. May have unmet dependencies. |
| `in-progress` | Actively being worked on. Locked to a session. Has a worktree. |
| `in-review` | Implementation complete, automated checks passed. User must manually test. |
| `done` | User confirmed it works. Session summary logged. |
| `blocked` | Cannot proceed due to an external blocker. |
| `archived` | Hidden from board. Reason recorded (`done`, `deprecated`, `duplicate`, `wont-fix`, `superseded`). |

**`in-review` is mandatory.** Tasks should not jump from `in-progress` to `done`. The stage exists because automated tests can't catch everything — UI behavior, integration feel, edge cases that need human judgment.

---

## Skills (Workflows)

Skills are high-level workflows invoked via natural language. Taskmaster provides a universal router (`taskmaster:taskmaster`) that detects intent and dispatches to the correct sub-skill.

### `taskmaster:start-session`

**Trigger:** "let's get started", "what should I work on", "orient me", new conversation

Shows a structured briefing:
- In-progress and in-review items
- Last session summary (from `PROGRESS.md`)
- Phase progress
- Full dashboard with stats
- Suggested next task

### `taskmaster:pick-task`

**Trigger:** "pick task X", "let's work on X", "what should I tackle"

1. If no task specified, calls `backlog_next_available` (phase-filtered)
2. Warns if 3+ tasks already in-progress
3. Checks dependency graph for unmet blockers
4. Locks the task to the current session
5. Reads any linked docs (plan, spec, etc.)
6. Creates a git worktree: `.worktrees/{task-id}` on branch `feature/{task-id}`
7. Records branch and worktree path on the task

Handles lock conflicts from crashed sessions with `force=true` to reclaim.

### `taskmaster:review-gate`

**Trigger:** "is this ready?", "check my work", "run the review gate"

Three gates:
1. **Spec/Plan check** (P0/P1 only) — verifies linked docs exist on disk
2. **Code review** — diffs branch vs. base, runs code reviewer sub-agent
3. **Tests + Build** — auto-detects test runner (pytest, npm test, cargo test, go test, etc.)

Presents a verdict with gate breakdown. Critical findings block unconditionally. On pass, transitions task to `in-review`.

### `taskmaster:end-session`

**Trigger:** "end session", "I'm done", "wrap up", "log this"

1. Auto-generates session summary: Done / Decisions / Issues / Tasks touched
2. Generates a session title (`{Topic}: {Brief Description}`)
3. Asks: `in-review` (needs manual testing) or `done` (already confirmed)?
4. Atomically transitions status + appends changelog to `PROGRESS.md`
5. Offers worktree cleanup if task is done
6. Commits tracking files

### `taskmaster:init-taskmaster`

**Trigger:** "set up taskmaster", "initialize backlog"

Bootstraps Taskmaster in a project. Two modes:
- **Analyze:** Scans for TODOs/FIXMEs, reads README, checks git log, proposes a backlog
- **Clean start:** Creates empty structure, guides epic/task creation

### `taskmaster:check-todos`

**Trigger:** "check TODOs", "todo audit", "what's untracked"

Scans the codebase for `TODO|FIXME|HACK|XXX` comments, cross-references with the backlog, and produces an audit report:
- **Tracked:** TODO already linked to a task
- **Untracked:** TODO with no corresponding task
- **Stale:** Task exists but the TODO is gone

Offers to create tasks for untracked items. Priority mapping: `FIXME/HACK/XXX` → P1, `TODO` → P2.

---

## MCP Tools (API)

All tools are prefixed with `backlog_`. These are the low-level building blocks that skills orchestrate.

### Read-Only

| Tool | Description |
|------|-------------|
| `backlog_status` | Full dashboard: epic table, in-progress, blocked, next-up, stats, phase |
| `backlog_list_tasks` | Filtered list. Params: `epic`, `status`, `priority`, `phase` |
| `backlog_get_task(task_id)` | Full task detail with epic context, deps, docs, related tasks |
| `backlog_search(query)` | Full-text search across ID, title, notes, branch, epic name, doc paths |
| `backlog_dependencies(task_id)` | Upstream (depends-on) and downstream (unblocks) graph |
| `backlog_next_available` | Ready-to-work tasks: todo in active epics, deps satisfied, phase-filtered |
| `backlog_validate` | Integrity check: dangling deps, circular deps, missing dates, status inconsistencies |
| `backlog_last_session` | Most recent changelog entry from `PROGRESS.md` |
| `backlog_worktrees` | All active git worktrees across repo and sub-repos |
| `backlog_phase_status(id?)` | Phase progress with bar, task breakdown; defaults to active phase |
| `backlog_snapshot(ops)` | Dry-run preview of batch operations without writing to disk |

### Mutating

| Tool | Description |
|------|-------------|
| `backlog_init(project, location)` | Initialize Taskmaster. Location: `hidden` or `tracked` |
| `backlog_add_task(title, epic, ...)` | Create task. ID auto-generated as `{epic}-{NNN}`. New params: `anchors` (comma-separated globs/URLs), tasks get `last_referenced` auto-set |
| `backlog_update_task(task_id, field, value)` | Update one field. Validates enums, parses docs/depends_on/stage |
| `backlog_pick_task(task_id, force?)` | Lock to session, set in-progress, set timestamps |
| `backlog_complete_task(task_id, ...)` | Atomic status transition + changelog append to `PROGRESS.md`. New param: `auto_summary` (bool) — lightweight session log for small sessions |
| `backlog_archive_task(task_id, reason)` | Archive with reason: `done`, `deprecated`, `duplicate`, `wont-fix`, `superseded` |
| `backlog_add_epic(id, name, ...)` | Create epic. ID must be lowercase kebab-case |
| `backlog_update_epic(id, field, value)` | Update epic name, status, or description |
| `backlog_add_phase(id, name, ...)` | Create phase. Auto-activates if first |
| `backlog_update_phase(id, field, value)` | Update phase. Activating one deactivates the current active |
| `backlog_advance_phase` | Complete active phase, archive done tasks, activate next by order |
| `backlog_batch_update(operations)` | Atomic multi-op: update, status, complete, archive, pick, update_epic |
| `backlog_open_viewer` | Open kanban board in browser |

---

## Data Model

The `backlog.yaml` file is the single source of truth. **Never edit it directly** — always use MCP tools, which own the schema and handle validation.

### Structure

```yaml
meta:
  project: "My Project"
  updated: "2026-03-20"

context:                          # Auto-regenerated on every save
  active_epic: "auth"
  in_progress: [{id, title, epic, branch, locked_by}]
  blocked: [{id, title, epic, blockers}]
  recent_completed: [{id, title, completed}]
  next_up: [{id, title, priority, epic}]
  stats: {total, done, in_progress, in_review, todo, blocked, archived}
  active_phase: {id, name, stats, target_date, start_date}

epics:
  - id: "auth"
    name: "Authentication System"
    status: "active"
    description: "..."
    tasks:
      - id: "auth-003"
        title: "Implement refresh token rotation"
        status: "in-progress"
        priority: "P1"
        phase: "m1"
        depends_on: ["auth-001", "auth-002"]
        branch: "feature/auth-003"
        worktree: ".worktrees/auth-003"
        docs:
          plan: "docs/plans/auth-refresh.md"
        # ... timestamps, notes, etc.

phases:
  - id: "m1"
    name: "Foundation"
    status: "active"
    order: 1
    target_date: "2026-04-01"
```

### Task Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | Auto-generated `{epic}-{NNN}` |
| `title` | string | Required |
| `status` | enum | `todo`, `in-progress`, `in-review`, `done`, `blocked`, `archived` |
| `priority` | enum | `P0`, `P1`, `P2`, `P3` |
| `estimate` | enum | `S`, `M`, `L` |
| `notes` | string | Freeform; warns if >300 chars |
| `blockers` | string | Description of what's blocking |
| `depends_on` | list | Task IDs this depends on |
| `docs` | map | Keys: `plan`, `spec`, `roadmap`, `design`, `analysis` |
| `branch` | string | Git branch name |
| `worktree` | string | Worktree path |
| `phase` | string | Phase ID |
| `sub_repo` | string | Monorepo sub-directory |
| `stage` | int | Optional phase number |
| `locked_by` | string | Session ID holding the lock |
| `review_instructions` | string | How to manually test |
| `created` / `started` / `completed` / `archived` | datetime | Auto-managed timestamps |
| `archive_reason` | enum | `done`, `deprecated`, `duplicate`, `wont-fix`, `superseded` |
| `anchors` | list[string] | Glob patterns or URLs declaring target files/systems |
| `last_referenced` | string | ISO timestamp, auto-updated when any tool touches this task |

### Context Block

The `context` section at the top of `backlog.yaml` is auto-regenerated on every save. It provides a quick snapshot for tools and skills to read without parsing the full task tree. Do not edit it manually.

---

## Worktree Integration

Worktrees are the core isolation mechanism. Each in-progress task gets its own working directory and branch, so multiple tasks can be worked on across sessions without conflicts.

**Lifecycle:**

1. **Pick task** → `git worktree add .worktrees/{task-id} -b feature/{task-id}`
2. **Work** → all edits happen in the worktree, not on the main branch
3. **Review gate** → diffs the task branch against its base
4. **End session** → if done, offers `git worktree remove .worktrees/{task-id}`

**Monorepo support:** If a task has `sub_repo` set, the worktree is created relative to that sub-directory.

**Lock conflicts:** If a previous session crashed without releasing a lock, use `backlog_pick_task(task_id, force=true)` to reclaim it.

**Listing:** `backlog_worktrees` runs `git worktree list --porcelain` across the project root and any sub-repos.

---

## Viewer

Taskmaster includes a kanban board that runs in your browser.

- Opens via `backlog_open_viewer` or automatically on init
- Served by an embedded HTTP server that starts with the MCP process
- **Port:** deterministic per-project, range 6800–6899 (MD5 hash of project root, mod 100, plus 6800)
- Multi-session safe: reuses port if same project, falls back to random port if collision

### HTTP Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Kanban board (backlog-viewer.html) |
| `GET /backlog.yaml` | Raw YAML |
| `GET /api/backlog` | Backlog as JSON |
| `GET /api/session` | Current session state + SESSION_ID |
| `GET /api/worktrees` | Live worktree list as JSON |
| `GET /api/identity` | `{"root": "/path/to/project"}` for port ownership |
| `GET /file/{path}` | Serve repo files; `.md` rendered as styled HTML |

---

## PROGRESS.md

An auto-generated file with two sections:

1. **Dashboard** — a table of workstreams, phase progress, in-progress/blocked/next-up summaries
2. **Changelog** — append-only session log (newest first)

### Changelog Entry Format

```markdown
### 2026-03-20 — Auth: Refresh Token Setup
**Done:**
- Implemented JWT refresh endpoint
- Added token rotation logic

**Decisions:**
- Store refresh tokens in Redis with 7-day TTL

**Issues:**
- None

**Tasks touched:** auth-003
```

The dashboard is regenerated on every `backlog_complete_task` call. The changelog is append-only — entries are never modified or removed.

---

## Hook Integration

Taskmaster installs a `SessionStart` hook that runs on every new Claude conversation:

1. **Checks** that `uv` is installed (warns if not)
2. **Injects context** into the session — a routing table mapping user intents to skills, plus a list of all available `backlog_*` tools

This ensures Claude always knows how to route task-related requests, even without the user explicitly invoking a skill.

---

## Quick Reference

| Want to... | Say... |
|------------|--------|
| Set up Taskmaster | "set up taskmaster" / "initialize backlog" |
| See what's going on | "what should I work on?" / "orient me" |
| Start working on a task | "pick task auth-003" / "what should I tackle?" |
| Check if work is ready | "is this ready?" / "run the review gate" |
| Finish a session | "I'm done" / "end session" / "wrap up" |
| Audit TODOs | "check TODOs" / "todo audit" |
| Open the board | "open the viewer" / "show the board" |
| Add a task | "add a task to the auth epic" |
| Search tasks | "search for refresh token" |
| Check phase progress | "how's the phase going?" |
| Advance to next phase | "advance the phase" |
