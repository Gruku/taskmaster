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
import time
from pathlib import Path

import pytest
import yaml

from taskmaster.taskmaster_v3 import write_task_file

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
    """Write .taskmaster/project.yaml, a minimal v3 backlog.yaml, and (when
    `gates` is given) a faithful v3 tasks/T-001.md carrying `gates` in
    frontmatter.

    `gates` is a HEAVY field (taskmaster_v3.HEAVY_FIELDS) — on a real v3
    backlog it never lives inline on the slim backlog.yaml task entry, only
    in the per-task file (see taskmaster_v3._split_task_for_v3). A real
    "gate never run" v3 task file has NO `gates` key at all (empty
    containers are stripped on save), so `gates={}` writes a task file with
    just id/title — never `gates: {}`.
    """
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

    backlog = {
        "meta": {"project": "t", "schema_version": 3, "updated": "2026-01-01"},
        "context": {},
        "epics": [{"id": "core", "name": "Core", "tasks": [task]}],
        "phases": [],
    }
    (tm / "backlog.yaml").write_text(
        yaml.dump(backlog, allow_unicode=True), encoding="utf-8"
    )

    if gates is not None:
        fm: dict = {"id": "T-001", "title": "Test task"}
        if gates:
            fm["gates"] = gates
        write_task_file(tm / "tasks" / "T-001.md", fm, "")


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


