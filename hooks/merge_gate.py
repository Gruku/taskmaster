#!/usr/bin/env python3
# User intent: stop a gated merge from going through when the review-gate is missing or stale, and in a team-server
# clone never let it through just because the server did not answer; the user's explicit approval is the only bypass
# (team-server-008). Solo keeps its fail-open behaviour.
"""merge_gate.py — PreToolUse hook for Bash commands.

Zero subprocess spawns on the hot path: a command that does not name both `git` and `merge`/`pull` exits 0 having
spawned nothing. Otherwise `merge_command.parse` finds every branch the command merges (compound commands,
`-m msg`, octopus merges and `git -C <dir>` included) and each one is decided.

SOLO (no team-server marker): FAIL-OPEN, as before. Each branch is decided by `merge_gate_decide.py` in a
subprocess bounded to 9 s; any error, timeout or unrecognized answer allows the merge.

SERVER MODE (a clone with `.taskmaster-server.json` or its latch): FAIL-CLOSED. The decision runs in this process
(one interpreter start, one deadline) and ALLOW needs a verdict from a fresh server answer. An unreachable server,
a malformed answer, a command the reader cannot follow, an exception, or a decision still running 9 s after this
script started (a watchdog) all block. The host kills a PreToolUse hook at its timeout (30 s in hooks.json), and a
killed hook lets the command run, so the block has to land inside that time.

Approval flow (taskmaster's OWN namespace — NOT guard-approve-*), the only bypass:
  On block the AI calls AskUserQuestion with a question starting `[merge-gate <branch>]` and the labels
  "Approve"/"Deny". taskmaster_merge_approve.py (PostToolUse on AskUserQuestion) appends
  {"branch": <branch>, "at": <epoch>} to $HOME/.claude/taskmaster-merge-approve-$SESSION_ID on Approve, only for a
  question of that form and a real session id. This hook honours a line for the branch being merged for 60 s —
  it is NEVER consumed: the window IS the expiry, so a retry within 60 s re-uses the approval. A Bash command that
  names the approval file is refused.
"""
from __future__ import annotations

import time

_STARTED = time.monotonic()      # the watchdog and the read deadline both count from here

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_command

WATCHDOG_SECONDS = 9.0
SOLO_DECISION_SECONDS = 9.0
APPROVAL_SECONDS = 60.0
APPROVAL_MARKER = "taskmaster-merge-approve"
SESSION_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
FORGERY_TEXT = ("the merge-gate approval file is written only by the approval hook, from the user's answer; "
                "ask the user instead.")
WATCHDOG_TEXT = "the merge gate got no answer within 9 s; retry or bypass explicitly."

GUIDANCE = """merge-gate blocked this merge: {reason}
This project requires a passing review-gate at the branch tip before a tracked task branch merges. Either run the taskmaster:review-gate skill and merge once it passes, or ask the user whether to merge anyway: call AskUserQuestion with one short question that starts with `[merge-gate {branch}]` and says why the merge is gated, and two options labelled "Approve" (merge anyway; the approval lasts 60 s) and "Deny" (don't merge). The approval hook matches those two labels literally and only honours a question that starts with `[merge-gate {branch}]`, so keep both as written. On Approve, rerun the same git merge command unchanged; on Deny or no answer, don't merge. The approval has to come from the user's answer, so don't write the approval file or change the task's merge-gate settings yourself.
"""

UNAVAILABLE_GUIDANCE = """merge-gate blocked this merge: {reason}
This clone uses a Taskmaster team server, and without its answer the merge gate can't tell whether this branch's task has a passing review-gate, so it blocks instead of guessing. Either retry the merge once the server answers, or ask the user whether to merge without the gate: call AskUserQuestion with one short question that starts with `[merge-gate {branch}]` and says why, and two options labelled "Approve" (merge anyway; the approval lasts 60 s) and "Deny" (don't merge). The approval hook matches those two labels literally and only honours a question that starts with `[merge-gate {branch}]`, so keep both as written. On Approve, rerun the same git merge command unchanged; on Deny or no answer, don't merge. The approval has to come from the user's answer, so don't write the approval file or change the task's merge-gate settings yourself.
"""


def _home() -> Path:
    """$HOME with Path.home() fallback (Windows: HOME may be unset natively)."""
    h = os.environ.get("HOME")
    return Path(h) if h else Path.home()


def approval_path(session_id) -> Path | None:
    """The approval file of a real session, or None (no session id means no approval can be honoured)."""
    if not isinstance(session_id, str) or not SESSION_RE.fullmatch(session_id):
        return None
    return _home() / ".claude" / f"taskmaster-merge-approve-{session_id}"


def check_approval(approve_file: Path | None, branch: str, now: float | None = None) -> bool:
    """Whether the user approved merging `branch` in the last 60 s.

    Honours only a line `{"branch": <branch>, "at": <epoch>}` at most 60 s old, in a file whose mtime is inside the
    window too. Reads the file; never creates it (that is the approval hook's job). A file whose lines have all
    expired is removed (expiry cleanup, NOT consumption: a fresh approval survives repeated retries).
    """
    if approve_file is None:
        return False
    try:
        text = approve_file.read_text(encoding="utf-8", errors="replace")
        mtime = approve_file.stat().st_mtime
    except OSError:
        return False
    now = time.time() if now is None else now
    live = approved = False
    for line in text.splitlines():
        try:
            entry = json.loads(line)
            at = entry["at"]
            name = entry["branch"]
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(at, (int, float)) and -2.0 <= now - at <= APPROVAL_SECONDS:
            live = True
            approved = approved or name == branch
    if not live and now - mtime > APPROVAL_SECONDS:
        try:
            approve_file.unlink()
        except OSError:
            pass
        return False
    return approved and -2.0 <= now - mtime <= APPROVAL_SECONDS


