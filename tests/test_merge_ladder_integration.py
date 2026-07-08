r"""End-to-end merge-ladder integration test.

Drives REAL git operations through BOTH hooks (merge_gate.py + merge_recorder.py)
in a throw-away temp git repo.  These are the three scenarios the unit tests
cannot cover:

  1. Allow + record:  policy on, fresh review-gate pass (commit_sha == real
     branch tip) -> merge-gate exits 0 -> REAL git merge -> merge-recorder
     stamps merge_status in the HEAVY per-task file tasks/<id>.md, and the slim
     merge_gate_state mirror in backlog.yaml.

  2. Block:  policy on, NO review-gate -> merge-gate exits 2 with
     "review-gate" in stderr.  No merge performed, no merge_status written.

  3. Approve bypass + retry-survives:  policy on, NO gate, but fresh approval
     file present -> merge-gate exits 0 -> REAL git merge -> merge-recorder
     stamps the rung.  THEN prove NOT-consumed: run merge-gate again (same
     file, still within 60 s, on a fresh branch) -> exits 0 again.  Optionally
     verify that a stale approval (mtime >60 s ago) fires the block path again.

Subprocess timeout: 60 s per call (generous — git on Windows can be slow).
Module-level slow marker so CI can filter; tests are NOT skipped.

Path note: hooks are launched by ABSOLUTE path; subprocess cwd is set to the
temp repo root so both hooks' decision modules resolve the project root via
Path.cwd() exactly as in production.  TASKMASTER_ROOT is also set in the
recorder env so backlog_server._load()/_save() round-trips against the temp repo.
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

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
GATE_HOOK    = str((PLUGIN_ROOT / "hooks" / "merge_gate.py").resolve())
RECORDER_HOOK = str((PLUGIN_ROOT / "hooks" / "merge_recorder.py").resolve())

# Give every subprocess a generous budget — git on Windows can be slow.
_TIMEOUT = 60

# Slow marker: present so CI can --ignore or -m "not slow"; does NOT skip.
pytestmark = pytest.mark.slow


# --------------------------------------------------------------------------- #
# Hook runners
# --------------------------------------------------------------------------- #

def _run_gate(payload: dict, cwd: Path, home: Path | None = None) -> subprocess.CompletedProcess:
    """Run merge_gate.py with payload JSON on stdin.

    cwd=<repo> drives merge_gate_decide.py's Path.cwd() (no env seam in our code).
    home= overrides $HOME so the approval file lives in an isolated tmp dir.
    """
    env = dict(os.environ)
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, GATE_HOOK],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(cwd),
        env=env,
        timeout=_TIMEOUT,
    )


def _run_recorder(payload: dict, cwd: Path) -> subprocess.CompletedProcess:
    """Run merge_recorder.py with payload JSON on stdin.

    cwd=<repo> drives merge_recorder_stamp.py's Path.cwd().
    TASKMASTER_ROOT=<repo> points backlog_server's storage layer at the repo.
    """
    env = dict(os.environ)
    env["TASKMASTER_ROOT"] = str(cwd)
    return subprocess.run(
        [sys.executable, RECORDER_HOOK],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(cwd),
        env=env,
        timeout=_TIMEOUT,
    )


def _gate_payload(command: str, session_id: str = "ses-integ") -> dict:
    return {"tool_input": {"command": command}, "session_id": session_id}


def _recorder_payload(command: str, exit_code: int = 0) -> dict:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"exit_code": exit_code, "stdout": "", "stderr": ""},
    }


# --------------------------------------------------------------------------- #
# Repo + backlog seed helpers
# --------------------------------------------------------------------------- #

def _init_repo(repo: Path, *, target: str = "master", feature: str = "feature/integ") -> str:
    """Init a git repo: initial commit on `target`, a feature commit on `feature`.

    Returns to `target` branch with HEAD pointing to the initial commit.
    Returns the full SHA of the feature branch tip.
    """
    subprocess.run(["git", "init", "-b", target, str(repo)],
                   check=True, capture_output=True, timeout=_TIMEOUT)
    for cfg in [("user.email", "test@test.com"), ("user.name", "Test")]:
        subprocess.run(["git", "-C", str(repo), "config"] + list(cfg),
                       check=True, capture_output=True, timeout=_TIMEOUT)
    # Initial commit on target
    (repo / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"],
                   check=True, capture_output=True, timeout=_TIMEOUT)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True, timeout=_TIMEOUT)
    # Feature branch with one commit
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", feature],
                   check=True, capture_output=True, timeout=_TIMEOUT)
    (repo / "f.txt").write_text("feature", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"],
                   check=True, capture_output=True, timeout=_TIMEOUT)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "feat"],
                   check=True, capture_output=True, timeout=_TIMEOUT)
    feature_tip = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=_TIMEOUT,
    ).stdout.strip()
    # Return to target branch (HEAD = initial commit; feature not yet merged)
    subprocess.run(["git", "-C", str(repo), "checkout", target],
                   check=True, capture_output=True, timeout=_TIMEOUT)
    return feature_tip


def _do_real_merge(repo: Path, feature: str) -> str:
    """Perform a real --no-ff merge of `feature` into current branch.

    Returns the resulting merge commit SHA.
    """
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", feature, "-m", "merge"],
        check=True, capture_output=True, timeout=_TIMEOUT,
    )
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=_TIMEOUT,
    ).stdout.strip()


def _seed_backlog(
    repo: Path,
    *,
    tid: str = "integ-001",
    branch: str = "feature/integ",
    gates: dict | None = None,
) -> str:
    """Seed .taskmaster/project.yaml + backlog.yaml with policy=on and one task.

    `gates` is a HEAVY field (taskmaster_v3.HEAVY_FIELDS) — on a real v3
    backlog it lives only in the per-task file, never inline on the slim
    backlog.yaml task entry. When `gates` is given, also write a faithful
    tasks/<tid>.md carrying it in frontmatter (empty dict => no `gates` key
    at all, matching how _split_task_for_v3 strips empty containers on save).

    Returns the task id.
    """
    tm = repo / ".taskmaster"
    (tm / "tasks").mkdir(parents=True, exist_ok=True)
    (tm / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")

    (tm / "project.yaml").write_text(
        textwrap.dedent("""\
            schema_version: 1
            meta: {name: Integ, slug: integ, kind: app}
            conventions:
              policies:
                review_gate_required_for_merge: true
        """),
        encoding="utf-8",
    )

    task: dict = {
        "id": tid,
        "title": "Integration test task",
        "status": "in-progress",
        "priority": "high",
        "created": "2026-01-01T00:00",
        "branch": branch,
    }

    backlog = {
        "version": 3,
        "project": "integ",
        "meta": {"project": "integ", "schema_version": 3, "updated": "2026-01-01"},
        "context": {},
        "epics": [{"id": "core", "name": "Core", "tasks": [task]}],
        "phases": [],
    }
    (tm / "backlog.yaml").write_text(yaml.dump(backlog, allow_unicode=True), encoding="utf-8")

    if gates is not None:
        fm: dict = {"id": tid, "title": "Integration test task"}
        if gates:
            fm["gates"] = gates
        write_task_file(tm / "tasks" / f"{tid}.md", fm, "")

    return tid


# --------------------------------------------------------------------------- #
# Verification helpers (mirror test_merge_recorder_hook.py)
# --------------------------------------------------------------------------- #

def _read_heavy_merge_status(repo: Path, tid: str) -> dict:
    """Read merge_status from the HEAVY per-task file tasks/<id>.md."""
    tf = repo / ".taskmaster" / "tasks" / f"{tid}.md"
    if not tf.exists():
        return {}
    text = tf.read_text(encoding="utf-8")
    assert text.startswith("---"), f"task file missing frontmatter: {text[:80]!r}"
    fm = text.split("---", 2)[1]
    data = yaml.safe_load(fm) or {}
    return data.get("merge_status") or {}


def _read_slim_merge_gate_state(repo: Path, tid: str) -> str:
    """Read merge_gate_state from the SLIM backlog.yaml (proves slim mirror)."""
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


# --------------------------------------------------------------------------- #
# Scenario 1: Allow + record
# --------------------------------------------------------------------------- #

def test_allow_and_record(tmp_path):
    """Policy on, fresh pass (commit_sha == real branch tip).

    Flow:
      1. merge_gate.py -> exit 0  (gate clears)
      2. REAL git merge
      3. merge_recorder.py -> exit 0
      4. HEAVY tasks/<id>.md merge_status['master'] stamped with real merge SHA
      5. SLIM backlog.yaml merge_gate_state == 'master'
    """
    feature = "feature/integ"
    target  = "master"
    tid     = "integ-001"

    # Real git repo; feature_tip is the SHA the gate record must match.
    feature_tip = _init_repo(tmp_path, target=target, feature=feature)

    _seed_backlog(
        tmp_path,
        tid=tid,
        branch=feature,
        gates={"review-gate": {"verdict": "pass", "commit_sha": feature_tip}},
    )

    # --- Step 1: merge-gate must allow ---
    gate_payload = _gate_payload(f"git merge {feature}")
    gr = _run_gate(gate_payload, tmp_path)
    assert gr.returncode == 0, (
        f"merge-gate should ALLOW (fresh pass) but got exit {gr.returncode}.\n"
        f"stderr: {gr.stderr}\nstdout: {gr.stdout}"
    )

    # --- Step 2: REAL git merge ---
    merge_sha = _do_real_merge(tmp_path, feature)
    assert len(merge_sha) == 40, f"expected a full 40-char SHA, got: {merge_sha!r}"

    # --- Step 3: merge-recorder must run without error ---
    rec_payload = _recorder_payload(f"git merge {feature}", exit_code=0)
    rr = _run_recorder(rec_payload, tmp_path)
    assert rr.returncode == 0, (
        f"merge-recorder must never block (exit was {rr.returncode}).\n"
        f"stderr: {rr.stderr}\nstdout: {rr.stdout}"
    )

    # --- Step 4: HEAVY storage — merge_status in tasks/<id>.md ---
    heavy = _read_heavy_merge_status(tmp_path, tid)
    assert "master" in heavy, (
        f"expected 'master' rung in heavy merge_status, got: {heavy!r}\n"
        f"recorder stderr: {rr.stderr}"
    )
    recorded_sha = heavy["master"]["merge_commit"]
    assert recorded_sha == merge_sha, (
        f"expected merge_commit == {merge_sha!r}, got {recorded_sha!r}"
    )

    # --- Step 5: SLIM mirror — merge_gate_state in backlog.yaml ---
    slim_state = _read_slim_merge_gate_state(tmp_path, tid)
    assert slim_state == "master", (
        f"expected slim merge_gate_state == 'master', got {slim_state!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario 2: Block
# --------------------------------------------------------------------------- #

def test_block_no_gate(tmp_path):
    """Policy on, task has NO review-gate -> merge-gate exits 2.

    No merge is performed; no merge_status record exists afterwards.
    """
    feature = "feature/integ"
    target  = "master"
    tid     = "integ-001"

    _init_repo(tmp_path, target=target, feature=feature)
    # Seed with empty gates dict (no review-gate record at all)
    _seed_backlog(tmp_path, tid=tid, branch=feature, gates={})

    # --- merge-gate must BLOCK ---
    gr = _run_gate(_gate_payload(f"git merge {feature}"), tmp_path)
    assert gr.returncode == 2, (
        f"merge-gate should BLOCK (no gate) but got exit {gr.returncode}.\n"
        f"stderr: {gr.stderr}\nstdout: {gr.stdout}"
    )
    combined = (gr.stderr + gr.stdout).lower()
    assert "review-gate" in combined, (
        f"block message should mention 'review-gate'; got: {gr.stderr!r}"
    )

    # --- No merge performed -> no merge_status in heavy storage ---
    heavy = _read_heavy_merge_status(tmp_path, tid)
    assert heavy == {}, (
        f"no merge_status should exist after a block, got: {heavy!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario 3: Approve bypass + retry-survives (NOT consumed)
# --------------------------------------------------------------------------- #

def test_approve_bypass_and_retry_survives(tmp_path):
    """Approve bypass: policy on, no gate, but fresh approval file present.

    Flow:
      A. Touch approval file -> merge-gate exits 0 (bypass)
      B. REAL git merge #1
      C. merge-recorder stamps 'master' rung
      D. Verify HEAVY + SLIM storage
      E. Run merge-gate AGAIN (still within 60s, same approval file) on a second
         (hypothetical) branch -> exits 0 again (approval not consumed)
      F. Stale the approval file (mtime > 60 s) -> merge-gate exits 2 (block)
    """
    feature  = "feature/integ"
    feature2 = "feature/other"   # hypothetical second branch (not merged)
    target   = "master"
    tid      = "integ-001"
    sid      = "ses-bypass"

    # Isolated HOME so approval file doesn't collide with real ~/.claude/
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    approve_dir = fake_home / ".claude"
    approve_dir.mkdir()
    approve_file = approve_dir / f"taskmaster-merge-approve-{sid}"

    _init_repo(tmp_path, target=target, feature=feature)
    # Seed task for feature (no gates — this would normally block)
    _seed_backlog(tmp_path, tid=tid, branch=feature, gates={})
    # Also add a task for feature2 so the gate actually evaluates it
    tm = tmp_path / ".taskmaster"
    raw = yaml.safe_load((tm / "backlog.yaml").read_text(encoding="utf-8"))
    raw["epics"][0]["tasks"].append({
        "id": "integ-002",
        "title": "Other task",
        "status": "in-progress",
        "priority": "medium",
        "created": "2026-01-01T00:00",
        "branch": feature2,
    })
    (tm / "backlog.yaml").write_text(yaml.dump(raw, allow_unicode=True), encoding="utf-8")
    # v3: gates is a HEAVY field — write a real task file (no `gates` key)
    # so merge_gate_decide.py hits the confident "no gate -> BLOCK" path in
    # step F below, instead of "missing task file -> fail-open ALLOW". Give
    # it a non-empty body: save_v3 (taskmaster_v3.py:4301-4307) deletes a
    # per-task file entirely when it has no HEAVY_FIELDS content and no
    # body — id+title mirrored into frontmatter don't count — and step D's
    # recorder round-trips the WHOLE backlog through backlog_server's
    # _load()/_save(), which would otherwise prune this file before step F.
    write_task_file(
        tm / "tasks" / "integ-002.md",
        {"id": "integ-002", "title": "Other task"},
        "Task body so the file survives the step-D save_v3 round-trip.\n",
    )

    # --- A. Touch fresh approval file ---
    approve_file.touch()

    # --- B. merge-gate must ALLOW via bypass (policy on, no gate, but fresh file) ---
    gr = _run_gate(
        _gate_payload(f"git merge {feature}", session_id=sid),
        tmp_path,
        home=fake_home,
    )
    assert gr.returncode == 0, (
        f"merge-gate should ALLOW via approval bypass, got exit {gr.returncode}.\n"
        f"stderr: {gr.stderr}"
    )
    assert approve_file.exists(), "approval file must NOT be consumed on use"

    # --- C. REAL git merge #1 ---
    merge_sha = _do_real_merge(tmp_path, feature)

    # --- D. merge-recorder stamps the rung ---
    rr = _run_recorder(_recorder_payload(f"git merge {feature}", exit_code=0), tmp_path)
    assert rr.returncode == 0, (
        f"recorder must never block (exit {rr.returncode}).\nstderr: {rr.stderr}"
    )

    # HEAVY: tasks/<id>.md has merge_status['master']
    heavy = _read_heavy_merge_status(tmp_path, tid)
    assert "master" in heavy, (
        f"expected 'master' in heavy merge_status, got: {heavy!r}\n"
        f"recorder stderr: {rr.stderr}"
    )
    assert heavy["master"]["merge_commit"] == merge_sha

    # SLIM: merge_gate_state == 'master'
    assert _read_slim_merge_gate_state(tmp_path, tid) == "master"

    # --- E. Run merge-gate a SECOND time on feature2 (still within 60 s) ---
    # Approval file still fresh -> must allow again (not consumed)
    gr2 = _run_gate(
        _gate_payload(f"git merge {feature2}", session_id=sid),
        tmp_path,
        home=fake_home,
    )
    assert gr2.returncode == 0, (
        f"approval should survive second merge within 60 s, got exit {gr2.returncode}.\n"
        f"stderr: {gr2.stderr}"
    )
    assert approve_file.exists(), "approval file must still exist (not consumed)"

    # --- F. Stale the file (mtime > 60 s ago) -> block fires ---
    stale_mtime = time.time() - 61
    os.utime(str(approve_file), (stale_mtime, stale_mtime))

    gr3 = _run_gate(
        _gate_payload(f"git merge {feature2}", session_id=sid),
        tmp_path,
        home=fake_home,
    )
    assert gr3.returncode == 2, (
        f"stale approval should trigger BLOCK (exit 2), got exit {gr3.returncode}.\n"
        f"stderr: {gr3.stderr}"
    )
    combined3 = (gr3.stderr + gr3.stdout).lower()
    assert "review-gate" in combined3, (
        f"stale block message should mention 'review-gate'; got: {gr3.stderr!r}"
    )
