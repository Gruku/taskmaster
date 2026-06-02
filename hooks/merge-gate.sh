#!/bin/bash
# merge-gate.sh — PreToolUse hook for Bash commands.
# FAIL-OPEN: any uncertainty, error, or exception => allow (exit 0).
# Blocks (exit 2) ONLY when ALL of the following are true:
#   1. The command is a `git merge` of a named branch (not SHA/HEAD~)
#   2. project.yaml has review_gate_required_for_merge: true
#   3. A task with branch == <SRC> exists in backlog.yaml
#   4. skip_merge_gate is not true on that task
#   5. gates["review-gate"] is not a fresh pass for that branch tip
#
# Approval flow (taskmaster's OWN namespace — NOT guard-approve-*):
#   On block the AI calls AskUserQuestion with labels "Approve"/"Deny".
#   taskmaster-merge-approve.sh (PostToolUse on AskUserQuestion) writes
#   $HOME/.claude/taskmaster-merge-approve-$SESSION_ID on Approve.
#   This hook reads that file with 60s stat-based freshness — it is NEVER
#   consumed: the window IS the expiry, so a retry within 60s re-uses the
#   approval without re-prompting.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && SESSION_ID="default"
APPROVE_FILE="$HOME/.claude/taskmaster-merge-approve-$SESSION_ID"

# Bail fast when there is no command at all.
[ -z "$COMMAND" ] && exit 0

# 1. Short-circuit: command must contain the word "merge" (fast pre-filter).
echo "$COMMAND" | grep -q 'merge' || exit 0

# 2. Must be a `git` invocation that reaches `merge` as a subcommand.
#    Tolerates:  git merge …
#                git -C path merge …
#                git --no-pager merge …  etc.
if ! echo "$COMMAND" | grep -qE 'git([[:space:]]+(-[A-Za-z]\S*[[:space:]]+\S+|--[a-z][a-z-]*))*[[:space:]]+merge\b'; then
  exit 0
fi

# --- check_approval: 60s stat-based freshness (keyed on APPROVE_FILE) ---
# Reads the file — NEVER creates or deletes it (that is the writer's job).
check_approval() {
  if [ -f "$APPROVE_FILE" ]; then
    if [ "$(uname)" = "Linux" ] || [ "$(uname)" = "Darwin" ]; then
      FILE_AGE=$(( $(date +%s) - $(stat -c %Y "$APPROVE_FILE" 2>/dev/null || stat -f %m "$APPROVE_FILE" 2>/dev/null || echo 0) ))
    else
      # Windows (Git Bash / MSYS2) — stat -c works
      FILE_AGE=$(( $(date +%s) - $(stat -c %Y "$APPROVE_FILE" 2>/dev/null || echo 0) ))
    fi
    if [ "$FILE_AGE" -le 60 ] 2>/dev/null; then
      return 0
    fi
    # Expiry cleanup ONLY — NOT consumption. The approval file is intentionally
    # never burned on use (there is no consumer hook); the 60s window IS the
    # expiry, so a fresh approval survives repeated merge retries. We delete it
    # here only because it has already aged out (>60s), never because it was used.
    rm -f "$APPROVE_FILE"
  fi
  return 1
}

# --- block: emit Approve/Deny prompt and exit 2 (unless already approved) ---
block() {
  if check_approval; then
    exit 0
  fi
  cat >&2 <<EOF
⛔ merge-gate: $1

ACTION REQUIRED: Use the AskUserQuestion tool with EXACTLY this shape:
  question: one short sentence describing why the merge is gated
  options (use these labels verbatim — do not rename, translate, or add more):
    - label: "Approve"  description: "Merge anyway (approval valid for 60 s)"
    - label: "Deny"     description: "Cancel; do not merge"

After the user responds:
  - "Approve" → rerun the ORIGINAL git merge command unchanged
  - "Deny" or no response → do NOT run the command

Run /taskmaster:review-gate to pass the gate properly.
Do NOT create the approval file yourself.
EOF
  exit 2
}

# 3. Parse source branch from the merge command.
#    Strategy: strip "git [global-flags] merge [merge-flags] " then take the
#    last word that doesn't start with "-".  Reject anonymous refs
#    (HEAD~N, @{N}, pure hex SHAs).
#
#    We drop everything up to and including "merge", then walk tokens.
AFTER_MERGE=$(echo "$COMMAND" | sed -E 's/.*\bmerge[[:space:]]*//')

SRC=""
for tok in $AFTER_MERGE; do
  case "$tok" in
    -*) continue ;;   # flag — skip
  esac
  SRC="$tok"
  # Keep updating: last non-flag token is the branch name
done

[ -z "$SRC" ] && exit 0

# Reject anonymous references: pure hex SHAs (≥7 hex), HEAD~, @{, FETCH_HEAD, ORIG_HEAD
if echo "$SRC" | grep -qE '^[0-9a-fA-F]{7,}$|^HEAD[~^]|^@\{|^FETCH_HEAD$|^ORIG_HEAD$|^MERGE_HEAD$'; then
  exit 0
fi

# 4–7. Delegate to python decision module (fail-open on any error).
#      The script lives one directory up from this hooks/ directory.
DECIDE_SCRIPT="$(dirname "$0")/../merge_gate_decide.py"
if [ ! -f "$DECIDE_SCRIPT" ]; then
  exit 0
fi

DECISION=$(python "$DECIDE_SCRIPT" "$SRC" 2>/dev/null) || exit 0

case "$DECISION" in
  ALLOW*|"") exit 0 ;;
  BLOCK:*)   block "${DECISION#BLOCK:}" ;;
  *)         exit 0 ;;   # unrecognized -> fail-open
esac
