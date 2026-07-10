"""Task bundle: structured predictive blast-radius for detection-fallback."""
import json
from taskmaster import backlog_server


def test_structured_predictive_returns_overlap_list(tm_epic_phase):
    backlog_server.backlog_add_task(title="a", epic="test-epic", phase="dev", options={"task_id": "t-1", "anchors": "plugins/x/foo.py"})
    backlog_server.backlog_add_task(title="b", epic="test-epic", phase="dev", options={"task_id": "t-2", "anchors": "plugins/x/foo.py"})
    out = backlog_server.backlog_blast_radius("t-1", mode="predictive", structured=True)
    data = out if isinstance(out, (list, dict)) else json.loads(out)
    overlaps = data["overlapping_tasks"] if isinstance(data, dict) else data
    ids = {o["task_id"] for o in overlaps}
    assert "t-2" in ids
    assert all({"task_id", "status", "shared_paths"} <= set(o) for o in overlaps)
