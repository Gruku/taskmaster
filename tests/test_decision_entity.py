"""Decision entity — schema, allocation, validation."""
from pathlib import Path
import pytest
import yaml

from plugins.taskmaster import taskmaster_v3 as tm


@pytest.fixture
def backlog(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text("meta:\n  schema_version: 3\nepics: []\n", encoding="utf-8")
    return bp


def test_decision_dir_is_created_on_first_use(backlog):
    d = tm.decision_dir(backlog)
    assert d.name == "decisions"
    assert d.parent == backlog.parent


def test_next_decision_id_allocates_DEC_001_when_empty(backlog):
    assert tm.next_decision_id(backlog) == "DEC-001"


def test_next_decision_id_increments_past_existing(backlog):
    d = tm.decision_dir(backlog)
    d.mkdir(parents=True)
    (d / "DEC-001.md").write_text("---\nid: DEC-001\n---\n", encoding="utf-8")
    (d / "DEC-007.md").write_text("---\nid: DEC-007\n---\n", encoding="utf-8")
    assert tm.next_decision_id(backlog) == "DEC-008"
