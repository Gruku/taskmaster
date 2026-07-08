"""Archive movement on task close."""
import pytest


def _setup(tmp_path):
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\n")
    return bp


def test_archive_bug_moves_fixed_bug_to_archive_dir(tmp_path):
    from taskmaster.taskmaster_v3 import write_bug, update_bug, archive_bug, bug_path
    bp = _setup(tmp_path)
    bid, _ = write_bug(bp, title="t", discovered_by="user")
    update_bug(bp, bid, status="fixed", fix_commit="abcd")
    assert bug_path(bp, bid).exists()
    archive_bug(bp, bid)
    assert not bug_path(bp, bid).exists()
    assert bug_path(bp, bid, archived=True).exists()


def test_archive_bug_refuses_open(tmp_path):
    from taskmaster.taskmaster_v3 import write_bug, archive_bug
    bp = _setup(tmp_path)
    bid, _ = write_bug(bp, title="t", discovered_by="user")
    with pytest.raises(ValueError, match="cannot archive bug with status=open"):
        archive_bug(bp, bid)


def test_archive_bug_idempotent_if_already_archived(tmp_path):
    from taskmaster.taskmaster_v3 import write_bug, update_bug, archive_bug, bug_path
    bp = _setup(tmp_path)
    bid, _ = write_bug(bp, title="t", discovered_by="user")
    update_bug(bp, bid, status="fixed", fix_commit="abcd")
    archive_bug(bp, bid)
    archive_bug(bp, bid)  # should not raise
    assert bug_path(bp, bid, archived=True).exists()


def test_read_bug_falls_through_to_archive(tmp_path):
    from taskmaster.taskmaster_v3 import write_bug, update_bug, archive_bug, read_bug
    bp = _setup(tmp_path)
    bid, _ = write_bug(bp, title="t", discovered_by="user")
    update_bug(bp, bid, status="fixed", fix_commit="abcd")
    archive_bug(bp, bid)
    fm, _ = read_bug(bp, bid)
    assert fm["id"] == bid
