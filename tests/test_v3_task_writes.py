# plugins/taskmaster/tests/test_v3_task_writes.py
import pytest
import yaml
from pathlib import Path


@pytest.fixture
def v2_backlog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({
        "meta": {"project": "test"},
        "epics": [
            {"id": "e1", "name": "E1", "status": "active",
             "tasks": [
                 {"id": "e1-001", "title": "Existing task", "status": "todo",
                  "priority": "medium", "depends_on": []},
             ]},
        ],
        "phases": [{"id": "p1", "name": "P1", "status": "active"}],
    }))
    return bp


def test_update_task_patches_existing_field(v2_backlog):
    from taskmaster.taskmaster_v3 import update_task
    update_task("e1-001", {"title": "Renamed"}, backlog_path=v2_backlog)
    data = yaml.safe_load(v2_backlog.read_text())
    assert data["epics"][0]["tasks"][0]["title"] == "Renamed"


def test_update_task_unknown_id_raises(v2_backlog):
    from taskmaster.taskmaster_v3 import update_task
    with pytest.raises(KeyError):
        update_task("nope", {"title": "x"}, backlog_path=v2_backlog)


def test_update_task_status_transition_stamps_started(v2_backlog):
    """When status moves to in-progress for the first time, started is set."""
    from taskmaster.taskmaster_v3 import update_task
    update_task("e1-001", {"status": "in-progress"}, backlog_path=v2_backlog)
    data = yaml.safe_load(v2_backlog.read_text())
    assert data["epics"][0]["tasks"][0]["status"] == "in-progress"
    started = data["epics"][0]["tasks"][0].get("started")
    assert started is not None
    assert len(started) >= 10  # at least YYYY-MM-DD


def test_update_task_status_transition_stamps_completed(v2_backlog):
    from taskmaster.taskmaster_v3 import update_task
    update_task("e1-001", {"status": "done"}, backlog_path=v2_backlog)
    data = yaml.safe_load(v2_backlog.read_text())
    assert data["epics"][0]["tasks"][0]["status"] == "done"
    assert data["epics"][0]["tasks"][0].get("completed") is not None


def test_update_task_does_not_overwrite_existing_started(v2_backlog):
    from taskmaster.taskmaster_v3 import update_task
    update_task("e1-001", {"status": "in-progress", "started": "2026-01-01"},
                backlog_path=v2_backlog)
    update_task("e1-001", {"status": "in-review"}, backlog_path=v2_backlog)
    data = yaml.safe_load(v2_backlog.read_text())
    assert data["epics"][0]["tasks"][0]["started"] == "2026-01-01"


def test_create_task_assigns_id_under_epic(v2_backlog):
    from taskmaster.taskmaster_v3 import create_task
    new_id = create_task({"title": "New", "epic": "e1", "priority": "low"},
                          backlog_path=v2_backlog)
    assert new_id == "e1-002"
    data = yaml.safe_load(v2_backlog.read_text())
    titles = [t["title"] for t in data["epics"][0]["tasks"]]
    assert "New" in titles


def test_create_task_unknown_epic_raises(v2_backlog):
    from taskmaster.taskmaster_v3 import create_task
    with pytest.raises(KeyError):
        create_task({"title": "x", "epic": "missing"}, backlog_path=v2_backlog)


def test_archive_task_moves_to_archived_status(v2_backlog):
    from taskmaster.taskmaster_v3 import archive_task
    archive_task("e1-001", backlog_path=v2_backlog)
    data = yaml.safe_load(v2_backlog.read_text())
    assert data["epics"][0]["tasks"][0]["status"] == "archived"
