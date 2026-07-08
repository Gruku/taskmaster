"""Unit tests for the Ideas data layer in taskmaster_v3."""
from pathlib import Path


def test_idea_path_returns_expected_location(tmp_path):
    from taskmaster.taskmaster_v3 import idea_path
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    p = idea_path(bp, "IDEA-007")
    assert p == tmp_path / ".taskmaster" / "ideas" / "IDEA-007.md"


def test_idea_dir_returns_expected_location(tmp_path):
    from taskmaster.taskmaster_v3 import idea_dir
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    assert idea_dir(bp) == tmp_path / ".taskmaster" / "ideas"


def test_ideas_index_path_returns_expected_location(tmp_path):
    from taskmaster.taskmaster_v3 import ideas_index_path
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    assert ideas_index_path(bp) == tmp_path / ".taskmaster" / "ideas" / "IDEAS.md"


def test_list_idea_ids_empty_dir(tmp_path):
    from taskmaster.taskmaster_v3 import list_idea_ids
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    assert list_idea_ids(bp) == []


def test_list_idea_ids_sorted_numerically(tmp_path):
    from taskmaster.taskmaster_v3 import list_idea_ids, idea_dir
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    d = idea_dir(bp)
    d.mkdir(parents=True)
    (d / "IDEA-002.md").write_text("---\nid: IDEA-002\n---\n")
    (d / "IDEA-010.md").write_text("---\nid: IDEA-010\n---\n")
    (d / "IDEA-001.md").write_text("---\nid: IDEA-001\n---\n")
    (d / "IDEAS.md").write_text("# Ideas\n")  # index file must be ignored
    assert list_idea_ids(bp) == ["IDEA-001", "IDEA-002", "IDEA-010"]


def test_next_idea_id_first(tmp_path):
    from taskmaster.taskmaster_v3 import next_idea_id
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    assert next_idea_id(bp) == "IDEA-001"


def test_next_idea_id_after_existing(tmp_path):
    from taskmaster.taskmaster_v3 import next_idea_id, idea_dir
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    d = idea_dir(bp)
    d.mkdir(parents=True)
    (d / "IDEA-007.md").write_text("---\nid: IDEA-007\n---\n")
    (d / "IDEA-003.md").write_text("---\nid: IDEA-003\n---\n")
    assert next_idea_id(bp) == "IDEA-008"


