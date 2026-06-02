r"""Tests for plugins/taskmaster/hooks/merge-recorder.sh (PostToolUse, never blocks).

Harness convention: mirrors test_merge_gate_hook.py — shells out via subprocess
so real bash + git + python path-resolution are all exercised.

Decision table tested (verifies .taskmaster/backlog.yaml after each run):
  - test_non_merge_command_noop         — non-merge command → exit 0, no record
  - test_failed_merge_not_recorded      — exit_code != 0 → no record
  - test_matched_rung_records           — target == "master" (ladder rung) →
                                          merge_status["master"] stamped
  - test_unmatched_target_records_branch_label — target == "foo" (not a rung) →
                                          merge_status["branch:foo"] recorded
  - test_untracked_source_noop          — no task.branch == SRC → no record
  - test_recorder_never_blocks          — even on internal error → returncode == 0

Path note (Windows/MSYS): the hook is launched by ABSOLUTE path with subprocess
cwd set to the test project dir.  No test-only env-var seam — merge_recorder_stamp.py
resolves the project root from the REAL Path.cwd() exactly as production does.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).parents[1]
HOOK = str((PLUGIN_ROOT / "hooks" / "merge-recorder.sh").resolve())

# On Windows, Python's subprocess resolves "bash" to the first entry on PATH,
# which may be WSL bash (lacks jq). shutil.which() uses Git bash (ships jq).
_BASH = shutil.which("bash") or "bash"


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run(payload: dict, cwd: Path) -> subprocess.CompletedProcess:
    """Run merge-recorder.sh with `payload` JSON on stdin.

    We invoke bash with the ABSOLUTE hook path and set cwd to the test project
    dir, so merge_recorder_stamp.py resolves the project root from the REAL
    process cwd (Path.cwd()) exactly as it does in production.  No env-var seam.
    """
    return subprocess.run(
        [_BASH, HOOK],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(cwd),           # REAL cwd — drives stamp module's Path.cwd()
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Seed helpers (mirrors test_merge_gate_hook.py conventions)
# ---------------------------------------------------------------------------

def _seed(
    tmp: Path,
    *,
    branch: str = "feature/x",
    merge_targets: list[dict] | None = None,
) -> None:
    """Write .taskmaster/project.yaml and a minimal v3 backlog.yaml."""
    tm = tmp / ".taskmaster"
    (tm / "tasks").mkdir(parents=True, exist_ok=True)

    # Build merge_targets section for project.yaml
    mt_yaml = ""
    if merge_targets is not None:
        mt_lines = ["    merge_targets:"]
        for rung in merge_targets:
            mt_lines.append(f"      - label: {rung['label']}")
            mt_lines.append(f"        branches: {rung['branches']}")
        mt_yaml = "\n" + "\n".join(mt_lines)

    (tm / "project.yaml").write_text(
        textwrap.dedent(f"""\
            schema_version: 1
            meta: {{name: T, slug: t, kind: app}}
            conventions:
              policies:
                review_gate_required_for_merge: false{mt_yaml}
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

    backlog = {
        "meta": {"project": "t", "schema_version": 3, "updated": "2026-01-01"},
        "context": {},
        "epics": [{"id": "core", "name": "Core", "tasks": [task]}],
        "phases": [],
    }
    (tm / "backlog.yaml").write_text(
        yaml.dump(backlog, allow_unicode=True), encoding="utf-8"
    )


