import sys
from pathlib import Path
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster_v3 import HANDOVER_STATUSES, write_handover, read_handover


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_handover_statuses_enum_has_new_values():
    assert "open" in HANDOVER_STATUSES
    assert "closed" in HANDOVER_STATUSES
    assert "superseded" in HANDOVER_STATUSES


def test_handover_statuses_enum_excludes_old_values():
    assert "todo" not in HANDOVER_STATUSES
    assert "in-progress" not in HANDOVER_STATUSES
    assert "done" not in HANDOVER_STATUSES


def test_write_handover_defaults_to_open(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="test", session_kind="end-of-day")
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "open"


def test_write_handover_auto_stage_defaults_to_closed(tmp_path):
    """auto-stage bookkeeping checkpoints are born closed — not open."""
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="auto", session_kind="auto-stage")
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "closed"


def test_update_handover_status_rejects_old_enum(tmp_path):
    import pytest
    from taskmaster_v3 import update_handover_status
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="test", session_kind="end-of-day")
    with pytest.raises(ValueError, match="open"):
        update_handover_status(bp, handover_id=hid, status="todo")
