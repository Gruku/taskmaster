#!/bin/bash
# worktree-submodule-init.sh — PostToolUse hook for Bash
# After `git worktree add`, detects .gitmodules in the new worktree
# and auto-initializes submodules. Injects context reminding the agent
# to fetch commits back before worktree removal.
#
# Exit 0 always — this is advisory, never blocks.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if [ -z "$COMMAND" ]; then
  exit 0
fi

# Only act on git worktree add commands
if ! echo "$COMMAND" | grep -qE 'git\s+worktree\s+add\s'; then
  exit 0
fi

# Extract the worktree path (first non-flag arg after `git worktree add`)
# Handles: git worktree add <path> [-b <branch>] [<commit-ish>]
#          git worktree add -b <branch> <path> [<commit-ish>]
WORKTREE_PATH=""
SKIP_NEXT=false
IN_WORKTREE_ADD=false
for token in $COMMAND; do
  if [ "$SKIP_NEXT" = true ]; then
    SKIP_NEXT=false
    continue
  fi
  if [ "$IN_WORKTREE_ADD" = false ]; then
    if [ "$token" = "add" ]; then
      IN_WORKTREE_ADD=true
    fi
    continue
  fi
  # Skip flags that take a value
  if [ "$token" = "-b" ] || [ "$token" = "-B" ]; then
    SKIP_NEXT=true
    continue
  fi
  # Skip bare flags
  if [[ "$token" == -* ]]; then
    continue
  fi
  # First non-flag token after `add` is the path
  WORKTREE_PATH="$token"
  break
done

if [ -z "$WORKTREE_PATH" ]; then
  exit 0
fi

# Resolve to absolute path if relative
if [[ "$WORKTREE_PATH" != /* ]]; then
  WORKTREE_PATH="$(pwd)/$WORKTREE_PATH"
fi

# Check if worktree has .gitmodules
if [ ! -f "$WORKTREE_PATH/.gitmodules" ]; then
  exit 0
fi

# Get list of submodule paths
SUBMODULES=$(git -C "$WORKTREE_PATH" config --file .gitmodules --get-regexp 'submodule\..*\.path' 2>/dev/null | awk '{print $2}')

if [ -z "$SUBMODULES" ]; then
  exit 0
fi

# Find the main checkout (the worktree's source repo)
MAIN_CHECKOUT=$(git -C "$WORKTREE_PATH" rev-parse --git-common-dir 2>/dev/null)
if [ -n "$MAIN_CHECKOUT" ]; then
  # --git-common-dir returns the .git dir; parent is the checkout
  MAIN_CHECKOUT=$(cd "$MAIN_CHECKOUT" && cd .. && pwd)
fi

# Initialize submodules and track results
INIT_OUTPUT=$(git -C "$WORKTREE_PATH" submodule update --init 2>&1)
INIT_EXIT=$?

RESULTS=""
FAILURES=""

# For each submodule, fetch from main checkout and validate
for sub in $SUBMODULES; do
  # Check if submodule init produced a working directory
  if [ ! -f "$WORKTREE_PATH/$sub/.git" ] && [ ! -d "$WORKTREE_PATH/$sub/.git" ]; then
    FAILURES="${FAILURES}  - ${sub}: submodule update --init failed (no .git in worktree)\n"
    continue
  fi

  # Fetch from main checkout's copy (avoids network)
  if [ -d "$MAIN_CHECKOUT/$sub/.git" ] || [ -f "$MAIN_CHECKOUT/$sub/.git" ]; then
    FETCH_OUTPUT=$(git -C "$WORKTREE_PATH/$sub" fetch "$MAIN_CHECKOUT/$sub" 2>&1)
    FETCH_EXIT=$?
    if [ $FETCH_EXIT -ne 0 ]; then
      FAILURES="${FAILURES}  - ${sub}: fetch from main checkout failed: ${FETCH_OUTPUT}\n"
      continue
    fi
  fi

  # Validate: confirm HEAD is resolvable (submodule is actually checked out)
  if ! git -C "$WORKTREE_PATH/$sub" rev-parse HEAD >/dev/null 2>&1; then
    FAILURES="${FAILURES}  - ${sub}: initialized but HEAD is unresolvable (detached/corrupt)\n"
    continue
  fi

  SUB_HEAD=$(git -C "$WORKTREE_PATH/$sub" rev-parse --short HEAD 2>/dev/null)
  RESULTS="${RESULTS}  - ${sub} ✓ (${SUB_HEAD})\n"
done

# Build status message
if [ -n "$FAILURES" ]; then
  if [ $INIT_EXIT -ne 0 ]; then
    STATUS="❌ SUBMODULE INIT FAILED (exit ${INIT_EXIT}): ${INIT_OUTPUT}"
  else
    STATUS="⚠ PARTIAL FAILURE — some submodules failed validation"
  fi
  DETAIL="${STATUS}\n\nSucceeded:\n${RESULTS:-  (none)}\n\nFailed:\n${FAILURES}\nManual recovery required. Run in the worktree:\n  git submodule update --init\n  git -C <submodule> fetch <main-checkout>/<submodule>"
else
  STATUS="✓ All submodules initialized and validated"
  DETAIL="${STATUS}\n${RESULTS}"
fi

# Escape for JSON
DETAIL_ESCAPED=$(printf '%s' "$DETAIL" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g' | tr '\n' ' ')

cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "SUBMODULE WORKTREE PROTOCOL — ${WORKTREE_PATH}\n\n${DETAIL_ESCAPED}\n\nCommits in worktree submodules are ISOLATED — they will be LOST on worktree removal unless fetched back.\n\nBEFORE removing this worktree or merging, run for each submodule:\n  git -C <main-checkout>/<submodule> fetch <worktree>/<submodule>\n\nThis ensures no commits are lost."
  }
}
EOF

exit 0
