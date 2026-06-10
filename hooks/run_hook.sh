#!/bin/sh
# run_hook.sh — resilient launcher for the Python hooks in this directory.
#
# Usage (from hooks.json) — SOURCED into the shell that interprets the hook
# command, so no extra process is spawned (MSYS bash process creation costs
# seconds under load; that overhead is the reason these hooks were ported
# off bash in the first place). The env-assignment prefix is the POSIX-
# portable way to pass the script name into a sourced file (`. file args`
# only carries args under bash):
#
#   CLAUDE_HOOK_SCRIPT=guard_bash.py . "${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh"
#
# Direct execution (bash run_hook.sh guard_bash.py) also works — the test
# suite uses that form.
#
# Why this exists instead of calling `python script.py` directly:
#   * `python missing.py` exits 2, and exit 2 from a PreToolUse hook is a
#     hard DENY — a half-updated plugin would block every matched tool call
#     for the whole session (observed 2026-06-10 in CodeMaestro: all
#     Write/Edit/Bash dead).
#   * Hook registrations are snapshotted at SessionStart, so renaming or
#     deleting a hook script strands every live session on a dead path.
#   * Bare `python` is not universal: some machines have only `python3` or
#     the `py` launcher, and Windows ships a Store stub named python.exe
#     that exits without running anything.
#
# Policy: fail OPEN. If the script or a usable interpreter is missing, warn
# loudly on stderr and exit 0 (tool allowed, hook inactive). A dead guard
# must degrade to "no guard", never to "deny everything". An intentional
# block from the hook itself (exit 2) passes through untouched.
#
# Hot path is builtins-only: the interpreter probe (a process spawn) runs
# once and is cached; every later call does string ops + one builtin read +
# the final exec, which REPLACES the shell rather than forking.
#
# NOTE: this file is duplicated byte-for-byte in the guard-hooks and
# taskmaster plugins (plugins must stay self-contained). A test asserts the
# copies match — edit both together.

# Locate this file: BASH_SOURCE when sourced under bash, $0 when executed,
# CLAUDE_PLUGIN_ROOT (exported to hook processes) as the env fallback.
hook_src=${BASH_SOURCE:-$0}
case "$hook_src" in
  */*)   hook_dir=${hook_src%/*} ;;
  *\\*)  hook_dir=${hook_src%\\*} ;;
  *)     hook_dir="${CLAUDE_PLUGIN_ROOT:-.}/hooks" ;;
esac

script_name=${CLAUDE_HOOK_SCRIPT:-${1:-}}
if [ -z "$script_name" ]; then
  echo "run_hook.sh: no hook script given (set CLAUDE_HOOK_SCRIPT or pass as \$1) — failing open (hook skipped)." >&2
  exit 0
fi

script="$hook_dir/$script_name"
if [ ! -f "$script" ]; then
  echo "run_hook.sh: hook script not found: $script" >&2
  echo "Failing OPEN: the tool call is allowed but this hook is INACTIVE." >&2
  echo "Fix: update/reinstall the plugin, then restart the Claude Code session." >&2
  exit 0
fi

# Resolve a Python >= 3.9. Probing spawns a process, so the first hit is
# cached; subsequent calls use only shell builtins before the final exec.
probe='import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'
cache="${TMPDIR:-/tmp}/claude-hooks-python.cache"

py=""
if [ -r "$cache" ]; then
  IFS= read -r py < "$cache" || py=""
  # Cheap builtin revalidation: drop the cache if the binary vanished.
  if [ -n "$py" ]; then
    command -v "${py%% *}" >/dev/null 2>&1 || py=""
  fi
fi

if [ -z "$py" ]; then
  for cand in "${CLAUDE_HOOKS_PYTHON:-}" python3 python "py -3"; do
    [ -n "$cand" ] || continue
    # </dev/null so a probe can never eat the hook JSON waiting on stdin.
    if $cand -c "$probe" </dev/null >/dev/null 2>&1; then
      py=$cand
      printf '%s\n' "$py" > "$cache" 2>/dev/null || true
      break
    fi
  done
fi

if [ -z "$py" ]; then
  echo "run_hook.sh: no usable Python >= 3.9 found (tried \$CLAUDE_HOOKS_PYTHON, python3, python, py -3)." >&2
  echo "Failing OPEN: tool calls are allowed but this plugin's hooks are INACTIVE on this machine." >&2
  echo "Fix: install Python 3.9+ (winget install Python.Python.3.12 | brew install python | apt install python3)" >&2
  echo "or set CLAUDE_HOOKS_PYTHON to an interpreter command." >&2
  exit 0
fi

# shellcheck disable=SC2086  # word-splitting wanted: py may be "py -3"
exec $py "$script"
