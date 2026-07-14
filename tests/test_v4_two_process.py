"""Two interleaved load/save cycles on one machine must not clobber."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture()
def v4_two_task_project(tmp_path, monkeypatch):
    from taskmaster import backlog_server, taskmaster_v3 as v3

    tm = tmp_path / ".taskmaster"
    (tm / "tasks").mkdir(parents=True)
    (tm / "local").mkdir()
    (tm / "local" / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    backlog = {
        "meta": {"project": "t", "schema_version": 4},
        "epics": [{"id": "e", "name": "E", "status": "active"}],
        "phases": [{"id": "p1", "name": "P1", "status": "active"}],
    }
    (tm / "backlog.yaml").write_text(yaml.dump(backlog), encoding="utf-8")
    for number in (1, 2):
        task = {
            "id": f"e-00{number}",
            "title": f"T{number}",
            "epic": "e",
            "order": float(number),
            "status": "todo",
            "priority": "medium",
        }
        frontmatter, body = v3.task_v4_to_file(task)
        v3.write_task_file(tm / "tasks" / f"e-00{number}.md", frontmatter, body)
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    monkeypatch.setattr(backlog_server, "CONFIG_PATH", tm / "taskmaster.json")
    monkeypatch.setattr(
        backlog_server,
        "LEGACY_CONFIG_PATH",
        tmp_path / ".claude" / "taskmaster.json",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_interleaved_saves_no_clobber(v4_two_task_project):
    from taskmaster import backlog_server

    data_a = backlog_server._load()
    snapshot_a = copy.deepcopy(backlog_server._LOAD_SNAPSHOT)

    data_b = backlog_server._load()
    snapshot_b = copy.deepcopy(backlog_server._LOAD_SNAPSHOT)
    backlog_server._find_task(data_b, "e-002")[0]["title"] = "B-edited-2"
    backlog_server._LOAD_SNAPSHOT = snapshot_b
    backlog_server._save(data_b)

    backlog_server._find_task(data_a, "e-001")[0]["title"] = "A-edited-1"
    backlog_server._LOAD_SNAPSHOT = snapshot_a
    backlog_server._save(data_a)

    final = backlog_server._load()
    titles = {
        task["id"]: task["title"]
        for epic in final["epics"]
        for task in epic["tasks"]
    }
    assert titles["e-001"] == "A-edited-1"
    assert titles["e-002"] == "B-edited-2"
