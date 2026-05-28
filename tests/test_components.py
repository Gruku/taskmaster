import json
import yaml
from backlog_server import backlog_add_epic, backlog_update_epic, _load

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
