import pytest
from plugins.taskmaster import taskmaster_v3 as tm


@pytest.fixture
def backlog(tmp_path):
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir()
    bp.write_text("meta:\n  schema_version: 3\nepics: []\nhandovers: []\n",
                  encoding="utf-8")
    return bp


def test_kinds_are_continuity_deep_context_milestone_auto_stage_task_complete():
    assert set(tm.HANDOVER_KINDS) == {"continuity", "deep-context", "milestone", "auto-stage", "task-complete"}


def test_legacy_kinds_translate_to_new_names_on_write(backlog):
    # Backwards compatibility: callers passing old kinds get mapped to new ones.
    hid, _ = tm.write_handover(backlog, tldr="x", session_kind="end-of-day")
    fm, _ = tm.read_handover(backlog, hid)
    assert fm["session_kind"] == "continuity"

    hid2, _ = tm.write_handover(backlog, tldr="y", session_kind="pivot")
    fm2, _ = tm.read_handover(backlog, hid2)
    assert fm2["session_kind"] == "milestone"


def test_handover_frontmatter_includes_open_decisions_and_resolved(backlog):
    hid, _ = tm.write_handover(
        backlog,
        tldr="x",
        open_decisions=["DEC-001", "DEC-003"],
        resolved_this_session=["DEC-002"],
    )
    fm, _ = tm.read_handover(backlog, hid)
    assert fm["open_decisions"] == ["DEC-001", "DEC-003"]
    assert fm["resolved_this_session"] == ["DEC-002"]


def test_handover_write_back_references_decisions(backlog):
    tm.write_decision(backlog, title="d1", options=["a", "b"])
    hid, _ = tm.write_handover(backlog, tldr="x", open_decisions=["DEC-001"])
    fm_dec, _ = tm.read_decision(backlog, "DEC-001")
    assert hid in fm_dec["referenced_in"]
