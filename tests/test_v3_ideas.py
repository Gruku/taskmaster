"""Unit tests for the Ideas data layer in taskmaster_v3."""
from pathlib import Path


def test_idea_path_returns_expected_location(tmp_path):
    from taskmaster_v3 import idea_path
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    p = idea_path(bp, "IDEA-007")
    assert p == tmp_path / ".taskmaster" / "ideas" / "IDEA-007.md"


def test_idea_dir_returns_expected_location(tmp_path):
    from taskmaster_v3 import idea_dir
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    assert idea_dir(bp) == tmp_path / ".taskmaster" / "ideas"


def test_ideas_index_path_returns_expected_location(tmp_path):
    from taskmaster_v3 import ideas_index_path
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    assert ideas_index_path(bp) == tmp_path / ".taskmaster" / "ideas" / "IDEAS.md"


def test_list_idea_ids_empty_dir(tmp_path):
    from taskmaster_v3 import list_idea_ids
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    assert list_idea_ids(bp) == []


def test_list_idea_ids_sorted_numerically(tmp_path):
    from taskmaster_v3 import list_idea_ids, idea_dir
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    d = idea_dir(bp)
    d.mkdir(parents=True)
    (d / "IDEA-002.md").write_text("---\nid: IDEA-002\n---\n")
    (d / "IDEA-010.md").write_text("---\nid: IDEA-010\n---\n")
    (d / "IDEA-001.md").write_text("---\nid: IDEA-001\n---\n")
    (d / "IDEAS.md").write_text("# Ideas\n")  # index file must be ignored
    assert list_idea_ids(bp) == ["IDEA-001", "IDEA-002", "IDEA-010"]


def test_next_idea_id_first(tmp_path):
    from taskmaster_v3 import next_idea_id
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    assert next_idea_id(bp) == "IDEA-001"


def test_next_idea_id_after_existing(tmp_path):
    from taskmaster_v3 import next_idea_id, idea_dir
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    d = idea_dir(bp)
    d.mkdir(parents=True)
    (d / "IDEA-007.md").write_text("---\nid: IDEA-007\n---\n")
    (d / "IDEA-003.md").write_text("---\nid: IDEA-003\n---\n")
    assert next_idea_id(bp) == "IDEA-008"
