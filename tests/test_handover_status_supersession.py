import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster_v3 import (
    apply_supersession,
    read_handover,
    update_handover_status,
    write_handover,
)


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_supersession_flips_old_to_done(tmp_path):
    bp = _setup(tmp_path)
    old_id, _ = write_handover(bp, tldr="old", session_kind="milestone-complete")
    new_id, _ = write_handover(bp, tldr="new", session_kind="milestone-complete")

    apply_supersession(bp, old_id=old_id, new_id=new_id)

    fm_old, _ = read_handover(bp, old_id)
    assert fm_old["status"] == "done"
    assert "superseded by" in fm_old["status_reason"].lower()
    assert fm_old["status_user_set"] is False


def test_supersession_respects_user_set_lock(tmp_path):
    bp = _setup(tmp_path)
    old_id, _ = write_handover(bp, tldr="old", session_kind="milestone-complete")
    new_id, _ = write_handover(bp, tldr="new", session_kind="milestone-complete")

    update_handover_status(bp, handover_id=old_id, status="in-progress", reason="still using")

    apply_supersession(bp, old_id=old_id, new_id=new_id)

    fm_old, _ = read_handover(bp, old_id)
    assert fm_old["status"] == "in-progress"
    assert fm_old["status_user_set"] is True


def test_supersession_idempotent_double_call(tmp_path):
    bp = _setup(tmp_path)
    old_id, _ = write_handover(bp, tldr="old", session_kind="milestone-complete")
    mid_id, _ = write_handover(bp, tldr="mid", session_kind="milestone-complete")
    new_id, _ = write_handover(bp, tldr="new", session_kind="milestone-complete")

    apply_supersession(bp, old_id=old_id, new_id=mid_id)
    apply_supersession(bp, old_id=old_id, new_id=new_id)

    fm_old, _ = read_handover(bp, old_id)
    assert fm_old["status"] == "done"
