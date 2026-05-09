"""write_handover assigns the right default status by session_kind, and the
status field flows into the slim backlog.yaml index entry."""
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster_v3 import (
    HANDOVER_KINDS,
    HANDOVER_STATUSES,
    _HANDOVER_INDEX_FIELDS,
    read_handover,
    sync_handover_index,
    write_handover,
)


def _make_backlog(tmp_path: Path) -> Path:
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {"updated": "2026-05-09"}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_handover_statuses_enum_is_three_states():
    assert HANDOVER_STATUSES == ("todo", "in-progress", "done")


def test_status_in_index_fields():
    assert "status" in _HANDOVER_INDEX_FIELDS


def test_auto_stage_handover_defaults_to_done(tmp_path):
    bp = _make_backlog(tmp_path)
    hid, _ = write_handover(bp, tldr="auto-stage checkpoint", session_kind="auto-stage")
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "done"
    assert fm["status_user_set"] is False
    assert fm.get("status_changed")  # ISO timestamp present


@pytest.mark.parametrize("kind", [k for k in HANDOVER_KINDS if k != "auto-stage"])
def test_other_kinds_default_to_todo(tmp_path, kind):
    bp = _make_backlog(tmp_path)
    hid, _ = write_handover(bp, tldr=f"a {kind} handover", session_kind=kind)
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "todo"
    assert fm["status_user_set"] is False


def test_index_entry_includes_status_after_sync(tmp_path):
    bp = _make_backlog(tmp_path)
    write_handover(bp, tldr="indexed", session_kind="end-of-day")
    data = {"handovers": []}
    sync_handover_index(data, bp)
    assert len(data["handovers"]) == 1
    assert data["handovers"][0]["status"] == "todo"
