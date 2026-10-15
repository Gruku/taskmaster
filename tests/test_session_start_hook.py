# User intent: the SessionStart hook must tell the model the truth about every backlog layout the server serves,
# and stay cheap (no subprocess per directory level) because it runs synchronously on every session start.
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "session-start.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def _run(cwd: Path, **env) -> str:
    e = {k: v for k, v in os.environ.items() if k != "TASKMASTER_ROOT"}
    e.update(env)
    r = subprocess.run([BASH, str(HOOK)], cwd=cwd, env=e, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


def _deep(tmp_path: Path) -> Path:
    d = tmp_path / "a" / "b" / "c"
    d.mkdir(parents=True)
    return d


def test_no_backlog_points_to_init(tmp_path):
    ctx = _run(_deep(tmp_path))
    assert "nothing is tracked here yet" in ctx and "init-taskmaster" in ctx


def test_taskmaster_dir_up_the_tree_is_tracked(tmp_path):
    (tmp_path / ".taskmaster").mkdir()
    ctx = _run(_deep(tmp_path))
    assert "taskmaster:taskmaster skill" in ctx and "migrate-v3" not in ctx


@pytest.mark.parametrize("rel", [".claude/backlog.yaml", "backlog.yaml"])
def test_legacy_layout_gets_lane_text_and_migrate_pointer_never_init(tmp_path, rel):
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x: 1\n")
    ctx = _run(_deep(tmp_path))
    assert "taskmaster:taskmaster skill" in ctx
    assert "taskmaster:migrate-v3" in ctx
    assert "nothing is tracked" not in ctx
    assert "Do not run init-taskmaster" in ctx


def test_taskmaster_root_env_counts_as_tracked(tmp_path):
    ctx = _run(_deep(tmp_path), TASKMASTER_ROOT=str(tmp_path))
    assert "taskmaster:taskmaster skill" in ctx and "nothing is tracked" not in ctx


def test_walk_up_spawns_no_subprocess_per_level():
    text = HOOK.read_text(encoding="utf-8")
    assert "dirname" not in text and "$(" not in text.split("tracked=0", 1)[1].split("fi\n\nif", 1)[0]