def test_write_idea_minimal(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, read_idea
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
    from taskmaster.taskmaster_v3 import write_idea, read_idea
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
    from taskmaster.taskmaster_v3 import write_idea
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    with _pytest.raises(ValueError, match="title"):
        write_idea(bp, title="   ")


def test_write_idea_appends_to_index(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, ideas_index_path
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
    from taskmaster.taskmaster_v3 import write_idea
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    a, _ = write_idea(bp, title="a")
    b, _ = write_idea(bp, title="b")
    c, _ = write_idea(bp, title="c")
    assert (a, b, c) == ("IDEA-001", "IDEA-002", "IDEA-003")


def test_update_idea_status(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, update_idea, read_idea
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    iid, _ = write_idea(bp, title="An idea")
    update_idea(bp, iid, status="parking-lot")
    fm, _ = read_idea(bp, iid)
    assert fm["status"] == "parking-lot"


def test_update_idea_archive_sets_flag_and_strikes_index(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, update_idea, read_idea, ideas_index_path
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    iid, _ = write_idea(bp, title="Drop-this idea")
    update_idea(bp, iid, archived=True)
    fm, _ = read_idea(bp, iid)
    assert fm["archived"] is True
    idx = ideas_index_path(bp).read_text()
    assert "~~Drop-this idea~~" in idx
    assert "_(archived)_" in idx


def test_update_idea_promote_records_task_id(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, update_idea, read_idea
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    iid, _ = write_idea(bp, title="Becomes a task")
    update_idea(bp, iid, promoted_to="T-XYZ")
    fm, _ = read_idea(bp, iid)
    assert fm["promoted_to"] == "T-XYZ"


def test_update_idea_body_replacement(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, update_idea, read_idea
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    iid, _ = write_idea(bp, title="An idea", body="old body")
    update_idea(bp, iid, body="new body")
    _, body = read_idea(bp, iid)
    assert body == "new body"


def test_update_idea_preserves_body_when_not_passed(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, update_idea, read_idea
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    iid, _ = write_idea(bp, title="Keep me", body="original body")
    update_idea(bp, iid, status="exploring")
    _, body = read_idea(bp, iid)
    assert body == "original body"


def test_update_idea_unknown_id_raises(tmp_path):
    import pytest as _pytest
    from taskmaster.taskmaster_v3 import update_idea
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    with _pytest.raises(FileNotFoundError):
        update_idea(bp, "IDEA-999", status="exploring")


def test_list_ideas_empty(tmp_path):
    from taskmaster.taskmaster_v3 import list_ideas
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    assert list_ideas(bp) == []


def test_list_ideas_returns_summaries_newest_first(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, list_ideas
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    write_idea(bp, title="oldest")
    write_idea(bp, title="middle")
    write_idea(bp, title="newest")
    out = list_ideas(bp)
    assert [e["title"] for e in out] == ["newest", "middle", "oldest"]
    assert out[0]["id"] == "IDEA-003"
    # Body is omitted in summaries
    assert "body" not in out[0]


def test_list_ideas_excludes_archived_by_default(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, update_idea, list_ideas
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    a, _ = write_idea(bp, title="active")
    b, _ = write_idea(bp, title="archived")
    update_idea(bp, b, archived=True)
    ids = [e["id"] for e in list_ideas(bp)]
    assert a in ids
    assert b not in ids


def test_list_ideas_includes_archived_when_requested(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, update_idea, list_ideas
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    write_idea(bp, title="active")
    b, _ = write_idea(bp, title="archived")
    update_idea(bp, b, archived=True)
    ids = [e["id"] for e in list_ideas(bp, archived=True)]
    assert "IDEA-001" in ids and "IDEA-002" in ids


def test_list_ideas_filter_by_status(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, list_ideas
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    write_idea(bp, title="exploring one", status="exploring")
    write_idea(bp, title="parked one", status="parking-lot")
    out = list_ideas(bp, status="exploring")
    assert [e["title"] for e in out] == ["exploring one"]


def test_list_ideas_filter_by_tag(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, list_ideas
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    write_idea(bp, title="perf", tags=["perf", "automation"])
    write_idea(bp, title="ux", tags=["ux"])
    out = list_ideas(bp, tag="perf")
    assert [e["title"] for e in out] == ["perf"]


def test_list_ideas_filter_by_related_task(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, list_ideas
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    write_idea(bp, title="linked", related_tasks=["v3-release-007"])
    write_idea(bp, title="unlinked")
    out = list_ideas(bp, related_task="v3-release-007")
    assert [e["title"] for e in out] == ["linked"]


def test_list_ideas_idea_id_returns_full_record(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, list_ideas
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    iid, _ = write_idea(bp, title="single", body="full body content")
    out = list_ideas(bp, idea_id=iid)
    assert len(out) == 1
    # Single-id returns body too
    assert out[0]["body"] == "full body content"


def test_list_ideas_limit(tmp_path):
    from taskmaster.taskmaster_v3 import write_idea, list_ideas
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    for i in range(5):
        write_idea(bp, title=f"idea {i}")
    out = list_ideas(bp, limit=3)
    assert len(out) == 3


def test_list_ideas_summary_false_includes_body(tmp_path):
    """summary=False augments each summary record with its full body."""
    from taskmaster.taskmaster_v3 import write_idea, list_ideas
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    write_idea(bp, title="A", body="alpha body")
    write_idea(bp, title="B", body="beta body")
    out = list_ideas(bp, summary=False)
    bodies_by_title = {e["title"]: e["body"] for e in out}
    assert bodies_by_title == {"A": "alpha body", "B": "beta body"}


def test_list_ideas_summary_true_omits_body(tmp_path):
    """summary=True (default) omits body to keep payloads small."""
    from taskmaster.taskmaster_v3 import write_idea, list_ideas
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    write_idea(bp, title="solo", body="some body content")
    out = list_ideas(bp)  # default summary=True
    assert "body" not in out[0]


def test_write_idea_concurrent_allocations_unique(tmp_path):
    """Two interleaved write_idea calls must not collide on the same IDEA-NNN.

    Simulates the race by pre-touching IDEA-001 (as if another writer just
    won the race) and then calling write_idea — it should bump to IDEA-002
    rather than overwriting.
    """
    from taskmaster.taskmaster_v3 import write_idea, idea_path
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    # Pre-create IDEA-001 to simulate another writer holding the slot.
    pre = idea_path(bp, "IDEA-001")
    pre.parent.mkdir(parents=True, exist_ok=True)
    pre.touch()
    iid, _ = write_idea(bp, title="should-bump")
    assert iid == "IDEA-002"
    # The pre-touched IDEA-001 file is left untouched (empty).
    assert pre.read_text(encoding="utf-8") == ""
