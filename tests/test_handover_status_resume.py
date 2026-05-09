import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster_v3 import (
    mark_task_handovers_resumed,
    read_handover,
    update_handover_status,
    write_handover,
)


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_resume_flips_todo_to_in_progress(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="for T-1", session_kind="end-of-day", task_ids=["T-1"])

    mark_task_handovers_resumed(bp, "T-1")

    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "in-progress"
    assert "resumed" in fm["status_reason"].lower()


def test_resume_skips_done(tmp_path):
    """A done handover stays done — picking up the task should not reopen
    historical handovers."""
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="auto", session_kind="auto-stage", task_ids=["T-1"])
    mark_task_handovers_resumed(bp, "T-1")
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "done"


def test_resume_skips_secondary_task_id(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="primary T-1", session_kind="end-of-day",
                            task_ids=["T-1", "T-2"])
    mark_task_handovers_resumed(bp, "T-2")
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "todo"


def test_resume_respects_user_set(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="t", session_kind="end-of-day", task_ids=["T-1"])
    update_handover_status(bp, handover_id=hid, status="done", reason="dismissed")
    mark_task_handovers_resumed(bp, "T-1")
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "done"