def _init_git_repo(tmp: Path, feature_branch: str = "feature/x") -> tuple[str, str]:
    """Init a throw-away git repo with a main commit on 'master' and a feature branch.

    Returns (feature_sha, master_sha) — the HEAD of the feature branch and master.
    Leaves HEAD on master (the merge target).
    """
    subprocess.run(["git", "init", "-b", "master", str(tmp)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    # Initial commit on master
    dummy = tmp / "README.md"
    dummy.write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    master_sha = subprocess.run(
        ["git", "-C", str(tmp), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    # Feature branch
    subprocess.run(
        ["git", "-C", str(tmp), "checkout", "-b", feature_branch],
        check=True, capture_output=True,
    )
    f = tmp / "feature.txt"
    f.write_text("feature work", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp), "add", "feature.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp), "commit", "-m", "feature commit"],
        check=True, capture_output=True,
    )
    feature_sha = subprocess.run(
        ["git", "-C", str(tmp), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    # Return to master (simulating post-merge state)
    subprocess.run(
        ["git", "-C", str(tmp), "checkout", "master"],
        check=True, capture_output=True,
    )
    # Merge the feature branch
    subprocess.run(
        ["git", "-C", str(tmp), "merge", "--no-ff", feature_branch, "-m", "merge feature"],
        check=True, capture_output=True,
    )
    merge_sha = subprocess.run(
        ["git", "-C", str(tmp), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    return feature_sha, merge_sha


def _reload_task(tmp: Path) -> dict | None:
    """Reload .taskmaster/backlog.yaml and return the first task."""
    bp = tmp / ".taskmaster" / "backlog.yaml"
    data = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
    tasks = (data.get("epics") or [{}])[0].get("tasks", [])
    return tasks[0] if tasks else None


def _merge_payload(command: str, exit_code: int = 0) -> dict:
    """Build a PostToolUse payload for a Bash tool call."""
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"exit_code": exit_code, "stdout": "", "stderr": ""},
    }


# ---------------------------------------------------------------------------
# Decision-table tests
# ---------------------------------------------------------------------------


def test_non_merge_command_noop(tmp_path):
    """Non-merge command → exit 0, no merge_status written."""
    _seed(tmp_path)
    payload = _merge_payload("git status")
    r = run(payload, tmp_path)
    assert r.returncode == 0
    task = _reload_task(tmp_path)
    assert task is not None
    assert "merge_status" not in task


def test_failed_merge_not_recorded(tmp_path):
    """exit_code != 0 → hook exits 0 but writes no merge_status."""
    _init_git_repo(tmp_path, "feature/x")
    _seed(tmp_path, branch="feature/x")
    payload = _merge_payload("git merge feature/x", exit_code=1)
    r = run(payload, tmp_path)
    assert r.returncode == 0
    task = _reload_task(tmp_path)
    assert "merge_status" not in (task or {})


def test_matched_rung_records(tmp_path):
    """Successful merge to 'master' (a ladder rung) → merge_status['master'] stamped."""
    _init_git_repo(tmp_path, "feature/x")
    _seed(tmp_path, branch="feature/x")
    # Default ladder has "master" as a rung with branches: ["master", "main"]
    payload = _merge_payload("git merge feature/x", exit_code=0)
    r = run(payload, tmp_path)
    assert r.returncode == 0
    task = _reload_task(tmp_path)
    assert task is not None
    ms = task.get("merge_status") or {}
    assert "master" in ms, f"Expected 'master' rung in merge_status, got: {ms}"
    rec = ms["master"]
    assert "merge_commit" in rec
    assert len(rec["merge_commit"]) >= 7  # at least a short SHA


def test_unmatched_target_records_branch_label(tmp_path):
    """Successful merge to 'foo' (not a ladder rung) → merge_status['branch:foo'] recorded."""
    # Create git repo where current branch is 'foo' after the merge
    subprocess.run(["git", "init", "-b", "foo", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    dummy = tmp_path / "README.md"
    dummy.write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    # Create and merge feature branch
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "-b", "feature/x"],
        check=True, capture_output=True,
    )
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "f.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "feat"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "foo"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "merge", "--no-ff", "feature/x", "-m", "merge"],
        check=True, capture_output=True,
    )

    _seed(tmp_path, branch="feature/x")
    payload = _merge_payload("git merge feature/x", exit_code=0)
    r = run(payload, tmp_path)
    assert r.returncode == 0
    task = _reload_task(tmp_path)
    assert task is not None
    ms = task.get("merge_status") or {}
    assert "branch:foo" in ms, f"Expected 'branch:foo' in merge_status, got: {ms}"
    rec = ms["branch:foo"]
    assert "merge_commit" in rec


def test_untracked_source_noop(tmp_path):
    """No task whose branch == SRC → no record, exit 0."""
    _init_git_repo(tmp_path, "feature/x")
    # Seed backlog with a DIFFERENT branch so SRC won't match
    _seed(tmp_path, branch="feature/different")
    payload = _merge_payload("git merge feature/x", exit_code=0)
    r = run(payload, tmp_path)
    assert r.returncode == 0
    task = _reload_task(tmp_path)
    # No merge_status should be recorded since feature/x doesn't match any task
    assert "merge_status" not in (task or {})


def test_recorder_never_blocks(tmp_path):
    """Even with a completely broken environment, returncode is always 0."""
    # Run with no .taskmaster at all — stamp module should swallow all errors
    payload = _merge_payload("git merge feature/x", exit_code=0)
    r = run(payload, tmp_path)
    assert r.returncode == 0
