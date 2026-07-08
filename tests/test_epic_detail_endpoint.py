# plugins/taskmaster/tests/test_epic_detail_endpoint.py
import json
from taskmaster.backlog_server import (
    backlog_add_epic, backlog_add_task, backlog_update_task,
    backlog_update_epic, _load_epic_full,
    _load, _find_task, _mutate_and_save,
)


def _set_status(task_id, status):
    """Set status via the data layer — bypasses the Spec A transition table /
    done-gate for tasks created lane'd, where the test only cares about the
    final state, not the transition mechanics."""
    if status in ("in-progress", "todo"):
        backlog_update_task(task_id, "status", status)
        return
    data = _load()
    task, _ = _find_task(data, task_id)
    task["status"] = status
    _mutate_and_save(data)


def test_load_epic_full_unknown_returns_none(tmp_taskmaster):
    assert _load_epic_full("ghost") is None


def test_load_epic_full_merges_heavy_fields_and_rollup(tm_epic_phase):
    # heavy fields (description/components) live in epics/<id>.md and must come
    # back through load_v3 — the whole point of the endpoint.
    backlog_update_epic("test-epic", "design_status", "locked")
    backlog_update_epic("test-epic", "description", "Ingest + thumbnail + CDN.")
    backlog_update_epic("test-epic", "components",
                        json.dumps({"core": {"title": "Core", "after": []}}))
    for tid, status in [("E-1", "done"), ("E-2", "in-progress"), ("E-3", "todo")]:
        backlog_add_task(epic="test-epic", task_id=tid, title=tid, phase="dev")
        backlog_update_task(tid, "component", "core")
        _set_status(tid, status)

    out = _load_epic_full("test-epic")
    assert out["id"] == "test-epic"
    assert out["design_status"] == "locked"
    assert out["description"].startswith("Ingest")          # merged via load_v3
    assert out["components"]["core"]["title"] == "Core"
    assert out["stats"]["total"] == 3 and out["stats"]["done"] == 1
    assert out["component_rollup"]["core"]["total"] == 3
    assert {t["id"] for t in out["tasks"]} == {"E-1", "E-2", "E-3"}
    # tasks are slim — no heavy _body leaking into the list
    assert all("_body" not in t for t in out["tasks"])


def test_load_epic_full_attention_lists_blocked(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="B-1", title="blocked one", phase="dev")
    backlog_update_task("B-1", "status", "blocked")
    backlog_update_task("B-1", "blockers", "waiting on CDN creds")
    out = _load_epic_full("test-epic")
    assert any(a["id"] == "B-1" and a["blocked"] and "CDN creds" in a["why"]
               for a in out["attention"])


def test_load_epic_full_carries_done_when_and_area(tm_epic_phase):
    out = _load_epic_full("test-epic")
    assert out["done_when"] == "all test tasks complete"
    assert out["area"] is None


def test_load_epic_full_closeable_when_all_done(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="CL-1", title="one", phase="dev")
    _set_status("CL-1", "done")
    out = _load_epic_full("test-epic")
    assert out["closeable"] is True


def test_load_epic_full_not_closeable_with_open_tasks(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="OP-1", title="one", phase="dev")
    _set_status("OP-1", "done")
    backlog_add_task(epic="test-epic", task_id="OP-2", title="two", phase="dev")
    out = _load_epic_full("test-epic")
    assert out["closeable"] is False


def test_load_epic_full_zero_tasks_not_closeable(tm_epic_phase):
    out = _load_epic_full("test-epic")
    assert out["closeable"] is False


def test_load_epic_full_closeable_counts_archived_as_done(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="ACL-1", title="kept", phase="dev")
    _set_status("ACL-1", "done")
    backlog_add_task(epic="test-epic", task_id="ACL-2", title="closed", phase="dev")
    _set_status("ACL-2", "done")
    from taskmaster.backlog_server import backlog_archive_task
    backlog_archive_task("ACL-2", reason="done")
    out = _load_epic_full("test-epic")
    assert out["closeable"] is True
