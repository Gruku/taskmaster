#!/usr/bin/env python3
"""worktree_submodule_init.py — PostToolUse hook for Bash.

Python port of worktree-submodule-init.sh (behavior-preserving; zero
subprocess spawns on the hot path — non-matching commands exit 0 having
spawned nothing).

After `git worktree add`, detects .gitmodules in the new worktree
and auto-initializes submodules. Injects context reminding the agent
to fetch commits back before worktree removal.

Exit 0 always — this is advisory, never blocks.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

WORKTREE_ADD_RE = re.compile(r"git\s+worktree\s+add\s")


def _git(args: list, combined: bool = False) -> "subprocess.CompletedProcess":
    """Run git with captured output. combined=True merges stderr into stdout
    (mirrors the shell's 2>&1)."""
    return subprocess.run(
        ["git"] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if combined else subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def parse_worktree_path(command: str) -> str:
    """Extract the worktree path (first non-flag arg after `git worktree add`).

    Handles: git worktree add <path> [-b <branch>] [<commit-ish>]
             git worktree add -b <branch> <path> [<commit-ish>]
    """
    worktree_path = ""
    skip_next = False
    in_worktree_add = False
    for token in command.split():
        if skip_next:
            skip_next = False
            continue
        if not in_worktree_add:
            if token == "add":
                in_worktree_add = True
            continue
        # Skip flags that take a value
        if token in ("-b", "-B"):
            skip_next = True
            continue
        # Skip bare flags
        if token.startswith("-"):
            continue
        # First non-flag token after `add` is the path
        worktree_path = token
        break
    return worktree_path


def _json_escape(s: str) -> str:
    """Escape a raw interpolated value for embedding in the JSON template.

    (The bash version embedded WORKTREE_PATH raw; escaping backslashes/quotes
    keeps the output valid JSON for native Windows paths.)
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0

    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command:
        return 0

    # Only act on git worktree add commands
    if not WORKTREE_ADD_RE.search(command):
        return 0

    worktree_path = parse_worktree_path(command)
    if not worktree_path:
        return 0

    # Resolve to absolute path if relative
    if not (worktree_path.startswith("/") or Path(worktree_path).is_absolute()):
        worktree_path = f"{Path.cwd()}/{worktree_path}"
    wt = Path(worktree_path)

    # Check if worktree has .gitmodules
    if not (wt / ".gitmodules").is_file():
        return 0

    # Get list of submodule paths
    try:
        r = _git(["-C", str(wt), "config", "--file", ".gitmodules",
                  "--get-regexp", r"submodule\..*\.path"])
    except Exception:
        return 0
    submodules = []
    if r.returncode == 0:
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                submodules.append(parts[1])

    if not submodules:
        return 0

    # Find the main checkout (the worktree's source repo)
    main_checkout = None
    try:
        r = _git(["-C", str(wt), "rev-parse", "--git-common-dir"])
        common_dir = (r.stdout or "").strip() if r.returncode == 0 else ""
        if common_dir:
            p = Path(common_dir)
            if not p.is_absolute():
                p = Path.cwd() / p
            if p.is_dir():
                # --git-common-dir returns the .git dir; parent is the checkout
                main_checkout = p.resolve().parent
    except Exception:
        main_checkout = None

    # Initialize submodules and track results
    try:
        r = _git(["-C", str(wt), "submodule", "update", "--init"], combined=True)
        init_output = (r.stdout or "").rstrip("\n")
        init_exit = r.returncode
    except Exception:
        init_output = ""
        init_exit = 1

    results = ""
    failures = ""

    # For each submodule, fetch from main checkout and validate
    for sub in submodules:
        sub_git = wt / sub / ".git"
        # Check if submodule init produced a working directory
        if not sub_git.is_file() and not sub_git.is_dir():
            failures += f"  - {sub}: submodule update --init failed (no .git in worktree)\\n"
            continue

        # Fetch from main checkout's copy (avoids network)
        if main_checkout is not None:
            main_sub_git = main_checkout / sub / ".git"
            if main_sub_git.is_dir() or main_sub_git.is_file():
                try:
                    fr = _git(["-C", str(wt / sub), "fetch", str(main_checkout / sub)],
                              combined=True)
                    fetch_output = (fr.stdout or "").rstrip("\n")
                    fetch_exit = fr.returncode
                except Exception:
                    fetch_output = ""
                    fetch_exit = 1
                if fetch_exit != 0:
                    failures += f"  - {sub}: fetch from main checkout failed: {fetch_output}\\n"
                    continue

        # Validate: confirm HEAD is resolvable (submodule is actually checked out)
        try:
            vr = _git(["-C", str(wt / sub), "rev-parse", "HEAD"])
        except Exception:
            vr = None
        if vr is None or vr.returncode != 0:
            failures += f"  - {sub}: initialized but HEAD is unresolvable (detached/corrupt)\\n"
            continue

        try:
            hr = _git(["-C", str(wt / sub), "rev-parse", "--short", "HEAD"])
            sub_head = (hr.stdout or "").strip() if hr.returncode == 0 else ""
        except Exception:
            sub_head = ""
        results += f"  - {sub} ✓ ({sub_head})\\n"

    # Build status message
    if failures:
        if init_exit != 0:
            status = f"❌ SUBMODULE INIT FAILED (exit {init_exit}): {init_output}"
        else:
            status = "⚠ PARTIAL FAILURE — some submodules failed validation"
        detail = (
            f"{status}\\n\\nSucceeded:\\n{results or '  (none)'}\\n\\nFailed:\\n{failures}"
            "\\nManual recovery required. Run in the worktree:"
            "\\n  git submodule update --init"
            "\\n  git -C <submodule> fetch <main-checkout>/<submodule>"
        )
    else:
        status = "✓ All submodules initialized and validated"
        detail = f"{status}\\n{results}"

    # Escape for JSON (same transform chain as the shell: backslash-double,
    # quote-escape, tab-escape, then real newlines -> spaces)
    detail_escaped = (
        detail.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\t", "\\t")
        .replace("\n", " ")
    )

    wt_escaped = _json_escape(worktree_path)
    sys.stdout.write(
        f"""{{
  "hookSpecificOutput": {{
    "hookEventName": "PostToolUse",
    "additionalContext": "SUBMODULE WORKTREE PROTOCOL — {wt_escaped}\\n\\n{detail_escaped}\\n\\nCommits in worktree submodules are ISOLATED — they will be LOST on worktree removal unless fetched back.\\n\\nBEFORE removing this worktree or merging, run for each submodule:\\n  git -C <main-checkout>/<submodule> fetch <worktree>/<submodule>\\n\\nThis ensures no commits are lost."
  }}
}}
"""
    )

    return 0


if __name__ == "__main__":
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # advisory hook — never blocks
