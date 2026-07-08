import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster.taskmaster_v3 import (
    smart_auto_close_handovers,
    read_handover,
    update_handover_status,
    write_handover,
)


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_complete_flips_primary_task_handovers(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="for T-1", session_kind="task-complete", task_ids=["T-1"])

    result = smart_auto_close_handovers(bp, triggering_task_id="T-1", done_or_archived_ids={"T-1"})

    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "closed"
    assert "T-1" in fm["status_reason"]


def test_complete_skips_non_included_task_handovers(tmp_path):
    """Handovers that don't include T-2 in task_ids are not touched."""
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="primary T-1", session_kind="task-complete",
                            task_ids=["T-1"])

    result = smart_auto_close_handovers(bp, triggering_task_id="T-2", done_or_archived_ids={"T-2"})

    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "open"


def test_complete_respects_user_set_lock(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="for T-1", session_kind="task-complete", task_ids=["T-1"])
    update_handover_status(bp, handover_id=hid, status="open")

    # Manually mark status_user_set to simulate user locking
    from taskmaster.taskmaster_v3 import read_handover as _rh, write_task_file, handover_path
    fm, body = _rh(bp, hid)
    fm["status_user_set"] = True
    write_task_file(handover_path(bp, hid), fm, body)

    smart_auto_close_handovers(bp, triggering_task_id="T-1", done_or_archived_ids={"T-1"})

    fm_after, _ = read_handover(bp, hid)
    assert fm_after["status"] == "open"


def test_complete_skips_already_closed(tmp_path):
    """Already-closed handovers (e.g. auto-stage) keep their status_changed
    untouched — smart-close skips non-open handovers."""
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="auto", session_kind="auto-stage", task_ids=["T-1"])
    fm_before, _ = read_handover(bp, hid)
    changed_before = fm_before["status_changed"]

    smart_auto_close_handovers(bp, triggering_task_id="T-1", done_or_archived_ids={"T-1"})

    fm_after, _ = read_handover(bp, hid)
    assert fm_after["status_changed"] == changed_before
