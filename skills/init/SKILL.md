---
name: init
description: "Initialize Taskmaster for the current project. Invoke when the user wants to set up task/backlog tracking, says 'set up taskmaster', 'initialize backlog', 'I want to track my work here', or when backlog.yaml does not yet exist in the project. Creates backlog.yaml and PROGRESS.md."
---

# Initialize Taskmaster

Bootstrap the task management system in the current project.

## Steps

1. **Call `backlog_init` tool** with the project name.
   - If the user didn't specify a project name, derive one from the current directory name or ask.
   - The tool creates `backlog.yaml` and `PROGRESS.md` if they don't already exist.
   - If files already exist, it reports this without overwriting.

2. **If already initialized** — don't stop there. Call `backlog_status` to show the user what's in the backlog. They may have forgotten it exists or want to pick up where they left off.

3. **Guide the user into the workflow:**
   - "Create your first epic with `backlog_add_epic` — epics are workstreams that group related tasks (e.g., `auth-system`, `api-v2`)"
   - "Add tasks with `backlog_add_task` — each task gets an auto-generated ID under its epic"
   - "When you're ready to work, use `/start-session` to see the dashboard and pick a task"

4. **MCP registration reminder** (only if this is a fresh setup):
   - Remind the user to add the taskmaster MCP server to their project's `.mcp.json`:
   ```json
   {
     "mcpServers": {
       "taskmaster": {
         "command": "uv",
         "args": ["run", "<path-to-plugins>/taskmaster/backlog_server.py"]
       }
     }
   }
   ```
   - The server uses the current working directory as the project root, so it automatically finds `backlog.yaml` and `PROGRESS.md` in the project.

## Edge Cases

- **Corrupt or empty backlog.yaml** — If the file exists but `backlog_status` fails, warn the user that the file may be malformed and offer to back it up and reinitialize.
- **Monorepo** — The project root (where the MCP server runs from) should be the top-level monorepo directory, not a sub-repo. Tasks can reference sub-repos via the `sub_repo` field.
