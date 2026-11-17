#!/usr/bin/env python3
# User intent: record the user's "Approve" to a merge-gate question as the one explicit bypass of the merge gate, and
# only that: the question must be the gate's own `[merge-gate <branch>]` question and the session a real one, so a
# stray "Approve" to any other question never lets a merge through (team-server-008 D12).
"""taskmaster_merge_approve.py — PostToolUse hook for AskUserQuestion.

Taskmaster's OWN approval writer for merge_gate.py. Does NOT depend on guard-hooks being installed.

Appends {"branch": <branch>, "at": <epoch>} to $HOME/.claude/taskmaster-merge-approve-$SESSION_ID (one
O_APPEND write) when the user answers exactly "Approve" (trimmed, any case) to a question whose text starts with
`[merge-gate <branch>]`, in a session with a real `session_id` ([A-Za-z0-9_-]{1,128}). An "Approve" to any other
question, a "Deny", or a missing or malformed session id writes nothing.

Namespace: distinct from guard-hooks' guard-approve-* files.

Expiry: the file is NEVER consumed (no consumer hook exists). merge_gate.py honours a line for 60 s, so within that
window the user can retry a failed `git merge` without re-approving; after it the next blocked merge re-prompts.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

APPROVE_RE = re.compile(r"^\s*approve\s*$", re.IGNORECASE)
QUESTION_RE = re.compile(r"^\s*\[merge-gate ([^\]\r\n]{1,255})\]")
SESSION_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _home() -> Path:
    """$HOME with Path.home() fallback (Windows: HOME may be unset natively)."""
    h = os.environ.get("HOME")
    return Path(h) if h else Path.home()


def extract_answers(data: dict) -> list:
    """`(question, answer)` pairs of the AskUserQuestion response, answers one per line.

    (tool_response.answers // tool_output.answers): an object maps question text to answer; a bare string is an
    answer to no known question; anything else is empty.
    """
    answers = None
    for key in ("tool_response", "tool_output"):
        container = data.get(key)
        if container is not None and not isinstance(container, dict):
            return []
        if isinstance(container, dict):
            a = container.get("answers")
            if a is not None and a is not False:
                answers = a
                break
    if answers is None:
        return []
    if isinstance(answers, dict):
        items = list(answers.items())
    elif isinstance(answers, str):
        items = [("", answers)]
    else:
        return []
    pairs = []
    for question, value in items:
        text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
        for line in text.split("\n"):
            pairs.append((question if isinstance(question, str) else "", line))
    return pairs


def approved_branches(data: dict) -> list:
    """The branches the user approved in this response."""
    found = []
    for question, answer in extract_answers(data):
        if not APPROVE_RE.match(answer):
            continue
        match = QUESTION_RE.match(question)
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not SESSION_RE.fullmatch(session_id):
        return 0
    branches = approved_branches(data)
    if not branches:
        return 0
    approve_file = _home() / ".claude" / f"taskmaster-merge-approve-{session_id}"
    now = int(time.time())
    payload = "".join(json.dumps({"branch": branch, "at": now}, separators=(",", ":")) + "\n"
                      for branch in branches).encode("utf-8")
    try:
        approve_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(approve_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
    except OSError:
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
        sys.exit(0)
