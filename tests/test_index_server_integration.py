# User intent: prove the MCP server keeps the derived index current on its own —
# refreshed inside _load(), reportable via backlog_index_status, buildable from the
# CLI, and never able to break a tool call when the index build fails.
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def _seed(bs, *, with_task: bool = True) -> None:
    """One epic (and optionally one task) written through the server's own API."""
    bs.backlog_add_epic(epic_id="e1", name="Epic one", done_when="e1 ships")
    bs.backlog_add_phase(phase_id="dev", name="Development")
    if with_task:
        bs.backlog_add_task(title="Task one", epic="e1", phase="dev", tldr="First task")


def test_load_refreshes_index(tmp_taskmaster):
    from taskmaster import backlog_server as bs

    _seed(bs)
    bs._load()
    assert (tmp_taskmaster / ".taskmaster" / "local" / "index.db").exists()


def test_index_status_reports_rows(tmp_taskmaster):
    from taskmaster import backlog_server as bs

    _seed(bs)
    out = bs.backlog_index_status()
    assert "Built:" in out
    assert "entities=" in out
    assert "index.db" in out


def test_index_status_rebuild(tmp_taskmaster):
    from taskmaster import backlog_server as bs

    _seed(bs, with_task=False)
    bs._load()
    out = bs.backlog_index_status(rebuild=True)
    assert "full rebuild: yes" in out
    assert "stale: no" in out


def test_load_survives_index_failure(tmp_taskmaster, monkeypatch):
    from taskmaster import backlog_server as bs
    from taskmaster import index

    _seed(bs)

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(index, "build_index", _boom)
    assert bs._load()["epics"]  # tool path unaffected
    log = tmp_taskmaster / ".taskmaster" / "local" / "index.log"
    assert "boom" in log.read_text(encoding="utf-8")


def test_index_log_is_capped(tmp_taskmaster, monkeypatch):
    from taskmaster import backlog_server as bs
    from taskmaster import index

    _seed(bs, with_task=False)
    log = tmp_taskmaster / ".taskmaster" / "local" / "index.log"
    log.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(index, "build_index", _boom)
    bs._load()
    text = log.read_text(encoding="utf-8")
    assert "boom" in text
    assert len(text) < 1024 * 1024


def test_build_index_cli_flag(tmp_taskmaster):
    from taskmaster import backlog_server as bs

    _seed(bs)
    proc = subprocess.run(
        [sys.executable, str(PLUGIN_ROOT / "backlog_server.py"), "--build-index",
         str(tmp_taskmaster / ".taskmaster" / "backlog.yaml")],
        capture_output=True, text=True,
        env={**os.environ, "TASKMASTER_ROOT": str(tmp_taskmaster)},
    )
    assert proc.returncode == 0, proc.stderr
    assert "row_counts" in proc.stdout
    assert (tmp_taskmaster / ".taskmaster" / "local" / "index.db").exists()


def test_build_index_cli_flag_without_path(tmp_taskmaster):
    """No positional after the flag: falls back to the resolved backlog path."""
    from taskmaster import backlog_server as bs

    _seed(bs, with_task=False)
    proc = subprocess.run(
        [sys.executable, str(PLUGIN_ROOT / "backlog_server.py"), "--build-index"],
        capture_output=True, text=True, cwd=str(tmp_taskmaster),
        env={**os.environ, "TASKMASTER_ROOT": str(tmp_taskmaster)},
    )
    assert proc.returncode == 0, proc.stderr
    assert "row_counts" in proc.stdout


def test_session_start_hook_warms_the_index():
    script = (PLUGIN_ROOT / "hooks" / "session-start.sh").read_text(encoding="utf-8")
    assert "--build-index" in script
    assert "command -v uv" in script
    # The warm must sit after the JSON block so a slow uv start cannot delay the hook.
    assert script.index("hookEventName") < script.index("--build-index")


@pytest.mark.parametrize("kind", ["exception", "clean"])
def test_index_status_builds_when_absent(tmp_taskmaster, kind):
    from taskmaster import backlog_server as bs
    from taskmaster import index

    _seed(bs, with_task=False)
    db = index.db_path(tmp_taskmaster / ".taskmaster" / "backlog.yaml")
    db.unlink(missing_ok=True)
    if kind == "exception":
        db.write_text("not a database", encoding="utf-8")
    out = bs.backlog_index_status()
    assert "Built:" in out and "entities=" in out
