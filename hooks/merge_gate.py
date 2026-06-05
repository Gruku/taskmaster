#!/usr/bin/env python3
"""merge_gate.py — PreToolUse hook for Bash commands.

Python port of merge-gate.sh (behavior-preserving; zero subprocess spawns on
the hot path — non-matching commands exit 0 having spawned nothing).

FAIL-OPEN: any uncertainty, error, or exception => allow (exit 0).
Blocks (exit 2) ONLY when ALL of the following are true:
  1. The command is a `git merge` of a named branch (not SHA/HEAD~)
  2. project.yaml has review_gate_required_for_merge: true
  3. A task with branch == <SRC> exists in backlog.yaml
  4. skip_merge_gate is not true on that task
  5. gates["review-gate"] is not a fresh pass for that branch tip

Approval flow (taskmaster's OWN namespace — NOT guard-approve-*):
  On block the AI calls AskUserQuestion with labels "Approve"/"Deny".
  taskmaster_merge_approve.py (PostToolUse on AskUserQuestion) writes
  $HOME/.claude/taskmaster-merge-approve-$SESSION_ID on Approve.
  This hook reads that file with 60s stat-based freshness — it is NEVER
  consumed: the window IS the expiry, so a retry within 60s re-uses the
  approval without re-prompting.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Must be a `git` invocation that reaches `merge` as a subcommand.
# Tolerates:  git merge …
#             git -C path merge …
#             git --no-pager merge …  etc.
# (ERE from merge-gate.sh: git([[:space:]]+(-[A-Za-z]\S*[[:space:]]+\S+|--[a-z][a-z-]*))*[[:space:]]+merge\b)
GIT_MERGE_RE = re.compile(r"git(\s+(-[A-Za-z]\S*\s+\S+|--[a-z][a-z-]*))*\s+merge\b")

# Anonymous references: pure hex SHAs (≥7 hex), HEAD~, @{, FETCH_HEAD, ORIG_HEAD, MERGE_HEAD
ANON_REF_RE = re.compile(
    r"^[0-9a-fA-F]{7,}$|^HEAD[~^]|^@\{|^FETCH_HEAD$|^ORIG_HEAD$|^MERGE_HEAD$"
)


def _home() -> Path:
    """$HOME with Path.home() fallback (Windows: HOME may be unset natively)."""
    h = os.environ.get("HOME")
    return Path(h) if h else Path.home()


def check_approval(approve_file: Path) -> bool:
    """60s stat-based freshness (keyed on APPROVE_FILE).

    Reads the file — NEVER creates it (that is the writer's job).
    """
    if approve_file.is_file():
        try:
            mtime = approve_file.stat().st_mtime
        except OSError:
            mtime = 0
        file_age = time.time() - mtime
        if file_age <= 60:
            return True
        # Expiry cleanup ONLY — NOT consumption. The approval file is intentionally
        # never burned on use (there is no consumer hook); the 60s window IS the
        # expiry, so a fresh approval survives repeated merge retries. We delete it
        # here only because it has already aged out (>60s), never because it was used.
        try:
            approve_file.unlink()
        except OSError:
            pass
    return False


def parse_src_branch(command: str) -> str:
    """Parse source branch from the merge command.

    Strategy (identical to merge-gate.sh): strip everything up to and including
    "merge" (per line, greedy — mirrors `sed -E 's/.*\\bmerge[[:space:]]*//'`),
    walk whitespace-split tokens, last non-flag token = branch name.
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


def block(reason: str, approve_file: Path) -> int:
    """Emit Approve/Deny prompt to stderr and return 2 (unless already approved)."""
    if check_approval(approve_file):
        return 0
    sys.stderr.write(
        f"""⛔ merge-gate: {reason}

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
"""
    )
    return 2


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0  # malformed/empty stdin -> fail open
    if not isinstance(data, dict):
        return 0

    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command:
        # Bail fast when there is no command at all.
        return 0

    session_id = data.get("session_id")
    if session_id is None or session_id is False or session_id == "":
        session_id = "default"
    approve_file = _home() / ".claude" / f"taskmaster-merge-approve-{session_id}"

    # 1. Short-circuit: command must contain the word "merge" (fast pre-filter).
    if "merge" not in command:
        return 0

    # 2. Must be a `git` invocation that reaches `merge` as a subcommand.
    if not GIT_MERGE_RE.search(command):
        return 0

    # 3. Parse source branch from the merge command; reject anonymous refs
    #    (HEAD~N, @{N}, pure hex SHAs).
    src = parse_src_branch(command)
    if not src:
        return 0
    if ANON_REF_RE.search(src):
        return 0

    # 4–7. Delegate to python decision module (fail-open on any error).
    #      The script lives one directory up from this hooks/ directory.
    decide_script = Path(__file__).parent.parent / "merge_gate_decide.py"
    if not decide_script.is_file():
        return 0

    try:
        result = subprocess.run(
            [sys.executable or "python", str(decide_script), src],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return 0
    if result.returncode != 0:
        return 0

    decision = (result.stdout or "").rstrip("\n")
    if decision == "" or decision.startswith("ALLOW"):
        return 0
    if decision.startswith("BLOCK:"):
        return block(decision[len("BLOCK:"):], approve_file)
    return 0  # unrecognized -> fail-open


if __name__ == "__main__":
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail-open on any unexpected error
