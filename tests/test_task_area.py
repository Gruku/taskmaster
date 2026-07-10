from taskmaster import taskmaster_v3 as v3
from taskmaster.backlog_server import (
    backlog_add_task, backlog_update_task, backlog_batch_update,
    backlog_list_tasks, backlog_area_create, _load, _find_task,
)


def test_add_task_with_unknown_area_rejected(tm_epic_phase):
    out = backlog_add_task(title="Do a thing", epic="test-epic", phase="dev", options={"area": "ghost-area"})
    assert "Error" in out and "ghost-area" in out
    data = _load()
    assert not any(t.get("title") == "Do a thing" for e in data["epics"] for t in e.get("tasks", []))


def test_add_task_with_known_area_accepted(tm_epic_phase):
    backlog_area_create(area_id="desktop-app", name="Desktop App")
    out = backlog_add_task(title="Do a thing", epic="test-epic", phase="dev", options={"area": "desktop-app"})
    assert "Error" not in out
    data = _load()
    task, _ = _find_task(data, out.split("`")[1])
    assert task["area"] == "desktop-app"


def test_add_task_without_area_allowed(tm_epic_phase):
    out = backlog_add_task(title="Do a thing", epic="test-epic", phase="dev")
    assert "Error" not in out
    data = _load()
    task, _ = _find_task(data, out.split("`")[1])
    assert "area" not in task


def test_update_task_area_unknown_rejected(tm_epic_phase):
    backlog_area_create(area_id="desktop-app", name="Desktop App")
    out = backlog_add_task(title="Do a thing", epic="test-epic", phase="dev", options={"area": "desktop-app"})
    task_id = out.split("`")[1]
    result = backlog_update_task(task_id, "area", "ghost-area")
    assert "Error" in result and "ghost-area" in result
    data = _load()
    task, _ = _find_task(data, task_id)
    assert task["area"] == "desktop-app"


def test_update_task_area_known_accepted(tm_epic_phase):
    backlog_area_create(area_id="desktop-app", name="Desktop App")
    backlog_area_create(area_id="viewer", name="Viewer")
    out = backlog_add_task(title="Do a thing", epic="test-epic", phase="dev", options={"area": "desktop-app"})
    task_id = out.split("`")[1]
    result = backlog_update_task(task_id, "area", "viewer")
    assert "Error" not in result
    data = _load()
    task, _ = _find_task(data, task_id)
    assert task["area"] == "viewer"


def test_update_task_area_clear(tm_epic_phase):
    backlog_area_create(area_id="desktop-app", name="Desktop App")
    out = backlog_add_task(title="Do a thing", epic="test-epic", phase="dev", options={"area": "desktop-app"})
    task_id = out.split("`")[1]
    result = backlog_update_task(task_id, "area", "")
    assert "Error" not in result
    data = _load()
    task, _ = _find_task(data, task_id)
    assert "area" not in task


def test_batch_update_task_area_unknown_rejected(tm_epic_phase):
    out = backlog_add_task(title="Do a thing", epic="test-epic", phase="dev")
    task_id = out.split("`")[1]
    result = backlog_batch_update(f"update {task_id} area ghost-area")
    assert "ghost-area" in result
    data = _load()
    task, _ = _find_task(data, task_id)
    assert "area" not in task


def test_batch_update_task_area_known_accepted(tm_epic_phase):
    backlog_area_create(area_id="desktop-app", name="Desktop App")
    out = backlog_add_task(title="Do a thing", epic="test-epic", phase="dev")
    task_id = out.split("`")[1]
    result = backlog_batch_update(f"update {task_id} area desktop-app")
    assert "Error" not in result and "not allowed" not in result
    data = _load()
    task, _ = _find_task(data, task_id)
    assert task["area"] == "desktop-app"


def test_list_tasks_filters_by_area(tm_epic_phase):
    backlog_area_create(area_id="desktop-app", name="Desktop App")
    backlog_area_create(area_id="viewer", name="Viewer")
    out1 = backlog_add_task(title="Desktop task", epic="test-epic", phase="dev", options={"area": "desktop-app"})
    out2 = backlog_add_task(title="Viewer task", epic="test-epic", phase="dev", options={"area": "viewer"})
    tid1 = out1.split("`")[1]
    tid2 = out2.split("`")[1]

    result = backlog_list_tasks(area="desktop-app")
    assert tid1 in result
    assert tid2 not in result


def test_task_area_slim_roundtrip(tm_epic_phase):
    backlog_area_create(area_id="desktop-app", name="Desktop App")
    out = backlog_add_task(title="Do a thing", epic="test-epic", phase="dev", options={"area": "desktop-app"})
    task_id = out.split("`")[1]

    bp = tm_epic_phase / ".taskmaster" / "backlog.yaml"
    v3.save_v3(bp, v3.load_v3(bp))
    reloaded = v3.load_v3(bp)
    task, _ = _find_task(reloaded, task_id)
    assert task["area"] == "desktop-app"
