r"""Tests for plugins/taskmaster/hooks/merge_recorder.py (PostToolUse, never blocks).

Harness convention: mirrors test_merge_gate_hook.py — shells out via subprocess
so real python + git + the REAL v3 storage layer are all exercised.

The recorder delegates persistence to backlog_server.backlog_record_merge, which
is the v3-correct path: heavy `merge_status` lands in tasks/<id>.md and the slim
`merge_gate_state` mirror is recomputed in backlog.yaml.  These tests therefore
verify BOTH against real storage (heavy file + slim backlog.yaml).

Decision table tested:
  - test_non_merge_command_noop         — non-merge command → exit 0, no record
  - test_failed_merge_not_recorded      — exit_code != 0 → no record
  - test_matched_rung_records           — target == "master" (ladder rung) →
                                          heavy merge_status["master"] stamped +
                                          slim merge_gate_state == "master"
  - test_unmatched_target_records_branch_label — target == "foo" (not a rung) →
                                          heavy merge_status["branch:foo"] +
                                          slim merge_gate_state == "" (no rung)
  - test_untracked_source_noop          — no task.branch == SRC → no record
  - test_recorder_never_blocks          — even on an internal error → exit 0

Path/env note: the hook is launched by ABSOLUTE path with subprocess cwd set to
the test project dir (drives the stamp module's Path.cwd() — no env seam in OUR
code).  We additionally set TASKMASTER_ROOT in the subprocess env: that is a
legitimate backlog_server config var (backlog_server.py line 38), not a
test-only hack — it points the storage layer at the repo so writes round-trip.
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
HOOK = str((PLUGIN_ROOT / "hooks" / "merge_recorder.py").resolve())


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run(payload: dict, cwd: Path) -> subprocess.CompletedProcess:
    """Run merge_recorder.py with `payload` JSON on stdin.

    cwd=<repo> drives merge_recorder_stamp.py's Path.cwd() (no env seam in our
    code).  TASKMASTER_ROOT=<repo> points backlog_server's storage layer at the
    same repo so its _load()/_save() round-trip against the seeded backlog.
    """
    env = dict(os.environ)
    env["TASKMASTER_ROOT"] = str(cwd)
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(cwd),
        env=env,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Real v3 storage seed + verification helpers
# ---------------------------------------------------------------------------

def _seed(
    repo: Path,
    *,
    branch: str = "feature/x",
    tid: str = "core-001",
) -> str:
    """Write .taskmaster/project.yaml + a minimal v3 backlog.yaml (task inline).

    The task is written inline in backlog.yaml (no per-task file yet); the v3
    loader tolerates this and the first save_v3 splits heavy fields out.
    Returns the task id.
    """
    tm = repo / ".taskmaster"
    (tm / "tasks").mkdir(parents=True, exist_ok=True)
    # PROGRESS.md must exist so regenerate_progress_dashboard() can read it.
    (tm / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")

    (tm / "project.yaml").write_text(
        textwrap.dedent("""\
            schema_version: 1
            meta: {name: T, slug: t, kind: app}
            conventions:
              policies:
                review_gate_required_for_merge: false
        """),
        encoding="utf-8",
    )

    task = {
        "id": tid,
        "title": "Test task",
        "status": "in-progress",
        "priority": "high",
        "created": "2026-01-01T00:00",
        "branch": branch,
    }
    backlog = {
        "version": 3,
        "project": "t",
        "meta": {"project": "t", "schema_version": 3, "updated": "2026-01-01"},
        "context": {},
        "epics": [{"id": "core", "name": "Core", "tasks": [task]}],
        "phases": [],
    }
    (tm / "backlog.yaml").write_text(
        yaml.dump(backlog, allow_unicode=True), encoding="utf-8"
    )
    return tid


def _read_heavy_merge_status(repo: Path, tid: str) -> dict:
    """Read merge_status directly out of the HEAVY per-task file tasks/<id>.md.

    Proves the field landed in heavy storage (not slim backlog.yaml).
    """
    tf = repo / ".taskmaster" / "tasks" / f"{tid}.md"
    if not tf.exists():
        return {}
    text = tf.read_text(encoding="utf-8")
    # Frontmatter is YAML between the first two '---' fences.
    assert text.startswith("---"), f"task file missing frontmatter: {text[:80]!r}"
    fm = text.split("---", 2)[1]
    data = yaml.safe_load(fm) or {}
    return data.get("merge_status") or {}


def _read_slim_merge_gate_state(repo: Path, tid: str) -> str:
    """Read merge_gate_state out of the SLIM backlog.yaml (proves slim mirror)."""
    bp = repo / ".taskmaster" / "backlog.yaml"
    data = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
    for epic in data.get("epics", []):
        for t in epic.get("tasks", []):
            if t.get("id") == tid:
                assert "merge_status" not in t, (
                    "merge_status leaked into slim backlog.yaml — must be heavy"
                )
                return t.get("merge_gate_state", "")
    return ""


# ---------------------------------------------------------------------------
# git repo helper
# ---------------------------------------------------------------------------

def _init_git_repo(repo: Path, *, target: str = "master", feature: str = "feature/x") -> None:
    """Init a git repo: initial commit on `target`, a `feature` branch, then
    merge feature into target (leaving HEAD on target = the merge commit)."""
    subprocess.run(["git", "init", "-b", target, str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    (repo / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", feature], check=True, capture_output=True)
    (repo / "f.txt").write_text("feature", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "feat"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "checkout", target], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", feature, "-m", "merge"],
        check=True, capture_output=True,
    )


def _merge_payload(command: str, exit_code: int = 0) -> dict:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"exit_code": exit_code, "stdout": "", "stderr": ""},
    }


# ---------------------------------------------------------------------------
# Decision-table tests
# ---------------------------------------------------------------------------


def test_non_merge_command_noop(tmp_path):
    """Non-merge command → exit 0, no merge record in heavy storage."""
    tid = _seed(tmp_path)
    r = run(_merge_payload("git status"), tmp_path)
    assert r.returncode == 0
    assert _read_heavy_merge_status(tmp_path, tid) == {}


def test_failed_merge_not_recorded(tmp_path):
    """exit_code != 0 → hook exits 0 but writes no merge record."""
    _init_git_repo(tmp_path, target="master", feature="feature/x")
    tid = _seed(tmp_path, branch="feature/x")
    r = run(_merge_payload("git merge feature/x", exit_code=1), tmp_path)
    assert r.returncode == 0
    assert _read_heavy_merge_status(tmp_path, tid) == {}


def test_matched_rung_records(tmp_path):
    """Successful merge to 'master' (a ladder rung) → heavy merge_status['master']
    stamped AND slim merge_gate_state == 'master'."""
    _init_git_repo(tmp_path, target="master", feature="feature/x")
    tid = _seed(tmp_path, branch="feature/x")
    r = run(_merge_payload("git merge feature/x", exit_code=0), tmp_path)
    assert r.returncode == 0

    # HEAVY: merge_status['master'] lives in tasks/<id>.md
    heavy = _read_heavy_merge_status(tmp_path, tid)
    assert "master" in heavy, f"expected 'master' in heavy merge_status, got: {heavy}"
    assert len(heavy["master"]["merge_commit"]) >= 7

    # SLIM: merge_gate_state mirror updated in backlog.yaml
    assert _read_slim_merge_gate_state(tmp_path, tid) == "master"


def test_unmatched_target_records_branch_label(tmp_path):
    """Successful merge to 'foo' (not a ladder rung) → heavy merge_status['branch:foo']
    recorded for the audit trail, but the slim merge_gate_state ladder mirror
    stays '' (compute_merge_gate_state only reports the highest *ladder* rung —
    a 'branch:<name>' label is intentionally not a rung)."""
    _init_git_repo(tmp_path, target="foo", feature="feature/x")
    tid = _seed(tmp_path, branch="feature/x")
    r = run(_merge_payload("git merge feature/x", exit_code=0), tmp_path)
    assert r.returncode == 0

    # HEAVY: the branch-label entry IS recorded (audit trail) in tasks/<id>.md
    heavy = _read_heavy_merge_status(tmp_path, tid)
    assert "branch:foo" in heavy, f"expected 'branch:foo' in heavy merge_status, got: {heavy}"
    assert "merge_commit" in heavy["branch:foo"]

    # SLIM: no ladder rung was reached, so the mirror is recomputed to '' —
    # and crucially merge_status did NOT leak into slim (asserted in the helper).
    assert _read_slim_merge_gate_state(tmp_path, tid) == ""


def test_untracked_source_noop(tmp_path):
    """No task whose branch == SRC → no record, exit 0."""
    _init_git_repo(tmp_path, target="master", feature="feature/x")
    tid = _seed(tmp_path, branch="feature/different")  # SRC won't match
    r = run(_merge_payload("git merge feature/x", exit_code=0), tmp_path)
    assert r.returncode == 0
    assert _read_heavy_merge_status(tmp_path, tid) == {}
    assert _read_slim_merge_gate_state(tmp_path, tid) == ""


def test_recorder_never_blocks(tmp_path):
    """Even with a completely broken environment (no .taskmaster), exit is 0."""
    r = run(_merge_payload("git merge feature/x", exit_code=0), tmp_path)
    assert r.returncode == 0
