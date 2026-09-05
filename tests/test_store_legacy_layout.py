# User intent: a project still on the legacy `.claude/` or root layout must be refused
# with an actionable message instead of silently opening an empty store beside its real
# backlog, and `backlog_canonicalize_layout` must move every artifact directory so the
# same tasks are visible afterwards.
"""Legacy-layout refusal and the one supported migration out of it (fix wave F1)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import store


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _legacy_project(root: Path, layout: str = ".claude") -> Path:
    """A v4 backlog sitting in the legacy location, with one real task file."""
    directory = root / layout if layout else root
    document = {
        "version": 4,
        "project": "legacy-layout",
        "meta": {"project": "legacy-layout", "schema_version": 4},
        "epics": [{"id": "core", "name": "Core", "status": "active", "done_when": "n/a"}],
        "phases": [{"id": "dev", "name": "Development", "status": "active"}],
    }
    _write(directory / "backlog.yaml", yaml.safe_dump(document, sort_keys=False))
    _write(directory / "PROGRESS.md", "## Changelog\n")
    _write(
        directory / "tasks" / "core-001.md",
        "---\nid: core-001\ntitle: Legacy task\nstatus: todo\nepic: core\n"
        "phase: dev\npriority: medium\norder: 1.0\n---\n",
    )
    return directory


@pytest.fixture()
def legacy_root(tmp_path, monkeypatch):
    root = tmp_path / "project"
    _legacy_project(root)
    monkeypatch.setattr(bs, "ROOT", root)
    monkeypatch.setattr(bs, "CONFIG_PATH", root / ".taskmaster" / "taskmaster.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", root / ".claude" / "missing.json")
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    return root


def test_open_store_refuses_a_claude_layout_backlog(tmp_path):
    root = tmp_path / "project"
    _legacy_project(root)

    with pytest.raises(store.LegacyLayoutError) as exc:
        store.open_store(backlog_path=root / ".claude" / "backlog.yaml")

    assert "backlog_canonicalize_layout" in str(exc.value)
    assert not (root / ".taskmaster").exists()


def test_open_store_refuses_a_root_layout_backlog(tmp_path):
    root = tmp_path / "project"
    _legacy_project(root, layout="")

    with pytest.raises(store.LegacyLayoutError):
        store.open_store(backlog_path=root / "backlog.yaml")

    assert not (root / ".taskmaster" / "local" / "store.db").exists()


def test_server_tools_refuse_a_legacy_layout_and_create_nothing(legacy_root):
    result = bs.backlog_list_tasks()

    assert result.startswith("Error"), result
    assert "backlog_canonicalize_layout" in result
    assert not (legacy_root / ".taskmaster").exists(), (
        "a legacy-layout project must never bootstrap a second, empty backlog"
    )


@pytest.mark.allow_projection_bypass
def test_canonicalize_then_the_same_tasks_are_visible(legacy_root):
    summary = bs.backlog_canonicalize_layout()
    assert "Canonicalized" in summary, summary

    result = bs.backlog_list_tasks()
    assert "core-001" in result, result
    assert "Legacy task" in result


@pytest.mark.allow_projection_bypass
def test_canonicalize_moves_epic_and_phase_files(tmp_path):
    root = tmp_path / "project"
    _legacy_project(root)
    _write(root / ".claude" / "epics" / "core.md", "---\nid: core\n---\n## Notes\nepic body\n")
    _write(root / ".claude" / "phases" / "dev.md", "---\nid: dev\n---\n## Notes\nphase body\n")
    _write(root / ".claude" / "bugs" / "B-001.md", "---\nid: B-001\n---\nbug body\n")
    _write(root / ".claude" / "decisions" / "DEC-001.md", "---\nid: DEC-001\n---\nwhy\n")

    from taskmaster.taskmaster_v3 import canonicalize_layout

    summary = canonicalize_layout(root)

    assert summary["status"] == "migrated", summary
    for relative in (
        "epics/core.md",
        "phases/dev.md",
        "bugs/B-001.md",
        "decisions/DEC-001.md",
    ):
        assert (root / ".taskmaster" / relative).exists(), relative
        assert not (root / ".claude" / relative).exists(), relative


def test_canonicalize_refuses_on_network_storage(legacy_root, monkeypatch):
    monkeypatch.setattr(
        store, "_network_filesystem_reason", lambda path: "network filesystem (smb)"
    )

    result = bs.backlog_canonicalize_layout()

    assert result.startswith("Error"), result
    assert "network filesystem" in result
    assert (legacy_root / ".claude" / "backlog.yaml").exists()
    assert not (legacy_root / ".taskmaster" / "backlog.yaml").exists()
