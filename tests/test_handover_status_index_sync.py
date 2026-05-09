"""End-to-end regression: status survives a full sync_handover_index round-trip
across the seven supported transitions (default, manual, supersession,
task-complete, resume, backfill, override)."""
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster_v3 import (
    apply_supersession,
    mark_task_handovers_complete,
    mark_task_handovers_resumed,
    sync_handover_index,
    update_handover_status,
    write_handover,
)


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_index_carries_all_status_transitions(tmp_path):
    bp = _setup(tmp_path)
    a, _ = write_handover(bp, tldr="default todo", session_kind="end-of-day")
    b, _ = write_handover(bp, tldr="auto done", session_kind="auto-stage")
    c, _ = write_handover(bp, tldr="manual override", session_kind="end-of-day")
    update_handover_status(bp, handover_id=c, status="in-progress")
    d, _ = write_handover(bp, tldr="task linked", session_kind="end-of-day", task_ids=["T-9"])
    mark_task_handovers_resumed(bp, "T-9")
    e, _ = write_handover(bp, tldr="task linked done", session_kind="end-of-day", task_ids=["T-10"])
    mark_task_handovers_complete(bp, "T-10")
    f, _ = write_handover(bp, tldr="will be superseded", session_kind="milestone-complete")
    g, _ = write_handover(bp, tldr="successor", session_kind="milestone-complete")
    apply_supersession(bp, old_id=f, new_id=g)

    data = {"handovers": []}
    sync_handover_index(data, bp)
    by_id = {entry["id"]: entry for entry in data["handovers"]}
    assert by_id[a]["status"] == "todo"
    assert by_id[b]["status"] == "done"
    assert by_id[c]["status"] == "in-progress"
    assert by_id[d]["status"] == "in-progress"
    assert by_id[e]["status"] == "done"
    assert by_id[f]["status"] == "done"
    assert by_id[g]["status"] == "todo"
