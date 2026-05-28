import json
import yaml
from backlog_server import backlog_add_epic, backlog_update_epic, _load, backlog_add_task, backlog_update_task, backlog_get_task, _component_rollup

def _epic(data, eid):
    return next(e for e in data["epics"] if e["id"] == eid)

def test_set_components_block(tmp_taskmaster):
    backlog_add_epic("asset-engine", "Asset Engine")
    val = json.dumps({"ingest": {"title": "Ingest", "after": []},
                      "thumb": {"title": "Thumbnailer", "after": ["ingest"]}})
    out = backlog_update_epic("asset-engine", "components", val)
    assert "Error" not in out
    data = _load()
    comps = _epic(data, "asset-engine")["components"]
    assert comps["thumb"]["after"] == ["ingest"]

def test_components_reject_unknown_after(tmp_taskmaster):
    backlog_add_epic("asset-engine", "Asset Engine")
    val = json.dumps({"thumb": {"title": "T", "after": ["nope"]}})
    out = backlog_update_epic("asset-engine", "components", val)
    assert "Error" in out and "nope" in out

def test_design_status_field(tmp_taskmaster):
    backlog_add_epic("asset-engine", "Asset Engine")
    assert "Error" not in backlog_update_epic("asset-engine", "design_status", "locked")
    assert _epic(_load(), "asset-engine")["design_status"] == "locked"
    assert "Error" in backlog_update_epic("asset-engine", "design_status", "bogus")


def test_bind_task_to_component(tm_epic_phase):
    # tm_epic_phase pre-creates epic "test-epic" + phase "dev"
    val = json.dumps({"core": {"title": "Core", "after": []}})
    backlog_update_epic("test-epic", "components", val)
    backlog_add_task(epic="test-epic", task_id="T-1", title="X", phase="dev")
    assert "Error" not in backlog_update_task("T-1", "component", "core")
    data = _load()
    t = next(t for e in data["epics"] for t in e.get("tasks", []) if t["id"] == "T-1")
    assert t["component"] == "core"

def test_bind_unknown_component_rejected(tm_epic_phase):
    backlog_update_epic("test-epic", "components", json.dumps({"core": {"title": "Core"}}))
    backlog_add_task(epic="test-epic", task_id="T-2", title="Y", phase="dev")
    out = backlog_update_task("T-2", "component", "ghost")
    assert "Error" in out and "ghost" in out

def test_clear_component(tm_epic_phase):
    backlog_update_epic("test-epic", "components", json.dumps({"core": {"title": "Core"}}))
    backlog_add_task(epic="test-epic", task_id="T-3", title="Z", phase="dev")
    backlog_update_task("T-3", "component", "core")
    assert "Error" not in backlog_update_task("T-3", "component", "")
    t = next(t for e in _load()["epics"] for t in e.get("tasks", []) if t["id"] == "T-3")
    assert "component" not in t


def test_component_rollup(tm_epic_phase):
    backlog_update_epic("test-epic", "components",
                        json.dumps({"core": {"title": "Core"}, "ui": {"title": "UI"}}))
    for tid, comp, status in [("R-1", "core", "done"), ("R-2", "core", "in-progress"),
                              ("R-3", "ui", "todo"), ("R-4", None, "todo")]:
        backlog_add_task(epic="test-epic", task_id=tid, title=tid, phase="dev")
        if comp:
            backlog_update_task(tid, "component", comp)
        backlog_update_task(tid, "status", status)
    roll = _component_rollup(_load(), "test-epic")
    assert roll["core"]["total"] == 2 and roll["core"]["done"] == 1
    assert roll["core"]["status"] == "in-progress"   # mixed -> in-progress
    assert roll["ui"]["status"] == "todo"            # nothing started
    assert roll["_unassigned"]["total"] == 1         # R-4 has no component
