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

# Walk up from the working directory looking for a tracked project. Parameter expansion only: a subprocess per
# directory level made session start seconds slower on Windows when no backlog exists.
# tracked = 1 for a .taskmaster/ layout (or TASKMASTER_ROOT), 2 for a legacy backlog.yaml layout.
tracked=0
if [ -n "${TASKMASTER_ROOT:-}" ]; then
  tracked=1
else
  dir="$PWD"
  while :; do
    if [ -d "$dir/.taskmaster" ]; then tracked=1; break; fi
    if [ -f "$dir/.claude/backlog.yaml" ] || [ -f "$dir/backlog.yaml" ]; then tracked=2; break; fi
    parent="${dir%/*}"
    [ -z "$parent" ] && parent=/
    [ "$parent" = "$dir" ] && break
    dir="$parent"
  done
fi

if [ "$tracked" != 0 ]; then
  CONTEXT="Taskmaster tracks this project's tasks, bugs, issues, decisions and handovers (tm MCP tools, taskmaster:* skills).
A change the user asks for directly that fits in one sitting, has no open design question and touches about three files or fewer needs no backlog entry: just do it. Anything larger or multi-session, or a direct change that grows past that, goes through the taskmaster:taskmaster skill; say so in one line when you switch.
Use taskmaster:start-session when the user asks what to work on or to be oriented, taskmaster:end-session to wrap up."
  if [ "$tracked" = 2 ]; then
    CONTEXT="${CONTEXT}
This backlog uses a legacy layout (backlog.yaml outside .taskmaster/); taskmaster:migrate-v3 moves it into .taskmaster/. Do not run init-taskmaster here."
  fi
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
