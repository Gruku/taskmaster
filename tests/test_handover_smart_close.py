import sys
from pathlib import Path
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster.taskmaster_v3 import (
    read_handover,
    smart_auto_close_handovers,
    write_handover,
)

_DONE_TASKS = {"T-1", "T-2", "T-archived"}
_ARCHIVED_TASKS = {"T-archived"}
_ALL_TERMINAL = _DONE_TASKS | _ARCHIVED_TASKS


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_smart_close_all_tasks_done_empty_next_action(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(
        bp,
        tldr="all wrapped",
        session_kind="task-complete",
        task_ids=["T-1"],
        next_action="",
    )
    result = smart_auto_close_handovers(bp, triggering_task_id="T-1",
                                        done_or_archived_ids=_ALL_TERMINAL)
    assert hid in result["closed"]
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "closed"


def test_smart_close_surviving_next_action_keeps_open(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(
        bp,
        tldr="next references live task",
        session_kind="task-complete",
        task_ids=["T-1"],
        next_action="Start T-99 after merging",
    )
    result = smart_auto_close_handovers(bp, triggering_task_id="T-1",
                                        done_or_archived_ids=_ALL_TERMINAL)
    assert hid in result["flagged"]
    assert hid not in result["closed"]
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "open"


def test_smart_close_context_handoff_kind_keeps_open(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(
        bp,
        tldr="context handoff",
        session_kind="context-handoff",
        task_ids=["T-1"],
        next_action="",
    )
    result = smart_auto_close_handovers(bp, triggering_task_id="T-1",
                                        done_or_archived_ids=_ALL_TERMINAL)
    assert hid in result["flagged"]
    assert hid not in result["closed"]
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "open"


def test_smart_close_partial_task_ids_keeps_open(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(
        bp,
        tldr="two tasks, one live",
        session_kind="task-complete",
        task_ids=["T-1", "T-live"],
        next_action="",
    )
    result = smart_auto_close_handovers(bp, triggering_task_id="T-1",
                                        done_or_archived_ids=_ALL_TERMINAL)
    assert hid in result["flagged"]
    assert hid not in result["closed"]


def test_smart_close_skips_already_closed(tmp_path):
    from taskmaster.taskmaster_v3 import update_handover_status
    bp = _setup(tmp_path)
    hid, _ = write_handover(
        bp,
        tldr="already closed",
        session_kind="task-complete",
        task_ids=["T-1"],
        next_action="",
    )
    update_handover_status(bp, handover_id=hid, status="closed")
    result = smart_auto_close_handovers(bp, triggering_task_id="T-1",
                                        done_or_archived_ids=_ALL_TERMINAL)
    assert hid not in result["closed"]
    assert hid not in result["flagged"]


def test_smart_close_skips_superseded(tmp_path):
    from taskmaster.taskmaster_v3 import update_handover_status
    bp = _setup(tmp_path)
    hid, _ = write_handover(
        bp,
        tldr="superseded",
        session_kind="task-complete",
        task_ids=["T-1"],
    )
    update_handover_status(bp, handover_id=hid, status="superseded")
    result = smart_auto_close_handovers(bp, triggering_task_id="T-1",
                                        done_or_archived_ids=_ALL_TERMINAL)
    assert hid not in result["closed"]
    assert hid not in result["flagged"]


def test_smart_close_next_action_only_references_done_tasks_closes(tmp_path):
    """next_action that mentions only done task IDs still qualifies for auto-close."""
    bp = _setup(tmp_path)
    hid, _ = write_handover(
        bp,
        tldr="all refs done",
        session_kind="task-complete",
        task_ids=["T-1"],
        next_action="Confirmed T-2 is done, no further work needed.",
    )
    result = smart_auto_close_handovers(bp, triggering_task_id="T-1",
                                        done_or_archived_ids=_ALL_TERMINAL)
    assert hid in result["closed"]


def test_smart_close_null_session_kind_is_eligible(tmp_path):
    """A handover with null/missing session_kind satisfies the kind criterion."""
    from taskmaster.taskmaster_v3 import handover_path, write_task_file
    bp = _setup(tmp_path)
    hid = "2026-01-01-no-kind"
    fm = {
        "id": hid,
        "date": "2026-01-01",
        "created": "2026-01-01T00:00:00+00:00",
        "tldr": "no kind set",
        "task_ids": ["T-1"],
        # session_kind intentionally absent
        "next_action": "",
        "status": "open",
        "status_user_set": False,
        "status_changed": "2026-01-01T00:00:00+00:00",
    }
    write_task_file(handover_path(bp, hid), fm, "body")
    result = smart_auto_close_handovers(bp, triggering_task_id="T-1",
                                        done_or_archived_ids=_ALL_TERMINAL)
    assert hid in result["closed"], (
        "null/absent session_kind should be treated as eligible for auto-close"
    )
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "closed"


def test_smart_close_status_user_set_blocks_auto_close(tmp_path):
    """status_user_set=True on an otherwise-eligible handover must prevent auto-close."""
    import yaml as _yaml
    bp = _setup(tmp_path)
    # Write handover directly via write_task_file so status_user_set is set
    # in frontmatter without going through update_handover_status (which would
    # confound the test by also writing status_user_set logic).
    from taskmaster.taskmaster_v3 import handover_path, write_task_file
    hid = "2026-01-01-user-locked"
    fm = {
        "id": hid,
        "date": "2026-01-01",
        "created": "2026-01-01T00:00:00+00:00",
        "tldr": "user has locked status",
        "task_ids": ["T-1"],
        "session_kind": "task-complete",
        "next_action": "",
        "status": "open",
        "status_user_set": True,  # manually set — smart-close must honour this
        "status_changed": "2026-01-01T00:00:00+00:00",
    }
    write_task_file(handover_path(bp, hid), fm, "body")

    result = smart_auto_close_handovers(bp, triggering_task_id="T-1",
                                        done_or_archived_ids=_ALL_TERMINAL)
    assert hid not in result["closed"]
    assert hid not in result["flagged"]
    fm_after, _ = read_handover(bp, hid)
    assert fm_after["status"] == "open", (
        "status_user_set=True must prevent smart-close from touching the handover"
    )
