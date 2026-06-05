r"""Tests for plugins/taskmaster/hooks/worktree_submodule_init.py (PostToolUse).

The bash original shipped without tests; this file covers the Python port:
  - benign / non-matching commands → exit 0, NO output (silent no-op)
  - `git worktree add` of a worktree without .gitmodules → silent no-op
  - real repo + submodule + worktree add → valid JSON on stdout with the
    SUBMODULE WORKTREE PROTOCOL context and a ✓ per initialized submodule

Harness convention: mirrors test_merge_gate_hook.py — shells out via
subprocess so real python + git are exercised.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parents[1]
HOOK = str((PLUGIN_ROOT / "hooks" / "worktree_submodule_init.py").resolve())

# git ≥2.38.1 forbids file-protocol submodule clones by default (CVE-2022-39253).
# The hook's internal `git submodule update --init` needs this override in the
# test fixture (production worktrees fetch from already-configured remotes).
_GIT_FILE_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "protocol.file.allow",
    "GIT_CONFIG_VALUE_0": "always",
}


def run(payload: dict, cwd: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=str(cwd),
        env=env,
        timeout=60,
    )


def _payload(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _git(args: list, cwd: Path) -> None:
    env = dict(os.environ)
    env.update(_GIT_FILE_ENV)
    subprocess.run(["git"] + args, cwd=str(cwd), env=env,
                   check=True, capture_output=True)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "master", "."], repo)
    _git(["config", "user.email", "test@test.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("hi", encoding="utf-8")
    _git(["add", "README.md"], repo)
    _git(["commit", "-m", "init"], repo)


# ---------------------------------------------------------------------------
# No-op paths (must be silent: exit 0, no stdout)
# ---------------------------------------------------------------------------


def test_benign_command_noop(tmp_path):
    r = run(_payload("echo hi"), tmp_path)
    assert r.returncode == 0
    assert r.stdout == ""


def test_missing_command_noop(tmp_path):
    r = run({"tool_name": "Bash", "tool_input": {}}, tmp_path)
    assert r.returncode == 0
    assert r.stdout == ""


def test_empty_stdin_noop(tmp_path):
    env = dict(os.environ)
    r = subprocess.run(
        [sys.executable, HOOK], input="", text=True, capture_output=True,
        cwd=str(tmp_path), env=env, timeout=30,
    )
    assert r.returncode == 0
    assert r.stdout == ""


def test_worktree_add_without_gitmodules_noop(tmp_path):
    """Real worktree add, but the repo has no .gitmodules → silent no-op."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    wt = tmp_path / "wt"
    _git(["worktree", "add", str(wt), "-b", "feature/wt"], repo)

    # NOTE: the matcher (same ERE as the bash original) only fires on a
    # contiguous `git worktree add …` — `git -C <repo> worktree add` is not
    # matched (preserved bash behavior).
    r = run(_payload(f"git worktree add {wt} -b feature/wt"), tmp_path)
    assert r.returncode == 0
    assert r.stdout == ""


def test_dash_c_form_not_matched_noop(tmp_path):
    """`git -C <repo> worktree add` does NOT match the (preserved) bash ERE."""
    r = run(_payload("git -C somerepo worktree add .worktrees/x -b feature/x"), tmp_path)
    assert r.returncode == 0
    assert r.stdout == ""


def test_nonexistent_path_noop(tmp_path):
    r = run(_payload("git worktree add .worktrees/ghost -b feature/ghost"), tmp_path)
    assert r.returncode == 0
    assert r.stdout == ""


# ---------------------------------------------------------------------------
# Submodule integration path
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_worktree_with_submodule_emits_protocol_context(tmp_path):
    """Repo with a submodule + real `git worktree add` → hook initializes the
    submodule in the worktree and emits valid additionalContext JSON."""
    subrepo = tmp_path / "subrepo"
    _init_repo(subrepo)
    main = tmp_path / "main"
    _init_repo(main)
    _git(["submodule", "add", subrepo.as_posix(), "sub"], main)
    _git(["commit", "-m", "add submodule"], main)

    wt = tmp_path / "wt"
    _git(["worktree", "add", str(wt), "-b", "feature/wt"], main)

    r = run(
        _payload(f"git worktree add {wt} -b feature/wt"),
        tmp_path,
        extra_env=_GIT_FILE_ENV,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)  # must be valid JSON
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "SUBMODULE WORKTREE PROTOCOL" in ctx
    assert "sub ✓" in ctx
    assert "LOST on worktree removal unless fetched back" in ctx
    # the submodule really got initialized in the worktree
    assert (wt / "sub" / "README.md").is_file()
