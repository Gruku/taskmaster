import sys
from pathlib import Path
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster_v3 import (
    migrate_handover_statuses,
    read_handover,
    write_task_file,
)


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    hd = tmp_path / "handovers"
    hd.mkdir()
    return bp, hd


def _legacy(hd: Path, hid: str, status: str, superseded_by: str = "") -> None:
    fm: dict = {
        "id": hid, "date": "2025-01-01",
        "created": "2025-01-01T00:00:00+00:00",
        "tldr": "test",
        "task_ids": [], "session_kind": "end-of-day",
        "status": status,
        "status_changed": "2025-01-01T00:00:00+00:00",
        "status_user_set": False,
    }
    if superseded_by:
        fm["superseded_by"] = superseded_by
    write_task_file(hd / f"{hid}.md", fm, "body")


def test_migrate_todo_becomes_open(tmp_path):
    bp, hd = _setup(tmp_path)
    _legacy(hd, "2025-01-01-a", "todo")
    data = yaml.safe_load(bp.read_text())
    report = migrate_handover_statuses(data, bp, done_or_archived_ids=set())
    fm, _ = read_handover(bp, "2025-01-01-a")
    assert fm["status"] == "open"
    assert "2025-01-01-a" in report["migrated"]


def test_migrate_in_progress_becomes_open(tmp_path):
    bp, hd = _setup(tmp_path)
    _legacy(hd, "2025-01-01-b", "in-progress")
    data = yaml.safe_load(bp.read_text())
    migrate_handover_statuses(data, bp, done_or_archived_ids=set())
    fm, _ = read_handover(bp, "2025-01-01-b")
    assert fm["status"] == "open"


def test_migrate_done_with_superseded_by_becomes_superseded(tmp_path):
    bp, hd = _setup(tmp_path)
    _legacy(hd, "2025-01-01-old", "done", superseded_by="2025-01-02-new")
    data = yaml.safe_load(bp.read_text())
    migrate_handover_statuses(data, bp, done_or_archived_ids=set())
    fm, _ = read_handover(bp, "2025-01-01-old")
    assert fm["status"] == "superseded"


def test_migrate_done_eligible_for_smart_close_becomes_closed(tmp_path):
    """done handover whose task_ids are all terminal, no next_action, eligible kind → closed."""
    bp, hd = _setup(tmp_path)
    fm_data = {
        "id": "2025-01-01-eligible", "date": "2025-01-01",
        "created": "2025-01-01T00:00:00+00:00",
        "tldr": "finished",
        "task_ids": ["T-1"], "session_kind": "task-complete",
        "next_action": "",
        "status": "done",
        "status_changed": "2025-01-01T00:00:00+00:00",
        "status_user_set": False,
    }
    write_task_file(hd / "2025-01-01-eligible.md", fm_data, "body")
    data = yaml.safe_load(bp.read_text())
    migrate_handover_statuses(data, bp, done_or_archived_ids={"T-1"})
    fm, _ = read_handover(bp, "2025-01-01-eligible")
    assert fm["status"] == "closed"


def test_migrate_done_not_eligible_becomes_open(tmp_path):
    """done handover with next_action referencing live task → open (not closed)."""
    bp, hd = _setup(tmp_path)
    fm_data = {
        "id": "2025-01-01-live-ref", "date": "2025-01-01",
        "created": "2025-01-01T00:00:00+00:00",
        "tldr": "partial",
        "task_ids": ["T-1"], "session_kind": "task-complete",
        "next_action": "Start T-99",
        "status": "done",
        "status_changed": "2025-01-01T00:00:00+00:00",
        "status_user_set": False,
    }
    write_task_file(hd / "2025-01-01-live-ref.md", fm_data, "body")
    data = yaml.safe_load(bp.read_text())
    migrate_handover_statuses(data, bp, done_or_archived_ids={"T-1"})
    fm, _ = read_handover(bp, "2025-01-01-live-ref")
    assert fm["status"] == "open"


def test_migrate_idempotent(tmp_path):
    bp, hd = _setup(tmp_path)
    _legacy(hd, "2025-01-01-c", "todo")
    data = yaml.safe_load(bp.read_text())
    migrate_handover_statuses(data, bp, done_or_archived_ids=set())
    report2 = migrate_handover_statuses(data, bp, done_or_archived_ids=set())
    assert report2["migrated"] == []  # already migrated, no-op
