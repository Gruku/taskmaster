import sys
from pathlib import Path
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

# Tests call the data layer directly to avoid the MCP tool's backlog-path lookup.
from taskmaster_v3 import (
    list_handover_ids,
    read_handover,
    update_handover_status,
    write_handover,
)
from taskmaster_v3 import HANDOVER_STATUSES


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_handover_statuses_used_in_list_filter_are_new_enum(tmp_path):
    """Verify the enum is new before testing filters."""
    assert "open" in HANDOVER_STATUSES
    assert "closed" in HANDOVER_STATUSES


def test_list_filter_status_open_returns_only_open(tmp_path):
    bp = _setup(tmp_path)
    open_id, _ = write_handover(bp, tldr="open one", session_kind="end-of-day")
    closed_id, _ = write_handover(bp, tldr="closed one", session_kind="end-of-day")
    update_handover_status(bp, handover_id=closed_id, status="closed")

    ids = list_handover_ids(bp)
    open_ids = [
        hid for hid in ids
        if read_handover(bp, hid)[0].get("status") == "open"
    ]
    assert open_id in open_ids
    assert closed_id not in open_ids


def test_list_filter_status_closed_returns_only_closed(tmp_path):
    bp = _setup(tmp_path)
    open_id, _ = write_handover(bp, tldr="open one", session_kind="end-of-day")
    closed_id, _ = write_handover(bp, tldr="closed one", session_kind="end-of-day")
    update_handover_status(bp, handover_id=closed_id, status="closed")

    ids = list_handover_ids(bp)
    closed_ids = [
        hid for hid in ids
        if read_handover(bp, hid)[0].get("status") == "closed"
    ]
    assert closed_id in closed_ids
    assert open_id not in closed_ids


def test_flag_reason_present_in_frontmatter_after_flag(tmp_path):
    from taskmaster_v3 import smart_auto_close_handovers
    bp = _setup(tmp_path)
    hid, _ = write_handover(
        bp,
        tldr="context handoff",
        session_kind="context-handoff",
        task_ids=["T-1"],
    )
    smart_auto_close_handovers(
        bp, triggering_task_id="T-1", done_or_archived_ids={"T-1"}
    )
    fm, _ = read_handover(bp, hid)
    assert fm.get("flag_reason"), "flag_reason should be set for flagged handovers"
    assert "context" in fm["flag_reason"].lower() or "session_kind" in fm["flag_reason"].lower()
