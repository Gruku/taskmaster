"""Tests for v3 handover write/validate/supersession plumbing."""
import pytest

from taskmaster_v3 import (
    HANDOVER_KINDS,
    HANDOVER_KIND_TO_VIEWER_KIND,
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
