#!/usr/bin/env python3
"""merge_recorder.py — PostToolUse (Bash). NEVER blocks (exit 0 always).

Python port of merge-recorder.sh (behavior-preserving; zero subprocess spawns
on the hot path — non-matching commands exit 0 having spawned nothing).

Stamps the reached merge rung on a task when a successful `git merge` is
detected.  Looks up the task whose branch == <SRC>, reads the post-merge
HEAD to determine the target branch, maps it to a ladder rung label (or
"branch:<name>" for untracked targets), then calls merge_recorder_stamp.py
to write merge_status into .taskmaster/backlog.yaml.

CARDINAL RULE: PostToolUse is advisory — exit code is IGNORED by the harness,
but we exit 0 explicitly on every path for defensive correctness.  On any
error or uncertainty, we silently do nothing and exit 0.

Source-branch parser:
  Duplicated from merge_gate.py (~10 lines) with a comment cross-link.
  Both hooks share the same strategy: strip everything up to and including
  "merge", walk tokens, last non-flag token = branch name.
  Cross-link: plugins/taskmaster/hooks/merge_gate.py parse_src_branch().
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Must be a `git` invocation reaching `merge` as a subcommand
# (same regex as merge_gate.py GIT_MERGE_RE)
GIT_MERGE_RE = re.compile(r"git(\s+(-[A-Za-z]\S*\s+\S+|--[a-z][a-z-]*))*\s+merge\b")

# Anonymous references: pure hex SHAs (≥7 hex), HEAD~, @{, *_HEAD
ANON_REF_RE = re.compile(
    r"^[0-9a-fA-F]{7,}$|^HEAD[~^]|^@\{|^FETCH_HEAD$|^ORIG_HEAD$|^MERGE_HEAD$"
)


# What merge_recorder_stamp.py imports through the server. Hooks run on whatever
# system Python exists; the plugin installs these only into its uv script environment.
STAMP_MODULES = ("fastmcp", "pydantic", "yaml")


def stamp_command(stamp_script: Path, src: str, *, deps_present: bool | None = None,
                  uv: str | None = "") -> list[str] | None:
    """argv for the stamp: this interpreter when it can import the server, else the
    stamp's own `uv run --script` environment (its PEP 723 header); None if neither."""
    if deps_present is None:
        deps_present = all(importlib.util.find_spec(name) is not None for name in STAMP_MODULES)
    if deps_present:
        return [sys.executable or "python", str(stamp_script), src]
    if uv == "":
        uv = shutil.which("uv")
    return [uv, "run", "--script", str(stamp_script), src] if uv else None


def run_stamp(argv: list[str], *, detach: bool) -> None:
    """Run the stamp. A `uv run` stamp may first build its environment (seconds), so it
    is started detached with no pipes back to this hook, which must return within its
    hooks.json timeout; the stamp logs its own outcome to .taskmaster/local/hook.log."""
    if not detach:
        subprocess.run(argv, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return
    options = ({'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
               if sys.platform == 'win32' else {'start_new_session': True})
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **options)


def parse_src_branch(command: str) -> str:
    """Parse source branch (cross-link: merge_gate.py parse_src_branch).

    Strategy: drop everything up to and including "merge", walk tokens,
    last non-flag token = branch name.
    """
    after_merge = "\n".join(
        re.sub(r".*\bmerge[ \t\r\f\v]*", "", line) for line in command.splitlines()
    )
    src = ""
    for tok in after_merge.split():
        if tok.startswith("-"):
            continue  # flag — skip
        src = tok
        # Keep updating: last non-flag token is the branch name
    return src


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0

    # -- Short-circuit: command must contain "merge" -----------------------------
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command:
        return 0
    if "merge" not in command:
        return 0

    # -- Must be a `git` invocation reaching `merge` as a subcommand -------------
    if not GIT_MERGE_RE.search(command):
        return 0

    # -- Only act on SUCCESSFUL merges --------------------------------------------
    # (jq semantics: .tool_response.exit_code // .exit_code // 1 — null/false
    #  fall through; a non-object tool_response made jq error -> not "0" -> exit 0)
    tool_response = data.get("tool_response")
    if tool_response is None:
        exit_code = None
    elif isinstance(tool_response, dict):
        exit_code = tool_response.get("exit_code")
    else:
        return 0
    if exit_code is None or exit_code is False:
        exit_code = data.get("exit_code")
    if exit_code is None or exit_code is False:
        exit_code = 1
    if str(exit_code) != "0":
        return 0

    # -- Parse source branch; reject anonymous refs --------------------------------
    src = parse_src_branch(command)
    if not src:
        return 0
    if ANON_REF_RE.search(src):
        return 0

    # -- Delegate to python stamp module (fail-open: errors ignored, exit 0) -------
    stamp_script = Path(__file__).parent / "merge_recorder_stamp.py"
    if not stamp_script.is_file():
        return 0

    argv = stamp_command(stamp_script, src)
    if argv is None:
        return 0
    try:
        run_stamp(argv, detach=argv[0] != (sys.executable or "python"))
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # advisory hook — never blocks
