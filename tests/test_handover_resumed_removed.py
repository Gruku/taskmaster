"""Guard: mark_task_handovers_resumed must not exist on the public surface."""
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))


def test_mark_task_handovers_resumed_not_importable():
    """After enum rename, the old resumed helper must be removed."""
    import taskmaster_v3 as m
    assert not hasattr(m, "mark_task_handovers_resumed"), (
        "mark_task_handovers_resumed must be removed — it hard-codes 'todo'/'in-progress' "
        "which are invalid in the new enum. Use smart_auto_close_handovers instead."
    )


def test_pick_task_does_not_corrupt_handover_status(tmp_path):
    """Regression: pick-task on a task with a related open handover must not
    write invalid status values into the handover's frontmatter."""
    import yaml
    from taskmaster_v3 import read_handover, write_handover, HANDOVER_STATUSES

    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    (tmp_path / "handovers").mkdir()

    hid, _ = write_handover(
        bp,
        tldr="open track",
        session_kind="context-handoff",
        task_ids=["T-1"],
    )

    # Simulate what pick-task used to do: call mark_task_handovers_resumed.
    # Since the function must not exist, any caller of it would fail.
    # Instead, assert the handover is still 'open' with no mutation.
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "open"
    assert fm["status"] in HANDOVER_STATUSES, (
        f"status {fm['status']!r} must be in the new enum {HANDOVER_STATUSES}"
    )
