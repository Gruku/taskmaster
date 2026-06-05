#!/usr/bin/env python3
"""taskmaster_merge_approve.py — PostToolUse hook for AskUserQuestion.

Python port of taskmaster-merge-approve.sh (behavior-preserving).
Taskmaster's OWN approval writer for merge_gate.py.

Touches a time-limited approval file when the user picks "Approve".
Does NOT depend on guard-hooks being installed.

Namespace: $HOME/.claude/taskmaster-merge-approve-$SESSION_ID
Distinct from guard-hooks' guard-approve-* files — guard-hooks'
consume-approval.sh only burns guard-approve-* so it never interferes here.

Expiry: the file is NEVER consumed (no consumer hook exists).  The 60s
stat-based freshness window in merge_gate.py IS the expiry.  Within 60s
the user can retry a failed `git merge` without re-approving.  After 60s
the file goes stale and the next blocked merge re-prompts.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

APPROVE_RE = re.compile(r"^\s*approve\s*$", re.IGNORECASE)


def _home() -> Path:
    """$HOME with Path.home() fallback (Windows: HOME may be unset natively)."""
    h = os.environ.get("HOME")
    return Path(h) if h else Path.home()


def extract_answers(data: dict) -> list:
    """Emit each answer value on its own line (mirrors ask-question-approval.sh).

    jq semantics: (.tool_response.answers // .tool_output.answers // {}) — null
    falls through; object -> values, string -> itself, anything else -> empty.
    A non-object tool_response/tool_output made jq error -> no answers.
    """
    answers = None
    for key in ("tool_response", "tool_output"):
        container = data.get(key)
        if container is not None and not isinstance(container, dict):
            return []  # jq would error indexing a non-object -> empty
        if isinstance(container, dict):
            a = container.get("answers")
            if a is not None and a is not False:
                answers = a
                break
    if answers is None:
        return []
    if isinstance(answers, dict):
        values = list(answers.values())
    elif isinstance(answers, str):
        values = [answers]
    else:
        return []
    # jq -r prints strings raw and non-strings as compact JSON, one per line.
    lines = []
    for v in values:
        text = v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))
        lines.extend(text.split("\n"))
    return lines


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0

    session_id = data.get("session_id")
    if session_id is None or session_id is False or session_id == "":
        session_id = "default"

    lines = extract_answers(data)
    if not lines:
        return 0

    # Any answer exactly equal to "Approve" (case-insensitive, whitespace-trimmed)
    # triggers the taskmaster approval file.
    if any(APPROVE_RE.match(line) for line in lines):
        approve_file = _home() / ".claude" / f"taskmaster-merge-approve-{session_id}"
        try:
            approve_file.parent.mkdir(parents=True, exist_ok=True)
            approve_file.touch()
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
