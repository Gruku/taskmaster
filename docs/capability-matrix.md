# Capability Matrix

What each assistant gets from taskmaster, per portability tier (see
`docs/specs/2026-07-06-taskmaster-multi-assistant-design.md`).

| Capability | Tier | Claude Code | ZCode | Codex CLI | Cursor | Zed | opencode |
|---|---|---|---|---|---|---|---|
| MCP server (`backlog_*` tools) | 1 | ✅ `.mcp.json` | ✅ `.mcp.json` | ✅ verified 2026-07-08 (`adapters/codex/config.toml`, codex 0.130.0) | pending (Phase 4) | pending (Phase 4) | pending (Phase 4) |
| `.taskmaster/` on-disk format | 1 | ✅ | ✅ | ✅ (file-based, assistant-independent) | ✅ | ✅ | ✅ |
| Viewer (kanban web UI) | 1 | ✅ | ✅ | ✅ (assistant-independent) | ✅ | ✅ | ✅ |
| Project manifest (`project.py`) | 1 | ✅ | ✅ | ✅ (via MCP tools) | pending (Phase 4) | pending (Phase 4) | pending (Phase 4) |
| Workflow playbooks (`playbooks/`) | 2 | ✅ via `skills/` wrappers | ✅ via `skills/` wrappers | ✅ `adapters/codex/` — full session loop verified 2026-07-08 (start-session → pick-task → end-session) | adapter shipped (`adapters/agents-md/`); live verify pending (Phase 4) | pending (Phase 4) | pending (Phase 4) |
| Hooks (gate enforcement, snapshot, merge recorder) | 3 | ✅ `hooks/hooks.json` | ✅ | ❌ by design — playbooks state the discipline advisorily | ❌ | ❌ | ❌ |
| Statusline integration | 3 | ✅ (separate plugin) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Subagent dispatch conventions | 3 | ✅ | ✅ | ❌ (inline fallback per playbook phrasing) | ❌ | ❌ | ❌ |

Legend: ✅ verified working · pending — planned, not yet verified · ❌ not
available on that assistant (tier 3 is CC-class only, by design).
