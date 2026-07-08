# Start Session

Load project context and orient for a new work session. Default mode is a **glance briefing** (~800–1,000 tokens). Append `--deep` for today's full ceremony.

## Glance flow (default)

### Step 1 — Project dashboard

Call `backlog_status()` (slim, no `verbose`). This returns counts, in-progress titles, active phase, and stale task count. Note the `**Schema:** v<N>` line — v3 steps activate only on v3 backlogs.

### Step 2 — Open handovers

Call `backlog_handover_list(status="open", limit=5)`. Returns slim entries (id + task_ids + tldr + next_action). Each entry is ~50 tokens. Flagged handovers (task done but handover still open) appear with an inline reason.

If there are more than 5 open handovers, show `(+N more — use --deep to see all)`.

### Step 3 — 1-line counts

Compose a single counts line from the above tool outputs:

```
N new issues (N P0) · N stale tasks · N flagged handovers
```

For issues count: use the `open_issues_count` field from `backlog_status` output (slim mode includes aggregate counts). For stale count: also from `backlog_status` aggregate fields.

### Step 3b — Linear sync footer (only if `linear.yaml` exists)

If `.taskmaster/linear.yaml` is present in the project, call `backlog_linear_status()` once. Append a single line to the briefing **only if non-zero**:

```
Linear sync: N queued · M permanent failures (latest: <reason snippet>)
```

If the queue is empty and there are no permanent failures, omit this line entirely. Surface verbosely only when there's a real backlog or a stuck failure — otherwise this is noise. If permanent failures are present, suggest `taskmaster:linear status` for the full breakdown.

### Step 3c — Your desk (sticky notes)

Call `backlog_note_list()`. If notes exist, render them under a **Your desk** heading in the briefing — pinned first, one line each (author · age · first line). These are the user's situational notes-to-self: treat as orientation context for "what was on my mind", alongside (not replacing) the last handover. Never archive, edit, or act on a note without the user asking.

### Step 3d — Legacy lessons notice (only if `.taskmaster/lessons/` has `L-*.md` files)

If the project's `.taskmaster/lessons/` directory contains any `L-*.md` files, add a single quiet line to the briefing noting legacy lessons exist and that `taskmaster:migrate-lessons` converts them. Omit entirely if the directory is empty or absent.

### Step 4 — Briefing

Present in order:

- **Where you left off:** latest open handover tldr + next_action (most actionable anchor)
- **Resuming:** in-progress tasks (from `backlog_status`)
- **Needs testing:** in-review tasks
- **Phase progress:** if active phase, show done/total
- **Stale tasks:** if any (from `backlog_status` stale list)
- **Dashboard:** epic progress summary
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
