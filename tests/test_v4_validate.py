from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from taskmaster import taskmaster_v3 as v3  # noqa: E402


def _orphan_project(tmp_path: Path) -> Path:
    tm = tmp_path / ".taskmaster"
    (tm / "tasks").mkdir(parents=True)
    (tm / "local").mkdir()
    (tm / "local" / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    (tm / "backlog.yaml").write_text(
        yaml.dump({
            "meta": {"schema_version": 4},
            "epics": [{"id": "e", "name": "E"}],
            "phases": [],
        }),
        encoding="utf-8",
    )
    fm, body = v3.task_v4_to_file({
        "id": "x-001",
        "title": "lost",
        "epic": "ghost",
        "order": 1.0,
    })
    v3.write_task_file(tm / "tasks" / "x-001.md", fm, body)
    return tm


def test_load_reports_orphan_epic(tmp_path):
    tm = _orphan_project(tmp_path)
    data = v3.load_v4(tm / "backlog.yaml")
    assert data["_orphan_tasks"] == ["x-001"]


def test_backlog_validate_surfaces_orphan(tmp_path, monkeypatch):
    from taskmaster import backlog_server

    tm = _orphan_project(tmp_path)
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    monkeypatch.setattr(backlog_server, "CONFIG_PATH", tm / "taskmaster.json")
    monkeypatch.setattr(
        backlog_server,
        "LEGACY_CONFIG_PATH",
        tmp_path / ".claude" / "taskmaster.json",
    )
    monkeypatch.chdir(tmp_path)
    output = backlog_server.backlog_validate()
    assert "x-001" in output
    assert "orphan" in output.lower() or "ghost" in output.lower()
