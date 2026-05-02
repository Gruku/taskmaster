"""Tests for v3 handover write/validate/supersession plumbing."""
import pytest
from pathlib import Path
import yaml

from taskmaster_v3 import (
    HANDOVER_KINDS,
    HANDOVER_KIND_TO_VIEWER_KIND,
    write_handover,
)


def test_handover_kinds_match_spec():
    assert set(HANDOVER_KINDS) == {
        "end-of-day",
        "context-handoff",
        "milestone-complete",
        "pivot",
        "exploration",
        "auto-stage",
    }
    # crash-recovery was removed — it never shipped to skills.
    assert "crash-recovery" not in HANDOVER_KINDS


def test_viewer_kind_mapping_covers_all_storage_kinds():
    for kind in HANDOVER_KINDS:
        assert kind in HANDOVER_KIND_TO_VIEWER_KIND, f"missing mapping for {kind}"


def _make_backlog(tmp_path: Path) -> Path:
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_write_handover_rejects_unknown_session_kind(tmp_path):
    bp = _make_backlog(tmp_path)
    with pytest.raises(ValueError, match="session_kind"):
        write_handover(bp, tldr="test", session_kind="not-a-real-kind")


def test_write_handover_accepts_each_known_kind(tmp_path):
    bp = _make_backlog(tmp_path)
    for kind in HANDOVER_KINDS:
        hid, _ = write_handover(
            bp, tldr=f"test {kind}", session_kind=kind,
        )
        assert hid.endswith(f"test-{kind}".lower().replace(" ", "-")[:40].rstrip("-")) or kind in hid