def block_text(reason: str, branch: str) -> str:
    """The stderr text for a block. `reason` is the decision after `BLOCK:`; a `-` task slot means no verdict."""
    if reason.startswith("-:"):
        return UNAVAILABLE_GUIDANCE.format(reason=reason[2:].strip(), branch=branch)
    return GUIDANCE.format(reason=reason, branch=branch)


def block(reason: str, branch: str, approve_file: Path | None) -> int:
    """Emit the Approve/Deny prompt to stderr and return 2 (unless already approved)."""
    if check_approval(approve_file, branch):
        return 0
    sys.stderr.write(block_text(reason, branch))
    return 2


def _cwd_of(data: dict) -> Path:
    cwd = data.get("cwd")
    return Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()


def _directory(base: Path, merge) -> Path:
    if merge.directory is None:
        return base
    chosen = Path(merge.directory)
    return chosen if chosen.is_absolute() else base / chosen


class _Watchdog:
    """At `_STARTED + 9 s` with no answer out, a server-mode merge gets a block instead of the host's kill."""

    def __init__(self, cwd: Path, branches: list, approve_file: Path | None):
        self.cwd, self.branches, self.approve_file = cwd, branches, approve_file
        self.lock = threading.Lock()
        self.decided = False
        self.mode = None                 # None (not known yet), "solo" or "server"
        self.timer = threading.Timer(max(0.0, _STARTED + WATCHDOG_SECONDS - time.monotonic()), self.fire)
        self.timer.daemon = True
        self.timer.start()

    def answer(self) -> bool:
        """The main thread asks to answer; False when the watchdog already has."""
        with self.lock:
            if self.decided:
                return False
            self.decided = True
        self.timer.cancel()
        return True

    def fire(self) -> None:
        with self.lock:
            if self.decided:
                return
            if self.mode == "solo":
                return
            if self.mode is None and not merge_command.marker_file_above(self.cwd):
                return
            self.decided = True
            if self.branches and all(check_approval(self.approve_file, b) for b in self.branches):
                code = 0
            else:
                code = 2
                branch = next((b for b in self.branches if not check_approval(self.approve_file, b)), "")
                sys.stderr.write(block_text(f"-: {WATCHDOG_TEXT}", branch))
            try:
                sys.stderr.flush()
                sys.stdout.flush()
            finally:
                os._exit(code)


def _solo_decision(decide_script: Path, name: str, directory: str | None, tip_ref: str | None) -> str:
    """The solo decision in a bounded subprocess. Anything but a recognized BLOCK allows."""
    argv = [sys.executable or "python", str(decide_script), name]
    if directory is not None or tip_ref:
        argv.append(directory or str(Path.cwd()))
    if tip_ref:
        argv.append(tip_ref)
    try:
        from taskmaster.bounded_run import run_bounded
        result = run_bounded(argv, timeout=SOLO_DECISION_SECONDS, text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        return "ALLOW"
    if result.returncode != 0:
        return "ALLOW"
    return (result.stdout or "").rstrip("\n")


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

    # Anything that names the approval file is refused, in every mode and before the prefilter: the approval is
    # the user's answer, not something the agent writes.
    if APPROVAL_MARKER in command:
        sys.stderr.write(FORGERY_TEXT + "\n")
        return 2

    if not merge_command.prefilter(command):
        return 0
    parsed = merge_command.parse(command)
    if not parsed.merges and not parsed.unsure:
        return 0

    approve_file = approval_path(data.get("session_id"))
    cwd = _cwd_of(data)
    targets = [(merge, branch) for merge in parsed.merges for branch in merge.branches]
    dog = _Watchdog(cwd, [branch for _, branch in targets], approve_file)
    try:
        return _gate(data, parsed, targets, approve_file, cwd, dog)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        mode = dog.mode
        if mode is None:
            mode = "server" if merge_command.marker_file_above(cwd) else "solo"
        if mode == "server":
            # Server mode never fails open: whatever else goes wrong here, the answer is a block (review minor 3).
            if dog.answer():
                try:
                    first = targets[0][1] if targets else ""
                    import merge_gate_decide as gate
                    return block(f"-: {gate.unavailable_text('internal', type(exc).__name__)}", first, approve_file)
                except BaseException:  # noqa: BLE001
                    return 2
            return 2
        dog.answer()
        return 0  # solo: fail-open on any unexpected error


def _gate(data, parsed, targets, approve_file, cwd, dog) -> int:
    import merge_gate_decide as gate

    server = gate.server_clone(cwd)
    dog.mode = "server" if server else "solo"
    if parsed.unsure:
        if not server:
            dog.answer()
            return 0       # solo stays fail-open
        reason = f"-: {gate.unavailable_text('unparsed', parsed.unsure)}"
        if not dog.answer():
            time.sleep(60)
        return block(reason, "(this command)", approve_file)

    decide_script = Path(__file__).parent / "merge_gate_decide.py"
    if not server and not decide_script.is_file():
        dog.answer()
        return 0
    for merge, branch in targets:
        if check_approval(approve_file, branch):
            continue
        where = _directory(cwd, merge)
        decision = "ALLOW"
        for name in merge_command.branch_names(branch, where):
            if server:
                decision = gate.decide(name, where, started=_STARTED, server=True, tip_ref=branch)
            else:
                directory = str(where) if merge.directory is not None else None
                decision = _solo_decision(decide_script, name, directory, branch if name != branch else None)
            if decision.startswith("BLOCK:"):
                break
        if decision.startswith("BLOCK:"):
            if not dog.answer():
                time.sleep(60)
            return block(decision[len("BLOCK:"):], branch, approve_file)
    if not dog.answer():
        time.sleep(60)
    return 0


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
