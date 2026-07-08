"""End-to-end smoke: create two parallel handovers, complete one task,
verify smart-close fires correctly, verify flagged reason surfaces."""
import sys
from pathlib import Path
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster.taskmaster_v3 import (
    HANDOVER_STATUSES,
    flag_open_reason,
    list_handover_ids,
    migrate_handover_statuses,
    read_handover,
    smart_auto_close_handovers,
    update_handover_status,
    write_handover,
)


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_parallel_handover_full_lifecycle(tmp_path):
    bp = _setup(tmp_path)

    # 1. Two parallel tracks written.
    track_a_id, _ = write_handover(
        bp,
        tldr="Track A: rewriting auth middleware",
        session_kind="task-complete",
        task_ids=["T-1"],
        next_action="",
    )
    track_b_id, _ = write_handover(
        bp,
        tldr="Track B: exploring DB migration",
        session_kind="context-handoff",
        task_ids=["T-2"],
        next_action="Next: run T-3 migration script",
    )

    # Both start as open.
    fm_a, _ = read_handover(bp, track_a_id)
    fm_b, _ = read_handover(bp, track_b_id)
    assert fm_a["status"] == "open"
    assert fm_b["status"] == "open"

    # 2. T-1 completes — smart-close runs.
    done_ids = {"T-1"}
    result = smart_auto_close_handovers(
        bp,
        triggering_task_id="T-1",
        done_or_archived_ids=done_ids,
    )

    # Track A: all tasks done, no next_action, eligible kind → closed.
    assert track_a_id in result["closed"]
    fm_a_after, _ = read_handover(bp, track_a_id)
    assert fm_a_after["status"] == "closed"
    assert flag_open_reason(bp, track_a_id) is None  # closed → no flag

    # Track B: T-1 not in its task_ids → untouched.
    fm_b_after, _ = read_handover(bp, track_b_id)
    assert fm_b_after["status"] == "open"

    # 3. T-2 completes — Track B gets flagged (context-handoff + live T-3 ref).
    done_ids = {"T-1", "T-2"}
    result2 = smart_auto_close_handovers(
        bp,
        triggering_task_id="T-2",
        done_or_archived_ids=done_ids,
    )
    assert track_b_id in result2["flagged"]
    fm_b_flagged, _ = read_handover(bp, track_b_id)
    assert fm_b_flagged["status"] == "open"
    assert fm_b_flagged.get("flag_reason"), "flag_reason must be set"

    # 4. flag_open_reason surfaces the reason string.
    reason = flag_open_reason(bp, track_b_id)
    assert reason is not None

    # 5. Manually close Track B.
    update_handover_status(bp, handover_id=track_b_id, status="closed", reason="deferred T-3 to backlog")
    assert flag_open_reason(bp, track_b_id) is None  # closed now


def test_new_enum_values_are_valid_statuses():
    for s in ("open", "closed", "superseded"):
        assert s in HANDOVER_STATUSES


def test_migration_runs_on_legacy_data(tmp_path):
    bp = _setup(tmp_path)
    from taskmaster.taskmaster_v3 import write_task_file
    hd = tmp_path / "handovers"
    legacy_fm = {
        "id": "2025-06-01-legacy",
        "date": "2025-06-01",
        "created": "2025-06-01T00:00:00+00:00",
        "tldr": "old work",
        "task_ids": [],
        "session_kind": "end-of-day",
        "status": "todo",
        "status_changed": "2025-06-01T00:00:00+00:00",
        "status_user_set": False,
    }
    write_task_file(hd / "2025-06-01-legacy.md", legacy_fm, "body")
    data = yaml.safe_load(bp.read_text())
    report = migrate_handover_statuses(data, bp, done_or_archived_ids=set())
    assert "2025-06-01-legacy" in report["migrated"]
    fm, _ = read_handover(bp, "2025-06-01-legacy")
    assert fm["status"] == "open"
