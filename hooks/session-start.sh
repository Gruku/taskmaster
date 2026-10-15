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

# Walk up from the working directory looking for a .taskmaster/ directory.
has_taskmaster=0
dir="$PWD"
while :; do
  if [ -d "$dir/.taskmaster" ]; then has_taskmaster=1; break; fi
  parent=$(dirname "$dir")
  [ "$parent" = "$dir" ] && break
  dir="$parent"
done

if [ "$has_taskmaster" = 1 ]; then
  CONTEXT="Taskmaster tracks this project's tasks, bugs, issues, decisions and handovers (tm MCP tools, taskmaster:* skills).
A change the user asks for directly that fits in one sitting, has no open design question and touches about three files or fewer needs no backlog entry: just do it. Anything larger or multi-session, or a direct change that grows past that, goes through the taskmaster:taskmaster skill; say so in one line when you switch.
Use taskmaster:start-session when the user asks what to work on or to be oriented, taskmaster:end-session to wrap up."
else
  CONTEXT="Taskmaster is installed but this project has no .taskmaster/ directory, so nothing is tracked here yet. Use taskmaster:init-taskmaster only when the user wants to set taskmaster up."
fi
if ! command -v uv &>/dev/null; then
  CONTEXT="${CONTEXT}
The tm MCP server cannot start because \`uv\` is not installed; tell the user."
fi

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
