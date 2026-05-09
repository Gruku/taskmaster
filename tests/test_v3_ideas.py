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


def test_write_idea_minimal(tmp_path):
    from taskmaster_v3 import write_idea, read_idea
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    iid, path = write_idea(bp, title="Per-task spike budgets")
    assert iid == "IDEA-001"
    assert path.exists()
    fm, body = read_idea(bp, iid)
    assert fm["id"] == "IDEA-001"
    assert fm["title"] == "Per-task spike budgets"
    assert fm["created_by"] == "Claude"
    assert fm["status"] == ""
    assert fm["tags"] == []
    assert fm["related_tasks"] == []
    assert fm["archived"] is False
    assert fm["promoted_to"] is None
    assert "created" in fm  # ISO-8601 string
    assert body == ""


def test_write_idea_full_payload(tmp_path):
    from taskmaster_v3 import write_idea, read_idea
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    iid, _ = write_idea(
        bp,
        title="Auto-tag from git diff",
        body="## Why\n\nLink ideas to recent files.",
        tags=["automation", "perf"],
        status="exploring",
        related_tasks=["v3-release-007"],
        related_issues=["ISS-004"],
        related_lessons=["L-001"],
        created_by="user",
    )
    fm, body = read_idea(bp, iid)
    assert fm["tags"] == ["automation", "perf"]
    assert fm["status"] == "exploring"
    assert fm["related_tasks"] == ["v3-release-007"]
    assert fm["related_issues"] == ["ISS-004"]
    assert fm["related_lessons"] == ["L-001"]
    assert fm["created_by"] == "user"
    assert "Link ideas to recent files" in body


def test_write_idea_rejects_empty_title(tmp_path):
    import pytest as _pytest
    from taskmaster_v3 import write_idea
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    with _pytest.raises(ValueError, match="title"):
        write_idea(bp, title="   ")


def test_write_idea_appends_to_index(tmp_path):
    from taskmaster_v3 import write_idea, ideas_index_path
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    write_idea(bp, title="First idea")
    write_idea(bp, title="Second idea", status="exploring")
    idx = ideas_index_path(bp).read_text()
    assert idx.startswith("# Ideas\n")
    # Newest first within the file
    lines = [l for l in idx.splitlines() if l.startswith("- ")]
    assert len(lines) == 2
    assert "IDEA-002" in lines[0]
    assert "Second idea" in lines[0]
    assert "_(exploring)_" in lines[0]
    assert "IDEA-001" in lines[1]
    assert "First idea" in lines[1]
    # No status suffix when status is empty
    assert "_()_" not in lines[1]


def test_write_idea_sequential_ids(tmp_path):
    from taskmaster_v3 import write_idea
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    a, _ = write_idea(bp, title="a")
    b, _ = write_idea(bp, title="b")
    c, _ = write_idea(bp, title="c")
    assert (a, b, c) == ("IDEA-001", "IDEA-002", "IDEA-003")
