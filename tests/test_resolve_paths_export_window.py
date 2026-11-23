# User intent: pin B-111 - a tool call that lands while the native exporter has backlog.yaml moved aside must still
# resolve to the project's .taskmaster layout, never fall through to the legacy root layout (the Linux sweep flake).
from __future__ import annotations

import pytest

from taskmaster import backlog_server as bs


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    monkeypatch.setattr(bs, "CONFIG_PATH", tmp_path / ".taskmaster" / "taskmaster.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", tmp_path / ".claude" / "taskmaster.json")
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    (tmp_path / ".taskmaster" / "local" / "store.db").write_bytes(b"")
    return tmp_path


def test_a_store_without_its_projection_file_still_resolves_under_taskmaster(project):
    backlog, progress = bs._resolve_paths()
    assert backlog == project / ".taskmaster" / "backlog.yaml"
    assert progress == project / ".taskmaster" / "PROGRESS.md"


def test_the_shared_layout_probe_agrees_for_the_migration_scan(project):
    from taskmaster import taskmaster_v3 as v3
    assert v3.canonical_layout_present(project)
    assert [name for name, _ in v3._detect_layout_sources(project)] == ["canonical"]
    (project / ".taskmaster" / "local" / "store.db").unlink()
    assert not v3.canonical_layout_present(project) and v3._detect_layout_sources(project) == []


def test_a_project_with_no_store_and_no_backlog_keeps_the_legacy_root_answer(project):
    (project / ".taskmaster" / "local" / "store.db").unlink()
    assert bs._resolve_paths()[0] == project / "backlog.yaml"
