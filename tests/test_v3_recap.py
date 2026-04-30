"""Recap entity + handover-kind viewer mapping."""
import pytest


def test_recap_schema_version_is_one():
    from taskmaster_v3 import RECAP_SCHEMA_VERSION
    assert RECAP_SCHEMA_VERSION == 1


def test_handover_kind_to_viewer_kind_maps_all_four():
    from taskmaster_v3 import HANDOVER_KIND_TO_VIEWER_KIND, HANDOVER_KINDS
    # Spec §3.12 — viewer renders the four storage kinds as four UI kinds:
    assert HANDOVER_KIND_TO_VIEWER_KIND["end-of-day"]      == "wrap"
    assert HANDOVER_KIND_TO_VIEWER_KIND["context-handoff"] == "mid-task"
    assert HANDOVER_KIND_TO_VIEWER_KIND["crash-recovery"]  == "checkpoint"
    assert HANDOVER_KIND_TO_VIEWER_KIND["auto-stage"]      == "standalone"
    # Mapping covers every storage kind:
    assert set(HANDOVER_KIND_TO_VIEWER_KIND.keys()) == set(HANDOVER_KINDS)
    # All viewer kinds are valid:
    assert set(HANDOVER_KIND_TO_VIEWER_KIND.values()) == {
        "mid-task", "checkpoint", "wrap", "standalone"
    }
