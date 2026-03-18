#!/usr/bin/env bash
# SessionStart hook for taskmaster plugin

set -euo pipefail

# Check for required dependency: uv
if ! command -v uv &>/dev/null; then
  cat >&2 <<'WARN'
⚠️ taskmaster plugin: 'uv' is not installed or not in PATH.
The MCP server (backlog tools) will not work without it.
Install: https://docs.astral.sh/uv/getting-started/installation/
  - macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh
  - Windows:     winget install astral-sh.uv
WARN
fi

# Determine plugin root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Escape string for JSON embedding
escape_for_json() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

CONTEXT="You have the Taskmaster plugin available for AI-powered task and backlog management.

For ANY task-related request, invoke taskmaster:taskmaster first — it routes to the correct sub-skill:

| User Intent | Routed To |
|-------------|-----------|
| New conversation, 'what should I work on', 'orient me' | taskmaster:start-session |
| 'Pick task X', 'start task', 'what should I tackle' | taskmaster:pick-task |
| 'Is this ready?', 'check my work', 'review gate' | taskmaster:review-gate |
| 'End session', 'I'm done', 'wrap up', 'log this' | taskmaster:end-session |
| 'Set up taskmaster', 'initialize backlog' | taskmaster:init-taskmaster |

Milestone tools: backlog_add_milestone, backlog_milestone_status, backlog_advance_milestone.
All other backlog_* MCP tools are available for direct task queries and mutations when you already know what to call."

context_escaped=$(escape_for_json "$CONTEXT")

# Output context injection as JSON
cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "${context_escaped}"
  }
}
EOF

exit 0
