import sys
from pathlib import Path
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster_v3 import (
    flag_open_reason,
    read_handover,
    smart_auto_close_handovers,
    write_handover,
)


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_flag_open_reason_returns_none_when_no_flag(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="clean", session_kind="end-of-day")
    assert flag_open_reason(bp, hid) is None


def test_flag_open_reason_returns_reason_after_smart_close_flags(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(
        bp,
        tldr="has live ref",
        session_kind="context-handoff",
        task_ids=["T-1"],
        next_action="",
    )
    smart_auto_close_handovers(
        bp,
        triggering_task_id="T-1",
        done_or_archived_ids={"T-1"},
    )
    reason = flag_open_reason(bp, hid)
    assert reason is not None
    assert "context-handoff" in reason or "session_kind" in reason


def test_flag_open_reason_returns_none_after_close(tmp_path):
    from taskmaster_v3 import update_handover_status
    bp = _setup(tmp_path)
    hid, _ = write_handover(
        bp,
        tldr="will close",
        session_kind="task-complete",
        task_ids=["T-1"],
    )
    update_handover_status(bp, handover_id=hid, status="closed")
    assert flag_open_reason(bp, hid) is None
