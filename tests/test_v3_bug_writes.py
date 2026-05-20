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
