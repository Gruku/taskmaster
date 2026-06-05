r"""Tests for plugins/taskmaster/hooks/merge_gate.py (PreToolUse, fail-open).

Harness convention: mirrors test_precompact_hook.py — shells out via
subprocess so real python + git path-resolution are all exercised.

Every test checks a row in the decision table:
  policy_off | no_yaml | not_a_merge | anonymous_merge | untracked_branch
  | corrupt_backlog | skip → ALLOW (exit 0)
  policy_on + fresh pass → ALLOW
  policy_on + stale pass + strict → BLOCK (exit 2)
  policy_on + stale pass + freshness=any → ALLOW
  policy_on + no gate → BLOCK
  existing approval (fresh file) → ALLOW even when block path fires
  approval survives second blocked merge within 60s (not consumed)

Path note: the hook is launched by ABSOLUTE path with the subprocess cwd set
to the test project dir, which makes merge_gate_decide.py resolve the project
root from the real Path.cwd() exactly as production does — no test-only env
seam.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).parents[1]
HOOK = str((PLUGIN_ROOT / "hooks" / "merge_gate.py").resolve())


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run(payload: dict, cwd: Path, home: Path | None = None) -> subprocess.CompletedProcess:
    """Run merge_gate.py with `payload` JSON on stdin.

    We invoke python with the ABSOLUTE hook path and set cwd to the test project
    dir, so the decision module resolves the project root from the REAL process
    cwd (Path.cwd()) exactly as it does in production.  No test-only env seam.
    """
    env = dict(os.environ)
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(cwd),           # REAL cwd — drives merge_gate_decide.py's Path.cwd()
        env=env,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# _seed helpers
# ---------------------------------------------------------------------------

def _seed(
    tmp: Path,
    *,
    policy: bool,
    gates: dict | None = None,
    skip: bool = False,
    freshness: str = "strict",
    branch: str = "feature/x",
    tip: str = "abc",
) -> None:
    """Write .taskmaster/project.yaml and a minimal v3 backlog.yaml."""
    tm = tmp / ".taskmaster"
    (tm / "tasks").mkdir(parents=True, exist_ok=True)

    (tm / "project.yaml").write_text(
        textwrap.dedent(f"""\
            schema_version: 1
            meta: {{name: T, slug: t, kind: app}}
            conventions:
              policies:
                review_gate_required_for_merge: {str(policy).lower()}
        """),
        encoding="utf-8",
    )

    task: dict = {
        "id": "T-001",
        "title": "Test task",
        "status": "in-progress",
        "priority": "high",
        "branch": branch,
    }
    if skip:
        task["skip_merge_gate"] = True
    if freshness != "strict":
        task["merge_gate_freshness"] = freshness
    if gates is not None:
        task["gates"] = gates

    backlog = {
        "meta": {"project": "t", "schema_version": 3, "updated": "2026-01-01"},
        "context": {},
        "epics": [{"id": "core", "name": "Core", "tasks": [task]}],
        "phases": [],
    }
    (tm / "backlog.yaml").write_text(
        yaml.dump(backlog, allow_unicode=True), encoding="utf-8"
    )


def _init_git_repo(tmp: Path, branch: str = "feature/x") -> str:
    """Init a throw-away git repo in tmp with one commit and a branch.

    Returns the commit SHA (full 40-char).
    """
    subprocess.run(["git", "init", str(tmp)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    dummy = tmp / "README.md"
    dummy.write_text("hi", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp), "add", "README.md"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(tmp), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp), "checkout", "-b", branch],
        check=True, capture_output=True,
    )
    f = tmp / "f.txt"
    f.write_text("feature", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp), "add", "f.txt"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(tmp), "commit", "-m", "feature"],
        check=True, capture_output=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(tmp), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return sha


# ---------------------------------------------------------------------------
# Decision-table tests
# ---------------------------------------------------------------------------


def test_not_a_merge_command_allows(tmp_path):
    """Non-merge bash command is immediately allowed."""
    r = run({"tool_input": {"command": "git status"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 0


def test_not_bash_command_allows(tmp_path):
    """Payload with no command key is immediately allowed."""
    r = run({"tool_input": {}, "session_id": "s"}, tmp_path)
    assert r.returncode == 0


def test_policy_off_allows(tmp_path):
    """Policy false => no gate => allow even when task has no gate record."""
    _seed(tmp_path, policy=False, gates={}, branch="feature/x")
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 0


def test_no_project_yaml_allows(tmp_path):
    """No .taskmaster/project.yaml => fail-open, allow."""
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 0


def test_policy_on_no_gate_blocks(tmp_path):
    """Policy on + task with no review-gate record => block."""
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 2
    combined = (r.stderr + r.stdout).lower()
    assert "review-gate" in combined or "taskmaster:review-gate" in combined


def test_policy_on_gate_failed_blocks(tmp_path):
    """Policy on + review-gate verdict=fail => block."""
    _seed(
        tmp_path, policy=True,
        gates={"review-gate": {"verdict": "fail", "commit_sha": "abc"}},
        branch="feature/x",
    )
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 2


def test_policy_on_fresh_pass_allows(tmp_path):
    """Policy on + fresh pass (commit_sha matches branch tip) => allow.

    Uses a real git repo so git rev-parse resolves correctly.
    """
    sha = _init_git_repo(tmp_path, branch="feature/x")
    _seed(
        tmp_path, policy=True,
        gates={"review-gate": {"verdict": "pass", "commit_sha": sha}},
        branch="feature/x",
        tip=sha,
    )
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 0


def test_policy_on_stale_pass_strict_blocks(tmp_path):
    """Policy on + review-gate commit_sha doesn't match branch tip + strict => block."""
    sha = _init_git_repo(tmp_path, branch="feature/x")
    _seed(
        tmp_path, policy=True,
        gates={"review-gate": {"verdict": "pass", "commit_sha": "OLDSHA1234567890"}},
        branch="feature/x",
        tip=sha,
        freshness="strict",
    )
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 2


