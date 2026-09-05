"""Decision entity — schema, allocation, validation."""
from pathlib import Path
import pytest
import yaml

from taskmaster import taskmaster_v3 as tm
import tests.entity_helpers as entity_helpers  # noqa: E402


@pytest.fixture
def backlog(tmp_path):
    bp = entity_helpers.taskmaster_backlog(tmp_path)
    bp.write_text("meta:\n  schema_version: 3\nepics: []\n", encoding="utf-8")
    return bp


def test_decision_dir_is_created_on_first_use(backlog):
    d = tm.decision_dir(backlog)
    assert d.name == "decisions"
    assert d.parent == backlog.parent


def test_decision_id_allocates_DEC_001_when_empty(backlog):
    did, _ = entity_helpers.write_decision(backlog, title="first", options=["a", "b"])
    assert did == "DEC-001"


def test_decision_id_increments_past_existing(backlog):
    d = tm.decision_dir(backlog)
    d.mkdir(parents=True)
    for ident in ("DEC-001", "DEC-007"):
        (d / f"{ident}.md").write_text(
            f"---\nid: {ident}\nstatus: open\noptions: [a, b]\n---\n", encoding="utf-8"
        )
    # The allocator counts the ids already on disk, so it never reuses DEC-007.
    did, _ = entity_helpers.write_decision(backlog, title="next", options=["a", "b"])
    assert did == "DEC-008"


def test_validate_decision_rejects_unknown_status():
    with pytest.raises(ValueError, match="status must be one of"):
        tm._validate_decision({"status": "wat", "options": ["a"]})


def test_validate_decision_requires_two_options():
    with pytest.raises(ValueError, match="at least 2 options"):
        tm._validate_decision({"status": "open", "options": ["only-one"]})


def test_validate_decision_resolved_requires_resolved_with():
    with pytest.raises(ValueError, match="resolved.*requires resolved_with"):
        tm._validate_decision({
            "status": "resolved",
            "options": ["a", "b"],
            "resolved_with": None,
        })


def test_validate_decision_recommendation_must_be_in_range():
    with pytest.raises(ValueError, match="recommendation must be 1.."):
        tm._validate_decision({
            "status": "open",
            "options": ["a", "b"],
            "recommendation": 3,
        })


def test_validate_decision_dropped_requires_reason():
    with pytest.raises(ValueError, match="status=dropped requires dropped_reason"):
        tm._validate_decision({
            "status": "dropped",
            "options": ["a", "b"],
            "dropped_reason": "",
        })


def test_write_decision_creates_file_with_frontmatter(backlog):
    did, target = entity_helpers.write_decision(
        backlog,
        title="Land ue-plugin-086 fix",
        options=[
            "Push feature + Draft MR against stage",
            "Local --no-ff into develop, push on approval",
            "Hold — user does merge",
        ],
        recommendation=2,
        task_id="ue-plugin-086",
        related_issues=["ISS-018"],
        branch="feature/ue-plugin-086",
        body="Context body here.\n",
    )
    assert did == "DEC-001"
    assert target.exists()
    fm, body = tm.read_decision(backlog, did)
    assert fm["id"] == "DEC-001"
    assert fm["title"] == "Land ue-plugin-086 fix"
    assert fm["status"] == "open"
    assert fm["recommendation"] == 2
    assert fm["task_id"] == "ue-plugin-086"
    assert fm["related_issues"] == ["ISS-018"]
    assert fm["branch"] == "feature/ue-plugin-086"
    assert "created_at" in fm
    assert fm["resolved_with"] is None
    assert fm["resolved_at"] is None
    assert fm["referenced_in"] == []
    assert body.strip() == "Context body here."


def test_write_decision_rejects_invalid(backlog):
    with pytest.raises(ValueError):
        entity_helpers.write_decision(backlog, title="Bad", options=["only-one"])
    with pytest.raises(ValueError, match="title is required"):
        entity_helpers.write_decision(backlog, title="  ", options=["a", "b"])
