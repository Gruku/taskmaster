"""Helpers and write paths for the Bug artifact."""
from pathlib import Path
import pytest


def test_bug_path_resolves_under_bugs_dir(tmp_path):
    from taskmaster_v3 import bug_path
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    out = bug_path(bp, "B-001")
    assert out == bp.parent / "bugs" / "B-001.md"


def test_bug_archive_path_resolves_under_archive_dir(tmp_path):
    from taskmaster_v3 import bug_path
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    out = bug_path(bp, "B-001", archived=True)
    assert out == bp.parent / "bugs" / "archive" / "B-001.md"


def test_next_bug_id_first_is_B001(tmp_path):
    from taskmaster_v3 import next_bug_id
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    (bp.parent / "bugs").mkdir(parents=True)
    assert next_bug_id(bp) == "B-001"


def test_next_bug_id_increments_max(tmp_path):
    from taskmaster_v3 import next_bug_id
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bugs = bp.parent / "bugs"
    bugs.mkdir(parents=True)
    (bugs / "B-001.md").write_text("---\nid: B-001\n---\n")
    (bugs / "B-007.md").write_text("---\nid: B-007\n---\n")
    (bugs / "archive").mkdir()
    (bugs / "archive" / "B-005.md").write_text("---\nid: B-005\n---\n")  # archived counts
    assert next_bug_id(bp) == "B-008"


def test_list_bug_ids_excludes_archive_by_default(tmp_path):
    from taskmaster_v3 import list_bug_ids
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bugs = bp.parent / "bugs"
    (bugs / "archive").mkdir(parents=True)
    (bugs / "B-001.md").write_text("---\nid: B-001\n---\n")
    (bugs / "archive" / "B-002.md").write_text("---\nid: B-002\n---\n")
    assert list_bug_ids(bp) == ["B-001"]
    assert list_bug_ids(bp, include_archive=True) == ["B-001", "B-002"]


def test_write_bug_creates_file_and_returns_id_path(tmp_path):
    from taskmaster_v3 import write_bug, read_bug
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\n")
    bid, target = write_bug(
        bp,
        title="Cosmetic mismatch in status pill",
        found_in="T-foo",
        discovered_by="user",
        components=["viewer"],
        body="## Repro\n1. open page\n",
    )
    assert bid == "B-001"
    assert target.exists()
    fm, body = read_bug(bp, bid)
    assert fm["id"] == "B-001"
    assert fm["status"] == "open"
    assert fm["found_in"] == "T-foo"
    assert "Repro" in body


def test_update_bug_status_to_fixed_requires_commit(tmp_path):
    from taskmaster_v3 import write_bug, update_bug
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\n")
    bid, _ = write_bug(bp, title="t", discovered_by="user")
    with pytest.raises(ValueError, match="status=fixed requires fix_commit"):
        update_bug(bp, bid, status="fixed")
    fm, _ = update_bug(bp, bid, status="fixed", fix_commit="abcd1234")
    assert fm["status"] == "fixed"
    assert fm["fix_commit"] == "abcd1234"


def test_sync_bug_index_lists_active_only(tmp_path):
    from taskmaster_v3 import write_bug, update_bug, archive_bug, sync_bug_index
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\n")
    bid1, _ = write_bug(bp, title="active one", discovered_by="user")
    bid2, _ = write_bug(bp, title="archived one", discovered_by="user")
    update_bug(bp, bid2, status="fixed", fix_commit="abcd")
    archive_bug(bp, bid2)

    data = {"bugs": []}
    sync_bug_index(data, bp)
    ids = [e["id"] for e in data["bugs"]]
    assert bid1 in ids
    assert bid2 not in ids  # archive excluded from active index
