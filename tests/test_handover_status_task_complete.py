import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster_v3 import (
    mark_task_handovers_complete,
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
    hid, _ = write_handover(bp, tldr="for T-1", session_kind="end-of-day", task_ids=["T-1"])

    mark_task_handovers_complete(bp, "T-1")

    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "done"
    assert "T-1" in fm["status_reason"]


def test_complete_skips_non_primary_task_handovers(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="primary T-1, ref T-2", session_kind="end-of-day",
                            task_ids=["T-1", "T-2"])

    mark_task_handovers_complete(bp, "T-2")

    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "todo"


def test_complete_respects_user_set_lock(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="for T-1", session_kind="end-of-day", task_ids=["T-1"])
    update_handover_status(bp, handover_id=hid, status="in-progress")

    mark_task_handovers_complete(bp, "T-1")

    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "in-progress"


def test_complete_skips_already_done(tmp_path):
    """Already-done handovers (e.g. auto-stage) keep their status_changed
    untouched — flipping a 'done' handover to 'done' is a no-op."""
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="auto", session_kind="auto-stage", task_ids=["T-1"])
    fm_before, _ = read_handover(bp, hid)
    changed_before = fm_before["status_changed"]

    mark_task_handovers_complete(bp, "T-1")

    fm_after, _ = read_handover(bp, hid)
    assert fm_after["status_changed"] == changed_before
