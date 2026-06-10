# -*- coding: utf-8 -*-
"""Legacy hook shims + launcher presence for taskmaster.

Sessions snapshot hook registrations at SessionStart, so sessions started
before the Python port (3.13.1) still invoke the old .sh paths. The shims
delegate to the .py ports through run_hook.sh, which fails OPEN when the
script or interpreter is missing instead of letting `python missing.py`
exit 2 (a hard PreToolUse deny).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"


def find_bash():
    """Locate the bash that hooks actually run under. On Windows that is
    git-bash (MSYS) — system32 bash.EXE is WSL and cannot read C:/ paths."""
    if os.name != "nt":
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parents[1]
        candidates += [root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"]
    candidates += [
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return None


BASH = find_bash()

pytestmark = pytest.mark.skipif(BASH is None, reason="git-bash not available")

LEGACY_SHIMS = {
    "merge-gate.sh": "merge_gate.py",
    "merge-recorder.sh": "merge_recorder.py",
    "worktree-submodule-init.sh": "worktree_submodule_init.py",
    "taskmaster-merge-approve.sh": "taskmaster_merge_approve.py",
}


def run_bash(args, stdin="", env_extra=None, cwd=None):
    env = os.environ.copy()
    env.update(env_extra or {})
    return subprocess.run(
        [BASH] + args,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
    )


def test_launcher_exists():
    assert (HOOKS_DIR / "run_hook.sh").is_file()


def test_legacy_shims_exist_and_name_their_port():
    for shim, port in LEGACY_SHIMS.items():
        path = HOOKS_DIR / shim
        assert path.is_file(), f"missing legacy shim {shim}"
        assert port in path.read_text(), f"{shim} does not delegate to {port}"
        assert (HOOKS_DIR / port).is_file()


def test_legacy_shims_allow_benign_input(tmp_path):
    env = {
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "TMPDIR": (tmp_path / "tmp").as_posix(),
    }
    (tmp_path / "tmp").mkdir()
    payloads = {
        "merge-gate.sh": '{"tool_input": {"command": "echo hi"}}',
        "merge-recorder.sh": '{"tool_input": {"command": "echo hi"}}',
        "worktree-submodule-init.sh": '{"tool_input": {"command": "echo hi"}}',
        "taskmaster-merge-approve.sh": '{"tool_input": {}, "tool_response": {}}',
    }
    for shim, payload in payloads.items():
        r = run_bash([(HOOKS_DIR / shim).as_posix()], stdin=payload,
                     env_extra=env, cwd=str(tmp_path))
        assert r.returncode == 0, f"{shim}: rc={r.returncode} stderr={r.stderr}"
