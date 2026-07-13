# Start Session

Load project context and orient for a new work session. Default mode is a **glance briefing** (~800–1,000 tokens). Append `--deep` for today's full ceremony.

## Glance flow (default)

### Step 1 — Project dashboard

Call `backlog_status()` (slim, no `verbose`). This returns counts, in-progress titles, active phase, and stale task count. Note the `**Schema:** v<N>` line — v3 steps activate only on v3 backlogs.

### Step 2 — Thread board

Call `backlog_thread_list()`. Returns the open lines of work — one entry per thread: name (the resume token), staleness, branch, tldr, next action, tasks. This replaces per-handover listing; a thread's newest handover is fetched only when the user picks it (`backlog_thread_resume("<name>")`).

### Step 3 — 1-line counts

Compose a single counts line from the above tool outputs:

```
N new issues (N P0) · N stale tasks · N flagged handovers
```

For issues count: use the `open_issues_count` field from `backlog_status` output (slim mode includes aggregate counts). For stale count: also from `backlog_status` aggregate fields.

### Step 3b — Linear sync footer (only if `linear.yaml` exists)

If `.taskmaster/linear.yaml` is present in the project, call `backlog_linear(action="status")` once. Append a single line to the briefing **only if non-zero**:

```
Linear sync: N queued · M permanent failures (latest: <reason snippet>)
```

If the queue is empty and there are no permanent failures, omit this line entirely. Surface verbosely only when there's a real backlog or a stuck failure — otherwise this is noise. If permanent failures are present, suggest `taskmaster:linear status` for the full breakdown.

### Step 3c — Your desk (sticky notes)

Call `backlog_note(action="list")`. If notes exist, render them under a **Your desk** heading in the briefing — pinned first, one line each (author · age · first line). These are the user's situational notes-to-self: treat as orientation context for "what was on my mind", alongside (not replacing) the last handover. Never archive, edit, or act on a note without the user asking.

### Step 3d — Legacy lessons notice (only if `.taskmaster/lessons/` has `L-*.md` files)

If the project's `.taskmaster/lessons/` directory contains any `L-*.md` files, add a single quiet line to the briefing noting legacy lessons exist and that `taskmaster:migrate-lessons` converts them. Omit entirely if the directory is empty or absent.

### Step 3e — Legacy in-review sweep (only if in-review tasks lack `human_action`)

If any in-review task has no `human_action`, it predates the human-gate semantics (pre-4.6.0: in-review meant "user tests"). Once per project: present them as one table (id, title) and offer to bulk-close all to `done` via `backlog_batch_update` (op `complete`) — unless the user flags one as genuinely blocked, in which case write its blocker instead: `backlog_update_task(id, "human_action", "...")`. Apply on approval, one message. If a close is rejected by outstanding gates, report it and leave that task as-is. Self-extinguishing: once every in-review task carries a human_action, this step never fires again.

### Step 4 — Briefing

Present in order:

- **Open threads:** the board from Step 2, one line each (`name — tldr → next_action`). If the user's prompt already names a thread or pastes a handover id, skip the board and call `backlog_thread_resume` with it directly.
- **Resuming:** in-progress tasks (from `backlog_status`)
- **Waiting on you:** in-review tasks, each with its `human_action`. If the user says one is handled — or you can verify it directly (e.g. the env var now exists) — close it with `backlog_complete_task(id, target_status="done", ...)`; that clears the human_action and logs the session record.
- **Phase progress:** if active phase, show done/total
- **Stale tasks:** if any (from `backlog_status` stale list)
- **Dashboard:** epic progress summary (flag any epic as "closeable" once its tasks are all done — suggest `backlog_archive_epic`)
- **Suggested next:** first available task from the active phase
- **Counts line** (Step 3)

### Step 5 — Prompt

"What would you like to work on? Use `--deep` for the full briefing with all issues and last session."

## Deep mode (`--deep`)

When the user says `start-session --deep`, "full briefing", "give me everything", or equivalent: run the glance flow above first, then continue with `references/deep-mode.md`.

## Empty state

If no epics and no tasks: "The backlog is empty — let's set it up! What are the main workstreams?" Guide to `backlog_add_epic` then tasks. Do not show an empty dashboard table.

## Error handling

If `backlog_status` fails: check if `backlog.yaml` exists. If not, suggest `/init`. If it exists, the MCP server may not be registered — guide to `.mcp.json`.

## Mid-session behavior

For idea capture: use `<idea-candidate>` inline for fuzzy/ambient ideas; call `backlog_idea_create` for explicit or concrete ones.

## Notes

- Read-only skill — no files modified.
- `backlog_status` handles all YAML parsing and stat computation.
- Deep ceremony detail: `references/deep-mode.md`.