def test_v3_heavy_gates_pass_allows(tmp_path):
    """v3 gates recorded only in tasks/<id>.md frontmatter (never inline in
    backlog.yaml) with verdict=pass and matching commit_sha => allow.

    Direct regression test for tm-audit-002: merge_gate_decide.py must
    hydrate the HEAVY `gates` field from the per-task file on schema v3.
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


def test_v3_missing_task_file_allows(tmp_path):
    """v3 backlog, task entry present and branch-matched, but tasks/<id>.md
    does not exist on disk => fail-open, allow (accepted risk per spec)."""
    _seed(tmp_path, policy=True, gates=None, branch="feature/x")
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 0


def test_v3_corrupt_task_file_allows(tmp_path):
    """v3 backlog, tasks/<id>.md exists but its frontmatter is malformed
    YAML => fail-open, allow."""
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    (tmp_path / ".taskmaster" / "tasks" / "T-001.md").write_text(
        "---\n{not: yaml\n---\nbody", encoding="utf-8"
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


def _approve(home: Path, session: str, *branches: str, age: float = 0.0) -> Path:
    path = home / ".claude" / f"taskmaster-merge-approve-{session}"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time() - age
    path.write_text("".join(json.dumps({"branch": b, "at": now}) + "\n" for b in branches), encoding="utf-8")
    os.utime(path, (now, now))
    return path


def test_existing_approval_allows_blocked_merge(tmp_path):
    """A fresh approval line for the branch => the block path exits 0."""
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    home = tmp_path / "home"
    _approve(home, "s", "feature/x")
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path, home=home)
    assert r.returncode == 0


def test_approval_survives_second_blocked_merge(tmp_path):
    """The approval is NOT consumed: a second blocked merge within 60 s is still allowed."""
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    home = tmp_path / "home"
    approve_file = _approve(home, "s", "feature/x")
    payload = {"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}
    assert run(payload, tmp_path, home=home).returncode == 0
    assert run(payload, tmp_path, home=home).returncode == 0
    assert approve_file.exists()


def test_an_empty_approval_file_no_longer_approves(tmp_path):
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "taskmaster-merge-approve-s").touch()
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path, home=home)
    assert r.returncode == 2


def test_an_approval_for_another_branch_or_an_old_one_does_not_approve(tmp_path):
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    home = tmp_path / "home"
    payload = {"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}
    _approve(home, "s", "feature/y")
    assert run(payload, tmp_path, home=home).returncode == 2
    _approve(home, "s", "feature/x", age=120)
    assert run(payload, tmp_path, home=home).returncode == 2
    assert not (home / ".claude" / "taskmaster-merge-approve-s").exists()      # all lines expired: removed


def test_no_session_id_means_no_approval_is_honoured(tmp_path):
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    home = tmp_path / "home"
    _approve(home, "default", "feature/x")
    assert run({"tool_input": {"command": "git merge feature/x"}}, tmp_path, home=home).returncode == 2


def test_both_block_texts_name_the_question_rule(tmp_path):
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    r = run({"tool_input": {"command": "git merge feature/x"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 2 and "starts with `[merge-gate feature/x]`" in r.stderr
    assert "merge-gate blocked this merge: T-001: no review-gate has been recorded" in r.stderr


# ---------------------------------------------------------------------------
# team-server-008 step 4: the shared reader reaches the decision
# ---------------------------------------------------------------------------


def test_a_message_option_no_longer_hides_the_branch(tmp_path):
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    for command in ("git merge --no-ff feature/x -m msg", "git status && git merge feature/x && git push",
                    "git merge feature/x; echo done", "git merge feature/x 2>&1 | tail -3"):
        r = run({"tool_input": {"command": command}, "session_id": "s"}, tmp_path)
        assert r.returncode == 2, command
        assert "[merge-gate feature/x]" in r.stderr


def test_an_octopus_merge_gates_every_named_branch(tmp_path):
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    r = run({"tool_input": {"command": "git merge feature/other feature/x"}, "session_id": "s"}, tmp_path)
    assert r.returncode == 2 and "[merge-gate feature/x]" in r.stderr


def test_a_read_only_merge_command_spawns_no_decision(tmp_path):
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    for command in ("git merge-base main feature/x", "git merge-tree a b", "git merge --abort", "git merge"):
        assert run({"tool_input": {"command": command}, "session_id": "s"}, tmp_path).returncode == 0, command


def test_solo_allows_a_command_the_reader_cannot_follow(tmp_path):
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    r = run({"tool_input": {"command": 'sh -c "git merge feature/x"'}, "session_id": "s"}, tmp_path)
    assert r.returncode == 0 and r.stderr == ""


def test_git_dash_c_judges_the_merge_in_that_clone(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _seed(other, policy=True, gates={}, branch="feature/x")
    here = tmp_path / "here"
    here.mkdir()
    r = run({"tool_input": {"command": f"git -C {other.as_posix()} merge feature/x"}, "session_id": "s",
             "cwd": str(here)}, here)
    assert r.returncode == 2 and "no review-gate has been recorded" in r.stderr


def test_a_remote_branch_is_checked_under_its_short_name_but_asked_about_as_written(tmp_path):
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    _init_git_repo(tmp_path, "scratch")
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "https://example.invalid/r.git"],
                   check=True, capture_output=True)
    home = tmp_path / "home"
    payload = {"tool_input": {"command": "git merge origin/feature/x"}, "session_id": "s"}
    r = run(payload, tmp_path, home=home)
    assert r.returncode == 2 and "[merge-gate origin/feature/x]" in r.stderr
    _approve(home, "s", "feature/x")                        # an approval for the short name is not enough
    assert run(payload, tmp_path, home=home).returncode == 2
    _approve(home, "s", "origin/feature/x")                 # the name as written lets the same command through
    assert run(payload, tmp_path, home=home).returncode == 0


def test_in_an_octopus_merge_approving_one_branch_still_blocks_on_the_other(tmp_path):
    _seed(tmp_path, policy=True, gates={}, branch="feature/x")
    tm = tmp_path / ".taskmaster"
    home = tmp_path / "home"
    payload = {"tool_input": {"command": "git merge feature/x feature/x"}, "session_id": "s"}
    assert run(payload, tmp_path, home=home).returncode == 2
    _approve(home, "s", "feature/x")
    assert run(payload, tmp_path, home=home).returncode == 0
    two = {"tool_input": {"command": "git merge feature/x feature/y"}, "session_id": "s"}
    assert run(two, tmp_path, home=home).returncode == 0       # feature/y has no task: allowed once x is approved
    assert tm.exists()


@pytest.mark.parametrize("command", [
    "touch ~/.claude/taskmaster-merge-approve-s1",
    "echo x > /home/u/.claude/taskmaster-merge-approve-s1",
    "cp a taskmaster-merge-approve-s1",
    'printf "{}" >> "$HOME/.claude/taskmaster-merge-approve-s"',
])
def test_a_command_naming_the_approval_file_is_refused_in_every_mode(tmp_path, command):
    r = run({"tool_input": {"command": command}, "session_id": "s"}, tmp_path)
    assert r.returncode == 2
    assert "the merge-gate approval file is written only by the approval hook, from the user's answer" in r.stderr
    teammate_dir = tmp_path / "tm"
    teammate_dir.mkdir()
    (teammate_dir / ".taskmaster-server.json").write_text("{}", encoding="utf-8")
    assert run({"tool_input": {"command": command}, "session_id": "s"}, teammate_dir).returncode == 2


def test_an_unrelated_command_is_unaffected_by_the_forgery_check(tmp_path):
    assert run({"tool_input": {"command": "ls -la"}, "session_id": "s"}, tmp_path).returncode == 0


# ---------------------------------------------------------------------------
# team-server-008 step 4: server mode (a teammate's clone)
# ---------------------------------------------------------------------------

from test_hooks_server_mode import HOST_ID, clone, teammate  # noqa: E402,F401  (fixtures)
from test_serve_read_api import root, serve  # noqa: E402,F401  (fixtures)


def run_in_clone(world, command, *, home, session="s", extra=None):
    payload = {"tool_input": {"command": command}, "session_id": session, "cwd": str(world.clone), **(extra or {})}
    env = dict(world.env, HOME=str(home))
    return subprocess.run([sys.executable, HOOK], input=json.dumps(payload), text=True, encoding="utf-8",
                          capture_output=True, cwd=str(world.clone), env=env, timeout=60)


def test_a_teammate_with_a_stale_gate_is_blocked_with_the_verdict_text(teammate, tmp_path):
    r = run_in_clone(teammate, "git merge shared", home=tmp_path / "h")
    assert r.returncode == 2
    assert "merge-gate blocked this merge: test-epic-001: no review-gate has been recorded" in r.stderr
    assert "[merge-gate shared]" in r.stderr
    assert "This clone uses a Taskmaster team server" not in r.stderr


def test_a_teammate_whose_server_is_down_is_blocked_with_the_unavailable_text(teammate, tmp_path):
    teammate.daemon.request_stop("server goes away")
    teammate.thread.join(30)
    started = time.monotonic()
    r = run_in_clone(teammate, "git merge shared", home=tmp_path / "h")
    assert r.returncode == 2 and time.monotonic() - started < 15
    assert "This clone uses a Taskmaster team server" in r.stderr
    assert "Taskmaster server unreachable; retry or bypass explicitly" in r.stderr


def test_a_fresh_approval_lets_the_merge_through_while_the_server_is_down(teammate, tmp_path):
    teammate.daemon.request_stop("server goes away")
    teammate.thread.join(30)
    home = tmp_path / "h"
    _approve(home, "s", "shared")
    started = time.monotonic()
    r = run_in_clone(teammate, "git merge shared", home=home)
    assert r.returncode == 0 and time.monotonic() - started < 8


def test_a_command_the_reader_cannot_follow_blocks_in_a_teammate_clone(teammate, tmp_path):
    r = run_in_clone(teammate, 'sh -c "git merge shared"', home=tmp_path / "h")
    assert r.returncode == 2
    assert "can't tell with confidence which branch this command merges" in r.stderr


def test_a_branch_name_the_server_cannot_be_asked_about_blocks(teammate, tmp_path):
    r = run_in_clone(teammate, "git merge release#1", home=tmp_path / "h")
    assert r.returncode == 2 and "can't be sent to the Taskmaster server" in r.stderr


def test_a_teammate_non_merge_command_spawns_nothing_and_passes(teammate, tmp_path):
    for command in ("git status", "git merge-base main shared", "ls"):
        assert run_in_clone(teammate, command, home=tmp_path / "h").returncode == 0, command


def test_the_teammate_decision_runs_in_this_process(teammate, tmp_path, monkeypatch):
    import io
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_gate_inproc", HOOK)
    gate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gate
    spec.loader.exec_module(gate)
    for name, value in teammate.env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    payload = {"tool_input": {"command": "git merge shared"}, "session_id": "s", "cwd": str(teammate.clone)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    spawned = []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def watch(args):
        if isinstance(args, (list, tuple)) and args and "python" in str(args[0]).lower():
            spawned.append(list(args))
        if isinstance(args, (list, tuple)) and any("merge_gate_decide" in str(a) for a in args):
            spawned.append(list(args))

    def run_spy(args, *a, **k):
        watch(args)
        return real_run(args, *a, **k)

    class PopenSpy(real_popen):
        def __init__(self, args, *a, **k):
            watch(args)
            super().__init__(args, *a, **k)
    monkeypatch.setattr(subprocess, "run", run_spy)
    monkeypatch.setattr(subprocess, "Popen", PopenSpy)
    assert gate.main() == 2
    assert spawned == []


# ── the watchdog ────────────────────────────────────────────────────────────────

WRAPPER = """
import sys, time
sys.path.insert(0, {hooks!r})
import merge_gate
import merge_gate_decide
merge_gate_decide.decide = lambda *a, **k: time.sleep(30)
sys.exit(merge_gate.main())
"""


def test_the_watchdog_blocks_a_silent_decision_at_nine_seconds(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    (clone_dir / ".taskmaster-server.json").write_text(json.dumps(
        {"format": 1, "host_id": HOST_ID, "mode": "server", "url": "https://team.example/mcp"}), encoding="utf-8")
    wrapper = tmp_path / "wrap.py"
    wrapper.write_text(WRAPPER.format(hooks=str(Path(HOOK).parent)), encoding="utf-8")
    payload = {"tool_input": {"command": "git merge shared"}, "session_id": "s", "cwd": str(clone_dir)}
    env = dict(os.environ, HOME=str(tmp_path / "h"))
    env.pop("TASKMASTER_ROOT", None)
    started = time.monotonic()
    r = subprocess.run([sys.executable, str(wrapper)], input=json.dumps(payload), text=True, encoding="utf-8",
                       capture_output=True, cwd=str(clone_dir), env=env, timeout=60)
    elapsed = time.monotonic() - started
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "the merge gate got no answer within 9 s; retry or bypass explicitly." in r.stderr
    assert 8.8 <= elapsed <= 9.8 + 3, elapsed          # interpreter and import start-up are inside the 9 s


def test_the_watchdog_does_nothing_in_a_solo_project(tmp_path):
    hooks = Path(HOOK).parent
    fake = tmp_path / "hooks"
    fake.mkdir()
    for name in ("merge_gate.py", "merge_command.py"):
        (fake / name).write_text((hooks / name).read_text(encoding="utf-8"), encoding="utf-8")
    (fake / "merge_gate_decide.py").write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(hooks.parent)!r})\n"
        "def server_clone(cwd):\n    return False\n"
        "if __name__ == '__main__':\n    time.sleep(11)\n    print('BLOCK:T-1: too late')\n", encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()
    _seed(project, policy=True, gates={}, branch="feature/x")
    payload = {"tool_input": {"command": "git merge feature/x"}, "session_id": "s", "cwd": str(project)}
    env = dict(os.environ, HOME=str(tmp_path / "h"))
    started = time.monotonic()
    r = subprocess.run([sys.executable, str(fake / "merge_gate.py")], input=json.dumps(payload), text=True,
                       encoding="utf-8", capture_output=True, cwd=str(project), env=env, timeout=60)
    elapsed = time.monotonic() - started
    assert r.returncode == 0 and "got no answer" not in r.stderr, (r.stdout, r.stderr)
    assert 8.5 <= elapsed < 11, elapsed
