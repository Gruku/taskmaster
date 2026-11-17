"""Tests for hooks/taskmaster_merge_approve.py and the approval half of hooks/merge_gate.py.

PostToolUse hook for AskUserQuestion that records the user's "Approve" to a `[merge-gate <branch>]` question, for
that branch only, in a real session only (team-server-008 D12). The gate honours it for 60 s.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parents[1]
HOOK = str((PLUGIN_ROOT / "hooks" / "taskmaster_merge_approve.py").resolve())
GATE = str((PLUGIN_ROOT / "hooks" / "merge_gate.py").resolve())
QUESTION = "[merge-gate feature/x] Merge anyway? The server is unreachable."


def run(payload: dict, home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home))
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(PLUGIN_ROOT),
        env=env,
        timeout=30,
    )


def _approve_file(home: Path, session: str = "s") -> Path:
    return home / ".claude" / f"taskmaster-merge-approve-{session}"


def _lines(home: Path, session: str = "s") -> list:
    path = _approve_file(home, session)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _answer(question, answer, session="s"):
    return {"session_id": session, "tool_response": {"answers": {question: answer}}}


def test_approve_to_the_gates_question_records_that_branch(tmp_path):
    r = run(_answer(QUESTION, "Approve"), tmp_path)
    assert r.returncode == 0
    (entry,) = _lines(tmp_path)
    assert entry["branch"] == "feature/x" and abs(entry["at"] - time.time()) < 30


@pytest.mark.parametrize("question", ["Proceed with merge?", "Pick a color", "merge-gate feature/x? Approve it",
                                      " x [merge-gate feature/x] late", ""])
def test_approve_to_any_other_question_writes_nothing(tmp_path, question):
    run(_answer(question, "Approve"), tmp_path)
    assert not _approve_file(tmp_path).exists()


def test_a_bare_string_answer_writes_nothing(tmp_path):
    run({"session_id": "s", "tool_response": {"answers": "Approve"}}, tmp_path)
    assert not _approve_file(tmp_path).exists()


def test_deny_writes_nothing(tmp_path):
    run(_answer(QUESTION, "Deny"), tmp_path)
    assert not _approve_file(tmp_path).exists()


@pytest.mark.parametrize("session", [None, "", "../x", "a b", "x" * 129, 5])
def test_a_missing_or_malformed_session_id_writes_nothing(tmp_path, session):
    payload = _answer(QUESTION, "Approve")
    if session is None:
        del payload["session_id"]
    else:
        payload["session_id"] = session
    run(payload, tmp_path)
    assert not (tmp_path / ".claude").exists() or not list((tmp_path / ".claude").iterdir())


def test_approve_is_trimmed_and_case_insensitive(tmp_path):
    run(_answer(QUESTION, "  aPPrOve "), tmp_path)
    assert len(_lines(tmp_path)) == 1


def test_a_second_approval_appends(tmp_path):
    run(_answer(QUESTION, "Approve"), tmp_path)
    run(_answer("[merge-gate feature/y] ok?", "Approve"), tmp_path)
    assert [e["branch"] for e in _lines(tmp_path)] == ["feature/x", "feature/y"]


def test_one_response_may_approve_several_branches(tmp_path):
    payload = {"session_id": "s", "tool_response": {"answers": {QUESTION: "Approve",
                                                                  "[merge-gate b] ok?": "Approve",
                                                                  "[merge-gate c] ok?": "Deny"}}}
    run(payload, tmp_path)
    assert [e["branch"] for e in _lines(tmp_path)] == ["feature/x", "b"]


# ── the reader (merge_gate.check_approval) ────────────────────────────────────────


@pytest.fixture
def gate_module():
    spec = importlib.util.spec_from_file_location("gate_launcher_for_approval", GATE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, *entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def test_the_gate_honours_the_branch_within_60_s_and_no_other(gate_module, tmp_path):
    path = _approve_file(tmp_path)
    _write(path, {"branch": "feature/x", "at": time.time() - 5})
    assert gate_module.check_approval(path, "feature/x") is True
    assert gate_module.check_approval(path, "feature/y") is False
    assert path.exists()                       # not consumed


def test_the_gate_does_not_honour_an_old_line_and_removes_the_expired_file(gate_module, tmp_path):
    path = _approve_file(tmp_path)
    _write(path, {"branch": "feature/x", "at": time.time() - 120})
    os.utime(path, (time.time() - 120, time.time() - 120))
    assert gate_module.check_approval(path, "feature/x") is False
    assert not path.exists()


def test_a_fresh_line_beside_an_expired_one_keeps_the_file(gate_module, tmp_path):
    path = _approve_file(tmp_path)
    _write(path, {"branch": "old", "at": time.time() - 500}, {"branch": "feature/x", "at": time.time()})
    assert gate_module.check_approval(path, "feature/x") is True
    assert gate_module.check_approval(path, "old") is False
    assert path.exists()


def test_a_stale_mtime_is_not_honoured_even_with_a_fresh_line(gate_module, tmp_path):
    path = _approve_file(tmp_path)
    _write(path, {"branch": "feature/x", "at": time.time()})
    os.utime(path, (time.time() - 300, time.time() - 300))
    assert gate_module.check_approval(path, "feature/x") is False


def test_an_empty_or_garbage_file_approves_nothing(gate_module, tmp_path):
    path = _approve_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.touch()
    assert gate_module.check_approval(path, "feature/x") is False
    path.write_text("approve\n{not json\n[]\n", encoding="utf-8")
    assert gate_module.check_approval(path, "feature/x") is False


def test_no_session_means_no_approval(gate_module):
    assert gate_module.approval_path(None) is None
    assert gate_module.approval_path("default") is not None      # a real id, though the writer never mints it
    assert gate_module.approval_path("../x") is None
    assert gate_module.check_approval(None, "feature/x") is False
