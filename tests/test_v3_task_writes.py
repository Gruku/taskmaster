# User intent: the edit-in-UI task write semantics (id assignment, started /
# completed stamping, soft archive) must survive the move off the legacy
# taskmaster_v3 primitives onto the store-backed viewer helpers.
"""Viewer task-write semantics, now owned by `backlog_server._viewer_*`."""
import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import store


@pytest.fixture
def v2_backlog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    monkeypatch.setattr(bs, "CONFIG_PATH", tmp_path / ".taskmaster" / "missing.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", tmp_path / ".claude" / "missing.json")
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
    store.reset_for_tests()
    return bp


def _task(bp, task_id="e1-001"):
    """The committed task document, read back from a freshly opened store."""
    store.reset_for_tests()
    for epic in store.load_dict(bp).get("epics", []):
        for task in epic.get("tasks", []):
            if task.get("id") == task_id:
                return task
    raise AssertionError(f"task {task_id} not found in committed state")


def test_update_task_patches_existing_field(v2_backlog):
    bs._viewer_update_task("e1-001", {"title": "Renamed"})
    assert _task(v2_backlog)["title"] == "Renamed"


def test_update_task_unknown_id_raises(v2_backlog):
    with pytest.raises(KeyError):
        bs._viewer_update_task("nope", {"title": "x"})


def test_update_task_status_transition_stamps_started(v2_backlog):
    """When status moves to in-progress for the first time, started is set."""
    bs._viewer_update_task("e1-001", {"status": "in-progress"})
    task = _task(v2_backlog)
    assert task["status"] == "in-progress"
    started = task.get("started")
    assert started is not None
    assert len(started) >= 10  # at least YYYY-MM-DD


def test_update_task_status_transition_stamps_completed(v2_backlog):
    bs._viewer_update_task("e1-001", {"status": "done"})
    task = _task(v2_backlog)
    assert task["status"] == "done"
    assert task.get("completed") is not None


def test_update_task_does_not_overwrite_existing_started(v2_backlog):
    bs._viewer_update_task(
        "e1-001", {"status": "in-progress", "started": "2026-01-01"}
    )
    bs._viewer_update_task("e1-001", {"status": "in-review"})
    assert _task(v2_backlog)["started"] == "2026-01-01"


def test_create_task_assigns_id_under_epic(v2_backlog):
    new_id = bs._viewer_create_task({"title": "New", "epic": "e1", "priority": "low"})
    assert new_id == "e1-002"
    assert _task(v2_backlog, "e1-002")["title"] == "New"


def test_create_task_unknown_epic_raises(v2_backlog):
    with pytest.raises(KeyError):
        bs._viewer_create_task({"title": "x", "epic": "missing"})


def test_archive_task_moves_to_archived_status(v2_backlog):
    bs._viewer_archive_task("e1-001")
    assert _task(v2_backlog)["status"] == "archived"


def test_archive_task_archives_the_store_row(v2_backlog):
    """The status flip alone left the row live and the file in tasks/."""
    bs._viewer_archive_task("e1-001")
    store.reset_for_tests()
    row = store.open_store(v2_backlog).connection.execute(
        "SELECT archived FROM entities WHERE kind='task' AND id='e1-001'"
    ).fetchone()
    assert row["archived"] == 1
