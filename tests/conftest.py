# plugins/taskmaster/tests/conftest.py
"""Shared pytest fixtures for taskmaster tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

# Make `import backlog_server` and `from taskmaster_v3 import ...` work
# exactly the same way the existing hermetic tests do.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


@pytest.fixture()
def tmp_taskmaster(tmp_path, monkeypatch):
    """Create a minimal .taskmaster/ layout and redirect all path resolution.

    Provides:
    - tmp_path/.taskmaster/backlog.yaml  (v3 schema, one epic "e" with zero tasks)
    - tmp_path/.taskmaster/tasks/
    - tmp_path/.taskmaster/handovers/
    - tmp_path/.taskmaster/issues/
    - tmp_path/.taskmaster/lessons/
    - tmp_path/.taskmaster/ideas/

    All backlog_server path helpers (ROOT, _backlog_path, _resolve_paths) are
    monkeypatched to point at tmp_path so tests are fully hermetic.

    Returns the tmp_path (Path).
    """
    # Build directory structure
    tm_dir = tmp_path / ".taskmaster"
    for subdir in ("tasks", "handovers", "issues", "lessons", "ideas"):
        (tm_dir / subdir).mkdir(parents=True, exist_ok=True)

    # Write a minimal v3 backlog
    backlog = {
        "version": 3,
        "project": "test-project",
        "epics": [],
    }
    (tm_dir / "backlog.yaml").write_text(yaml.dump(backlog), encoding="utf-8")

    # Redirect path resolution in backlog_server
    import backlog_server  # noqa: PLC0415 — imported here so sys.path is set first

    monkeypatch.setattr(backlog_server, "ROOT", tmp_path, raising=False)

    # _resolve_paths() uses CWD or ROOT; patch CWD as a belt-and-suspenders fallback
    monkeypatch.chdir(tmp_path)

    return tmp_path
