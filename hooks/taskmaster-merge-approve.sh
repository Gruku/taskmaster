#!/bin/bash
# taskmaster-merge-approve.sh — PostToolUse hook for AskUserQuestion.
# Taskmaster's OWN approval writer for merge-gate.sh.
#
# Touches a time-limited approval file when the user picks "Approve".
# Does NOT depend on guard-hooks being installed.
#
# Namespace: $HOME/.claude/taskmaster-merge-approve-$SESSION_ID
# Distinct from guard-hooks' guard-approve-* files — guard-hooks'
# consume-approval.sh only burns guard-approve-* so it never interferes here.
#
# Expiry: the file is NEVER consumed (no consumer hook exists).  The 60s
# stat-based freshness window in merge-gate.sh IS the expiry.  Within 60s
# the user can retry a failed `git merge` without re-approving.  After 60s
# the file goes stale and the next blocked merge re-prompts.

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && SESSION_ID="default"

# Emit each answer value on its own line (mirrors ask-question-approval.sh).
ANSWERS=$(echo "$INPUT" | jq -r '
  (.tool_response.answers // .tool_output.answers // {}) as $a
  | if ($a | type) == "object" then ($a | to_entries | map(.value) | .[])
    elif ($a | type) == "string" then $a
    else empty end
')

[ -z "$ANSWERS" ] && exit 0

# Any answer exactly equal to "Approve" (case-insensitive, whitespace-trimmed)
# triggers the taskmaster approval file.
if echo "$ANSWERS" | grep -qiE '^[[:space:]]*approve[[:space:]]*$'; then
  APPROVE_FILE="$HOME/.claude/taskmaster-merge-approve-$SESSION_ID"
  mkdir -p "$(dirname "$APPROVE_FILE")"
  touch "$APPROVE_FILE"
fi

exit 0
