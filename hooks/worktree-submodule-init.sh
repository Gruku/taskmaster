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

# Initialize submodules
git -C "$WORKTREE_PATH" submodule update --init 2>/dev/null

# For each submodule, fetch from main checkout's copy (avoids network)
for sub in $SUBMODULES; do
  if [ -d "$MAIN_CHECKOUT/$sub/.git" ] || [ -f "$MAIN_CHECKOUT/$sub/.git" ]; then
    git -C "$WORKTREE_PATH/$sub" fetch "$MAIN_CHECKOUT/$sub" 2>/dev/null || true
  fi
done

# Build the submodule list for the warning
SUB_LIST=$(echo "$SUBMODULES" | sed 's/^/  - /' | tr '\n' '\n')

cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "⚠ SUBMODULE WORKTREE PROTOCOL — Auto-initialized submodules in ${WORKTREE_PATH}:\n${SUB_LIST}\n\nSubmodules were fetched from the main checkout. Commits in worktree submodules are ISOLATED — they will be LOST on worktree removal unless fetched back.\n\nBEFORE removing this worktree or merging, run for each submodule:\n  git -C <main-checkout>/<submodule> fetch <worktree>/<submodule>\n\nThis ensures no commits are lost."
  }
}
EOF

exit 0
