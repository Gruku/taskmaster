"""Server-level v4 dispatch: mutate through the MCP server, land in task files."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture()
def v4_project(tmp_path, monkeypatch):
    tm = tmp_path / ".taskmaster"
    (tm / "tasks").mkdir(parents=True)
    (tm / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    backlog = {"meta": {"project": "t", "updated": "", "schema_version": 4},
               "epics": [{"id": "e", "name": "E", "status": "active", "tasks": [
                   {"id": "e-001", "title": "First", "epic": "e", "order": 1.0,
                    "status": "todo", "priority": "medium"},
               ]}],
               "phases": [{"id": "p1", "name": "P1", "status": "active"}]}
    (tm / "backlog.yaml").write_text(yaml.dump(backlog), encoding="utf-8")
    fm, body = _mk_task_file(
        {"id": "e-001", "title": "First", "epic": "e", "order": 1.0,
         "status": "todo", "priority": "medium"})
    _write(tm / "tasks" / "e-001.md", fm, body)
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    monkeypatch.setattr(backlog_server, "CONFIG_PATH", tmp_path / ".taskmaster" / "taskmaster.json")
    monkeypatch.setattr(backlog_server, "LEGACY_CONFIG_PATH", tmp_path / ".claude" / "taskmaster.json")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _mk_task_file(task):
    from taskmaster import taskmaster_v3 as v3
    return v3.task_v4_to_file(task)


def _write(path, fm, body):
    from taskmaster import taskmaster_v3 as v3
    v3.write_task_file(path, fm, body)


def test_load_returns_globbed_tasks(v4_project):
    from taskmaster import backlog_server
    data = backlog_server._load()
    assert data["epics"][0]["tasks"][0]["id"] == "e-001"


def test_update_task_writes_to_task_file_not_backlog(v4_project):
    from taskmaster import backlog_server
    from taskmaster.taskmaster_v3 import update_task
    update_task("e-001", {"title": "Renamed"}, backlog_path=backlog_server._backlog_path())
    # task file has the new title
    fm, _ = _read(v4_project / ".taskmaster" / "tasks" / "e-001.md")
    assert fm["title"] == "Renamed"
    # backlog.yaml still carries no task list
    on_disk = yaml.safe_load((v4_project / ".taskmaster" / "backlog.yaml").read_text())
    assert "tasks" not in on_disk["epics"][0]


def test_load_snapshot_is_deep_copy(v4_project):
    from taskmaster import backlog_server
    data = backlog_server._load()
    data["epics"][0]["tasks"][0]["title"] = "mutated in memory"
    assert backlog_server._LOAD_SNAPSHOT["epics"][0]["tasks"][0]["title"] == "First"


def _read(path):
    from taskmaster import taskmaster_v3 as v3
    return v3.read_task_file(path)


def test_add_task_allocates_id_and_order(v4_project):
    from taskmaster import backlog_server
    out = backlog_server.backlog_add_task(
        epic="e", title="Second", phase="p1", priority="medium")
    assert "e-002" in out
    fm, _ = _read(v4_project / ".taskmaster" / "tasks" / "e-002.md")
    assert fm["epic"] == "e"
    assert fm["order"] == 2.0


class TestLocalRelocation:
    def test_progress_written_under_local(self, v4_project):
        from taskmaster import backlog_server
        data = backlog_server._load()
        backlog_server._mutate_and_save(data)
        assert (v4_project / ".taskmaster" / "local" / "PROGRESS.md").exists()

    def test_viewer_prefs_under_local(self, v4_project):
        from taskmaster import taskmaster_v3 as v3
        assert v3.viewer_prefs_path().parent.name == "local"

    def test_meta_updated_cached_locally(self, v4_project):
        import json
        from taskmaster import backlog_server
        data = backlog_server._load()
        backlog_server._save(data)
        cache = v4_project / ".taskmaster" / "local" / "cache" / "meta.json"
        assert cache.exists()
        assert "updated" in json.loads(cache.read_text(encoding="utf-8"))
