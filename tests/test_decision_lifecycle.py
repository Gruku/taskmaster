from pathlib import Path
import pytest

from plugins.taskmaster import taskmaster_v3 as tm


@pytest.fixture
def backlog(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text("meta:\n  schema_version: 3\nepics: []\n", encoding="utf-8")
    return bp


@pytest.fixture
def open_decision(backlog):
    did, _ = tm.write_decision(
        backlog, title="x", options=["a", "b", "c"], recommendation=2,
        task_id="t-001", branch="feature/x",
    )
    return did


def test_resolve_sets_status_resolved_with_and_timestamp(backlog, open_decision):
    fm = tm.resolve_decision(backlog, open_decision, resolved_with=2, rationale="winner")
    assert fm["status"] == "resolved"
    assert fm["resolved_with"] == 2
    assert fm["resolved_rationale"] == "winner"
    assert fm["resolved_at"]   # truthy ISO string


def test_resolve_rejects_out_of_range(backlog, open_decision):
    with pytest.raises(ValueError, match="must be 1..3"):
        tm.resolve_decision(backlog, open_decision, resolved_with=99)


def test_drop_sets_status_dropped_with_reason(backlog, open_decision):
    fm = tm.drop_decision(backlog, open_decision, reason="superseded by external decision")
    assert fm["status"] == "dropped"
    assert fm["dropped_reason"] == "superseded by external decision"


def test_update_decision_can_change_title_options_recommendation(backlog, open_decision):
    fm = tm.update_decision(backlog, open_decision, {
        "title": "Renamed",
        "options": ["a", "b", "c", "d"],
        "recommendation": 4,
    })
    assert fm["title"] == "Renamed"
    assert fm["options"] == ["a", "b", "c", "d"]
    assert fm["recommendation"] == 4


def test_update_decision_rejects_terminal_to_open(backlog, open_decision):
    tm.resolve_decision(backlog, open_decision, resolved_with=1)
    with pytest.raises(ValueError, match="cannot reopen"):
        tm.update_decision(backlog, open_decision, {"status": "open"})


def test_update_rejects_shrinking_options_below_resolved_with(backlog, open_decision):
    """B-026: once resolved with option 3, you cannot shrink options to fewer
    than 3 — that would leave resolved_with pointing past the end of the list
    and crash options[resolved_with - 1] on the next read."""
    tm.resolve_decision(backlog, open_decision, resolved_with=3)
    with pytest.raises(ValueError, match="resolved_with must be 1..2"):
        tm.update_decision(backlog, open_decision, {"options": ["a", "b"]})


def test_update_allows_shrinking_options_that_keep_resolved_with_in_range(backlog, open_decision):
    """B-026 guard must not over-fire: shrinking to a list that still contains
    the resolved option is fine."""
    tm.resolve_decision(backlog, open_decision, resolved_with=2)
    fm = tm.update_decision(backlog, open_decision, {"options": ["a", "b"]})
    assert fm["options"] == ["a", "b"]
    assert fm["resolved_with"] == 2


def test_link_handover_appends_referenced_in(backlog, open_decision):
    tm.link_decision_to_handover(backlog, open_decision, "2026-05-15-foo")
    fm, _ = tm.read_decision(backlog, open_decision)
    assert "2026-05-15-foo" in fm["referenced_in"]
    # idempotent — same id twice doesn't duplicate.
    tm.link_decision_to_handover(backlog, open_decision, "2026-05-15-foo")
    fm, _ = tm.read_decision(backlog, open_decision)
    assert fm["referenced_in"].count("2026-05-15-foo") == 1
