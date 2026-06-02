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


def pytest_configure(config):
    """Register custom markers (avoids PytestUnknownMarkWarning)."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (real git/bash subprocess)"
    )

# Make `import skill_budget_helper` work from tests that live in this directory.
TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))


@pytest.fixture()
def tmp_taskmaster(tmp_path, monkeypatch):
    """Create a minimal .taskmaster/ layout and redirect path resolution.

    Provides:
    - tmp_path/.taskmaster/backlog.yaml  (v3 schema with `meta.schema_version: 3`,
      empty epics/phases lists, `meta.updated` stub required by _save())
    - tmp_path/.taskmaster/PROGRESS.md   (stub with `## Changelog` header, required
      by regenerate_progress_dashboard() which reads it before rewriting)
    - tmp_path/.taskmaster/tasks/
    - tmp_path/.taskmaster/handovers/
    - tmp_path/.taskmaster/issues/
    - tmp_path/.taskmaster/lessons/
    - tmp_path/.taskmaster/ideas/

    Monkeypatches these backlog_server module attributes to point at tmp_path:
    - ROOT
    - CONFIG_PATH        (frozen at import time as ROOT / ".taskmaster" / "taskmaster.json")
    - LEGACY_CONFIG_PATH (frozen at import time as ROOT / ".claude" / "taskmaster.json")

    _resolve_paths() reads CONFIG_PATH and LEGACY_CONFIG_PATH directly — they
    are module-level constants captured at import time, not re-derived from
    ROOT on each call — so patching ROOT alone is insufficient for hermeticity.

    Returns the tmp_path (Path).
    """
    # Build directory structure
    tm_dir = tmp_path / ".taskmaster"
    for subdir in ("tasks", "handovers", "issues", "lessons", "ideas"):
        (tm_dir / subdir).mkdir(parents=True, exist_ok=True)

    # PROGRESS.md must exist so regenerate_progress_dashboard() can read it.
    (tm_dir / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")

    # Write a minimal v3 backlog.
    # meta.updated is required by _save(); meta.schema_version is the v3 marker
    # that _detect_schema_version reads to dispatch to load_v3/save_v3.
    backlog = {
        "version": 3,
        "project": "test-project",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [],
        "phases": [],
        "context": {},
    }
    (tm_dir / "backlog.yaml").write_text(yaml.dump(backlog), encoding="utf-8")

    # Redirect path resolution in backlog_server. Default raising=True so a
    # rename or removal of any of these attributes fails the fixture loudly
    # instead of silently no-op'ing.
    import backlog_server  # noqa: PLC0415 — imported here so sys.path is set first

    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    monkeypatch.setattr(
        backlog_server,
        "CONFIG_PATH",
        tmp_path / ".taskmaster" / "taskmaster.json",
    )
    monkeypatch.setattr(
        backlog_server,
        "LEGACY_CONFIG_PATH",
        tmp_path / ".claude" / "taskmaster.json",
    )

    # Tests that resolve relative paths against cwd should land in tmp_path —
    # keep this as a safety net for code paths that call Path.cwd() at runtime.
    monkeypatch.chdir(tmp_path)

    return tmp_path


@pytest.fixture()
def tm_epic_phase(tmp_taskmaster):
    """tmp_taskmaster + a `test-epic` epic and `dev` phase ready for task writes."""
    import backlog_server  # noqa: PLC0415
    backlog_server.backlog_add_epic(epic_id="test-epic", name="Test Epic")
    backlog_server.backlog_add_phase(phase_id="dev", name="Development")
    return tmp_taskmaster
