# Generic AGENTS.md adapter (Cursor / Zed / opencode)

For any tool that reads AGENTS.md-style rules and speaks MCP. Two steps:

1. Replace `{{TASKMASTER_HOME}}` in `AGENTS.md` with your taskmaster
   checkout's absolute path (forward slashes).
2. Append the fragment below the `---` in `AGENTS.md` to the rules file your
   tool reads, and register the MCP server (`uv run
   <home>/backlog_server.py`) in the tool's MCP configuration:
   - **Cursor**: `.cursor/mcp.json` (or global MCP settings)
   - **Zed**: `settings.json` → `context_servers`
   - **opencode**: `opencode.json` → `mcp`

Verify by asking for backlog status — the `backlog_status` MCP tool should
answer. Per-tool verification status is tracked in
`docs/capability-matrix.md`.

The single source of workflow truth stays `{{TASKMASTER_HOME}}/playbooks/`;
this fragment only routes to it.

## Gotchas (verified 2026-07-08)

- **opencode (1.17.14):** use the absolute path to `uv` in `opencode.json`'s
  `mcp.<name>.command`. Playbooks live outside the project dir, so
  non-interactive `opencode run` auto-rejects the read — add
  `"permission": {"external_directory": "allow"}` to `opencode.json`
  (interactive sessions just prompt). AGENTS.md routing works natively:
  opencode reads the fragment and follows the playbook table unprompted.
- **Cursor:** add the server to `~/.cursor/mcp.json` (`mcpServers` map,
  same absolute-uv-path rule); verification requires the GUI.
