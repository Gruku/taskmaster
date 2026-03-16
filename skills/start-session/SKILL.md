---
name: start-session
description: "Start a work session and orient for a new conversation. Invoke when the user says 'let's get started', 'what should I work on', 'show me the backlog', 'orient me', or begins a new conversation in a project that has backlog.yaml. Shows dashboard, last session summary, and suggests next tasks."
---

# Start Session

Load project context and orient for a new work session.

The user is arriving at the start of a conversation — they've lost context since last time and want to feel grounded quickly. Your job is to deliver a concise briefing, not a data dump.

## Steps

1. **Call `backlog_status` tool** to get the current dashboard.

2. **Call `backlog_last_session` tool** to get the most recent changelog entry for continuity.

3. **Present a structured briefing to the user:**

   Lead with the most actionable information first:

   - **If there are in-progress items:** "**Resuming:** You left these in progress:" — these are the most important because work is already started.
   - **If there are in-review items:** "**Needs your testing:** These are implemented but waiting for you to confirm they work:" — in-review tasks are equally important as in-progress. They represent finished work the user hasn't verified yet. Don't let them be forgotten between sessions.
   - **Last session summary** — what was accomplished last time, for continuity.
   - **Milestone progress** — if an active milestone exists, show it prominently: "**Milestone: {name}** — {done}/{total} tasks done". This gives the user a sense of where they are in the project's arc.
   - **Dashboard** — epic progress, stats.
   - **If there are next-up items:** "**Suggested next:** {first item} ({priority})" — these are filtered to the active milestone when one exists, so the user only sees what's relevant right now.

4. **Prompt:** "What would you like to work on? Pick a task with `/pick-task` or tell me to add new work."

## Empty State

If there are no epics and no tasks (fresh project):
- Say: "The backlog is empty — let's set it up! What are the main workstreams for this project?"
- Guide them to create their first epic with `backlog_add_epic`, then add tasks.
- Don't show an empty dashboard table — it's confusing.

## Error Handling

If the `backlog_status` tool fails (MCP server not running, backlog.yaml missing):
- Check if `backlog.yaml` exists. If not, suggest running `/init` first.
- If it exists but the tool fails, the MCP server may not be registered. Guide the user to check their `.mcp.json`.

## Notes

- This skill is read-only — it does not modify any files.
- The `backlog_status` tool handles all YAML parsing and stat computation.
- The `backlog_last_session` tool extracts the last changelog entry from PROGRESS.md.
