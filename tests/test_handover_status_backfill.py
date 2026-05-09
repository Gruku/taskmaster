import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster_v3 import backfill_handover_status, read_handover, write_task_file


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    hd = tmp_path / "handovers"
    hd.mkdir()
    return bp, hd


def _write_legacy_handover(handovers_dir: Path, hid: str, tldr: str):
    """Write a handover file lacking the new status fields (pre-feature shape)."""
    fm = {
        "id": hid,
        "date": "2025-01-01",
        "created": "2025-01-01T00:00:00+00:00",
        "tldr": tldr,
        "task_ids": [],
        "session_kind": "end-of-day",
    }
    write_task_file(handovers_dir / f"{hid}.md", fm, "body")


def test_backfill_sets_done_on_legacy_handovers(tmp_path):
    bp, hd = _setup(tmp_path)
    _write_legacy_handover(hd, "2025-01-01-legacy-a", "old a")
    _write_legacy_handover(hd, "2025-01-02-legacy-b", "old b")

    data = yaml.safe_load(bp.read_text())
    flipped = backfill_handover_status(data, bp)

    assert sorted(flipped) == ["2025-01-01-legacy-a", "2025-01-02-legacy-b"]
    for hid in flipped:
        fm, _ = read_handover(bp, hid)
        assert fm["status"] == "done"
        assert fm["status_user_set"] is False
        assert "backfilled" in fm["status_reason"].lower()
        assert fm.get("status_changed")
    assert data["handover_status_backfilled"] is True


def test_backfill_idempotent_on_second_run(tmp_path):
    bp, hd = _setup(tmp_path)
    _write_legacy_handover(hd, "2025-01-01-legacy-a", "old a")
    data = yaml.safe_load(bp.read_text())
    backfill_handover_status(data, bp)
    flipped_again = backfill_handover_status(data, bp)
    assert flipped_again == []  # marker present, no-op


def test_backfill_leaves_already_statused_handovers_alone(tmp_path):
    """A handover that already has status (i.e. written post-feature) is
    untouched."""
    bp, hd = _setup(tmp_path)
    fm = {
        "id": "2026-05-09-modern", "date": "2026-05-09",
        "created": "2026-05-09T00:00:00+00:00",
        "tldr": "modern", "task_ids": [], "session_kind": "end-of-day",
        "status": "todo", "status_changed": "2026-05-09T00:00:00+00:00",
        "status_user_set": False,
    }
    write_task_file(hd / "2026-05-09-modern.md", fm, "")
    data = yaml.safe_load(bp.read_text())
    backfill_handover_status(data, bp)
    fm_after, _ = read_handover(bp, "2026-05-09-modern")
    assert fm_after["status"] == "todo"


def test_backfill_stamps_marker_even_on_empty_backlog(tmp_path):
    """Empty backlog with no handover files still gets the marker stamped on
    first run, so subsequent process starts skip the scan via the on-disk flag."""
    bp, _ = _setup(tmp_path)  # no handover files written
    data = yaml.safe_load(bp.read_text())
    flipped = backfill_handover_status(data, bp)
    assert flipped == []
    assert data["handover_status_backfilled"] is True
