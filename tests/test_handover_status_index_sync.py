"""End-to-end regression: status survives a full sync_handover_index round-trip
across the supported transitions (default open, closed, superseded, manual override,
smart-close)."""
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster_v3 import (
    apply_supersession,
    smart_auto_close_handovers,
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
    a, _ = write_handover(bp, tldr="default open", session_kind="end-of-day")
    b, _ = write_handover(bp, tldr="auto closed", session_kind="auto-stage")
    c, _ = write_handover(bp, tldr="manual override to closed", session_kind="end-of-day")
    update_handover_status(bp, handover_id=c, status="closed")
    d, _ = write_handover(bp, tldr="task linked, smart-closed", session_kind="task-complete",
                          task_ids=["T-9"])
    smart_auto_close_handovers(bp, triggering_task_id="T-9", done_or_archived_ids={"T-9"})
    f, _ = write_handover(bp, tldr="will be superseded", session_kind="milestone-complete")
    g, _ = write_handover(bp, tldr="successor", session_kind="milestone-complete")
    apply_supersession(bp, old_id=f, new_id=g)

    data = {"handovers": []}
    sync_handover_index(data, bp)
    by_id = {entry["id"]: entry for entry in data["handovers"]}
    assert by_id[a]["status"] == "open"
    assert by_id[b]["status"] == "closed"
    assert by_id[c]["status"] == "closed"
    assert by_id[d]["status"] == "closed"
    assert by_id[f]["status"] == "superseded"
    assert by_id[g]["status"] == "open"