def test_policy_on_stale_pass_freshness_any_allows(tmp_path):
    """Policy on + stale commit_sha + freshness=any => allow."""
    sha = _init_git_repo(tmp_path, branch="feature/x")
    _seed(
        tmp_path, policy=True,
        gates={"review-gate": {"verdict": "pass", "commit_sha": "OLDSHA1234567890"}},
        branch="feature/x",
        tip=sha,
        freshness="any",
    )
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 0


def test_skip_merge_gate_allows(tmp_path):
    """skip_merge_gate=true => allow regardless of gate state."""
    _seed(tmp_path, policy=True, gates={}, skip=True, branch="feature/x")
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 0


def test_untracked_branch_allows(tmp_path):
    """No task matches the source branch => allow."""
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    r = run(
        {"tool_input": {"command": "git merge feature/UNTRACKED"}, "session_id": "s"},
        tmp_path,
    )
    assert r.returncode == 0


def test_anonymous_merge_sha_allows(tmp_path):
    """Anonymous merge (HEAD~) => allow."""
    _seed(tmp_path, policy=True, gates={})
    r = run(
        {"tool_input": {"command": "git merge HEAD~1"}, "session_id": "s"}, tmp_path
    )
    assert r.returncode == 0


def test_anonymous_merge_detached_sha_allows(tmp_path):
    """Merge with a raw SHA (40 hex chars) is treated as anonymous => allow."""
    _seed(tmp_path, policy=True, gates={})
    r = run(
        {"tool_input": {"command": "git merge abc123def456abc123def456abc123def456abc1"}, "session_id": "s"},
        tmp_path,
    )
    assert r.returncode == 0


def test_corrupt_backlog_allows(tmp_path):
    """Corrupt backlog.yaml => fail-open, allow."""
    tm = tmp_path / ".taskmaster"
    tm.mkdir()
    (tm / "project.yaml").write_text(
        "schema_version: 1\nmeta: {name: T, slug: t}\n"
        "conventions:\n  policies:\n    review_gate_required_for_merge: true\n",
        encoding="utf-8",
    )
    (tm / "backlog.yaml").write_text("{[ not yaml", encoding="utf-8")
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 0


def test_merge_with_flags_still_detected(tmp_path):
    """git merge --no-ff feature/x is still a merge and is subject to gate."""
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    r = run(
        {"tool_input": {"command": "git merge --no-ff feature/x"}, "session_id": "s"},
        tmp_path,
    )
    assert r.returncode == 2


def test_merge_squash_still_detected(tmp_path):
    """git merge --squash feature/x is still a merge and is subject to gate."""
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    r = run(
        {"tool_input": {"command": "git merge --squash feature/x"}, "session_id": "s"},
        tmp_path,
    )
    assert r.returncode == 2


# ---------------------------------------------------------------------------
# Approval tests
# ---------------------------------------------------------------------------


def test_existing_approval_allows_blocked_merge(tmp_path):
    """Fresh taskmaster-merge-approve-<session> file => block path exits 0."""
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    home = tmp_path / "home"
    home.mkdir()
    approve_dir = home / ".claude"
    approve_dir.mkdir()
    approve_file = approve_dir / "taskmaster-merge-approve-s"
    approve_file.touch()

    r = run(
        {"tool_input": {"command": "git merge feature/x"}, "session_id": "s"},
        tmp_path,
        home=home,
    )
    assert r.returncode == 0


def test_approval_survives_second_blocked_merge(tmp_path):
    """Approval file is NOT consumed — second blocked merge within 60s still allowed."""
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    home = tmp_path / "home"
    home.mkdir()
    approve_dir = home / ".claude"
    approve_dir.mkdir()
    approve_file = approve_dir / "taskmaster-merge-approve-s"
    approve_file.touch()

    payload = {"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}
    r1 = run(payload, tmp_path, home=home)
    r2 = run(payload, tmp_path, home=home)
    assert r1.returncode == 0
    assert r2.returncode == 0
    # File must still be present (not consumed)
    assert approve_file.exists()
