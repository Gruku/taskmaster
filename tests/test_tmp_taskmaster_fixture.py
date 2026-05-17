# plugins/taskmaster/tests/test_tmp_taskmaster_fixture.py
"""Verify the tmp_taskmaster fixture creates the expected layout."""
from pathlib import Path

import yaml


def test_fixture_creates_directory_structure(tmp_taskmaster):
    base = Path(tmp_taskmaster) / ".taskmaster"
    assert base.is_dir()
    for subdir in ("tasks", "handovers", "issues", "lessons", "ideas"):
        assert (base / subdir).is_dir(), f"Missing .taskmaster/{subdir}/"
    bl = base / "backlog.yaml"
    assert bl.exists()
    data = yaml.safe_load(bl.read_text())
    assert data["version"] == 3


def test_fixture_enables_backlog_server_import(tmp_taskmaster):
    """conftest sys.path setup must allow bare `import backlog_server`."""
    import backlog_server  # noqa: F401 — import success is the test
    assert backlog_server.ROOT == Path(tmp_taskmaster)
