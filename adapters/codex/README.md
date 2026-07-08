# Codex CLI adapter

Gives OpenAI Codex CLI the taskmaster MCP server plus the same workflow
playbooks Claude Code uses (tier 1 + tier 2; enforcement hooks are
Claude-Code-class only and intentionally absent — the playbooks state the
discipline, Codex follows it advisorily).

## Install (3 steps)

1. **Replace the placeholder.** In copies of these files, replace every
   `{{TASKMASTER_HOME}}` with your taskmaster checkout's absolute path,
   forward slashes (e.g. `C:/Users/you/taskmaster` or `~/taskmaster`).
2. **Register the MCP server.** Merge `config.toml`'s
   `[mcp_servers.taskmaster]` block into `~/.codex/config.toml`.
   Slash prompts: copy `prompts/*.md` into `~/.codex/prompts/` — each file
   becomes a `/name` prompt (e.g. `/start-session`). Routing rules: append
   `AGENTS.md`'s fragment to your project's `AGENTS.md` (or
   `~/.codex/AGENTS.md` for all projects).
3. **Verify.** In a project with a `.taskmaster/` backlog (create one with
   the `init-taskmaster` playbook), start Codex and ask for the backlog
   status — the `backlog_status` tool should answer. Then `/start-session`.

## What each piece does

| File | Purpose |
|---|---|
| `config.toml` | MCP server registration (`uv run <home>/backlog_server.py`) |
| `prompts/<name>.md` | One slash prompt per playbook — thin pointer, no duplicated content |
| `AGENTS.md` | Intent → playbook routing table + advisory gate discipline |

The single source of workflow truth is `{{TASKMASTER_HOME}}/playbooks/` —
updating the checkout updates every already-installed prompt's behavior,
because prompts only point.
