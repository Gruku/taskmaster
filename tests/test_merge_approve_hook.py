"""Tests for plugins/taskmaster/hooks/taskmaster_merge_approve.py.

PostToolUse hook for AskUserQuestion that writes the taskmaster-namespace
approval file when the user picks "Approve".  Four tests from the plan
(lines 824-866) verbatim, ported to invoke the Python hook directly.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parents[1]
HOOK = str((PLUGIN_ROOT / "hooks" / "taskmaster_merge_approve.py").resolve())


def run(payload: dict, home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home))
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(PLUGIN_ROOT),
        env=env,
        timeout=15,
    )


def _approve_file(home: Path, session: str = "s") -> Path:
    return home / ".claude" / f"taskmaster-merge-approve-{session}"


def test_approve_answer_touches_taskmaster_file(tmp_path):
    """Answer value 'Approve' => touch taskmaster-merge-approve-<session>."""
    payload = {
        "session_id": "s",
        "tool_response": {"answers": {"Proceed with merge?": "Approve"}},
    }
    r = run(payload, tmp_path)
    assert r.returncode == 0
    assert _approve_file(tmp_path).exists()


def test_deny_answer_does_not_touch_file(tmp_path):
    """Answer value 'Deny' => no approval file created."""
    payload = {
        "session_id": "s",
        "tool_response": {"answers": {"Proceed with merge?": "Deny"}},
    }
    run(payload, tmp_path)
    assert not _approve_file(tmp_path).exists()


def test_unrelated_question_does_not_touch_file(tmp_path):
    """Non-approve answer => no approval file created."""
    payload = {
        "session_id": "s",
        "tool_response": {"answers": {"Pick a color": "Blue"}},
    }
    run(payload, tmp_path)
    assert not _approve_file(tmp_path).exists()


def test_session_id_fallback_default(tmp_path):
    """No session_id in payload => falls back to 'default'."""
    payload = {"tool_response": {"answers": {"q": "Approve"}}}
    run(payload, tmp_path)
    assert _approve_file(tmp_path, "default").exists()
