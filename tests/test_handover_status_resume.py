"""Under the new model, open handovers stay open when a task is picked
(no resumed transition). This file verifies the open state is correct
and that mark_task_handovers_resumed is gone."""
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster_v3 import (
    read_handover,
    update_handover_status,
    write_handover,
    HANDOVER_STATUSES,
)


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_resumed_function_removed():
    """mark_task_handovers_resumed must not exist — pick-task no longer needs it."""
    import taskmaster_v3 as m
    assert not hasattr(m, "mark_task_handovers_resumed"), (
        "mark_task_handovers_resumed must be removed under the new open/closed/superseded enum"
    )


def test_open_handover_stays_open_on_task_pick(tmp_path):
    """Under the new model, open handovers are visible in start-session glance
    without any status transition — they stay open until smart-closed or manually closed."""
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="for T-1", session_kind="end-of-day", task_ids=["T-1"])

    # Simulate pick-task: no transition call — open handovers stay open.
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "open"
    assert fm["status"] in HANDOVER_STATUSES


def test_closed_handover_skipped_by_smart_close(tmp_path):
    """Already-closed handovers (e.g. auto-stage) are not touched by smart-close."""
    from taskmaster_v3 import smart_auto_close_handovers
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="auto", session_kind="auto-stage", task_ids=["T-1"])
    fm_before, _ = read_handover(bp, hid)
    changed_before = fm_before["status_changed"]
    assert fm_before["status"] == "closed"  # auto-stage born closed

    # smart-close skips already-closed handovers
    result = smart_auto_close_handovers(bp, triggering_task_id="T-1", done_or_archived_ids={"T-1"})
    assert hid not in result["closed"]
    assert hid not in result["flagged"]

    fm_after, _ = read_handover(bp, hid)
    assert fm_after["status_changed"] == changed_before


def test_user_set_handover_not_mutated_by_smart_close(tmp_path):
    from taskmaster_v3 import smart_auto_close_handovers
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="t", session_kind="end-of-day", task_ids=["T-1"])
    update_handover_status(bp, handover_id=hid, status="open", reason="dismissed")
    fm_before, _ = read_handover(bp, hid)
    # status_user_set=True from update_handover_status; smart-close should skip it
    assert fm_before["status_user_set"] is True

    result = smart_auto_close_handovers(bp, triggering_task_id="T-1", done_or_archived_ids={"T-1"})
    assert hid not in result["closed"]
    assert hid not in result["flagged"]
    fm_after, _ = read_handover(bp, hid)
    assert fm_after["status"] == "open"
