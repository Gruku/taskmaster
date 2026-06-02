#!/bin/bash
# merge-recorder.sh — PostToolUse (Bash). NEVER blocks (exit 0 always).
#
# Stamps the reached merge rung on a task when a successful `git merge` is
# detected.  Looks up the task whose branch == <SRC>, reads the post-merge
# HEAD to determine the target branch, maps it to a ladder rung label (or
# "branch:<name>" for untracked targets), then calls merge_recorder_stamp.py
# to write merge_status into .taskmaster/backlog.yaml.
#
# CARDINAL RULE: PostToolUse is advisory — exit code is IGNORED by the harness,
# but we exit 0 explicitly on every path for defensive correctness.  On any
# error or uncertainty, we silently do nothing and exit 0.
#
# Source-branch parser:
#   Duplicated from merge-gate.sh (~10 lines) with a comment cross-link.
#   Both hooks share the same strategy: strip everything up to and including
#   "merge", walk tokens, last non-flag token = branch name.
#   Cross-link: plugins/taskmaster/hooks/merge-gate.sh lines 86-107.

INPUT=$(cat)

# -- Short-circuit: command must contain "merge" ---------------------------------
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$COMMAND" ] && exit 0
echo "$COMMAND" | grep -q 'merge' || exit 0

# -- Must be a `git` invocation reaching `merge` as a subcommand ----------------
#    (same regex as merge-gate.sh line 35)
if ! echo "$COMMAND" | grep -qE 'git([[:space:]]+(-[A-Za-z]\S*[[:space:]]+\S+|--[a-z][a-z-]*))*[[:space:]]+merge\b'; then
  exit 0
fi

# -- Only act on SUCCESSFUL merges ----------------------------------------------
EXIT_CODE=$(echo "$INPUT" | jq -r '.tool_response.exit_code // .exit_code // 1' 2>/dev/null)
[ "$EXIT_CODE" = "0" ] || exit 0

# -- Parse source branch (cross-link: merge-gate.sh lines 86-107) --------------
#    Strategy: drop everything up to and including "merge", walk tokens,
#    last non-flag token = branch name. Reject anonymous refs.
AFTER_MERGE=$(echo "$COMMAND" | sed -E 's/.*\bmerge[[:space:]]*//')

SRC=""
for tok in $AFTER_MERGE; do
  case "$tok" in
    -*) continue ;;  # flag — skip
  esac
  SRC="$tok"
  # Keep updating: last non-flag token is the branch name
done

[ -z "$SRC" ] && exit 0

# Reject anonymous references: pure hex SHAs (≥7 hex), HEAD~, @{, *_HEAD
if echo "$SRC" | grep -qE '^[0-9a-fA-F]{7,}$|^HEAD[~^]|^@\{|^FETCH_HEAD$|^ORIG_HEAD$|^MERGE_HEAD$'; then
  exit 0
fi

# -- Delegate to python stamp module (fail-open: || true ensures exit 0) --------
STAMP_SCRIPT="$(dirname "$0")/../merge_recorder_stamp.py"
if [ ! -f "$STAMP_SCRIPT" ]; then
  exit 0
fi

python "$STAMP_SCRIPT" "$SRC" 2>/dev/null || true

exit 0
