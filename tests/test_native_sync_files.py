"""N13 imports only known authored paths and rechecks exact observed bytes."""
from pathlib import Path

import pytest

from taskmaster.coordinator import sync_files


def write(root, rel, content=b"authored"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_known_paths_never_include_local_or_derived_files(tmp_path):
    for rel in ("backlog.yaml", "project.yaml", "tasks/a-001.md", "tasks/archive/a-002.md",
                "handovers/_archive/2025/old.md", "notes/_archive/NOTE-001.md",
                "local/store.db", "local/secret.md", "ideas/IDEAS.md", "PROGRESS.md",
                "tasks/.draft.md", "tasks/a.tmp.md", "tasks/a.corrupt-old.md"):
        write(tmp_path, rel)
    inventory = sync_files.discover(tmp_path)
    assert set(inventory.files) == {"backlog.yaml", "project.yaml", "tasks/a-001.md",
                                   "tasks/archive/a-002.md", "handovers/_archive/2025/old.md",
                                   "notes/_archive/NOTE-001.md"}
    assert not inventory.refused


def test_canonical_first_duplicate_is_reported_and_never_imported_twice(tmp_path):
    for rel in ("tasks/a.md", "tasks/archive/a.md", "trackers/TR-001.md",
                "integrations/trackers/TR-001.md", "handovers/_archive/z/h.md",
                "handovers/_archive/a/h.md"):
        write(tmp_path, rel)
    inventory = sync_files.discover(tmp_path)
    assert set(inventory.files) == {"tasks/a.md", "trackers/TR-001.md", "handovers/_archive/a/h.md"}
    assert inventory.duplicates == {"tasks/archive/a.md": "tasks/a.md",
                                     "integrations/trackers/TR-001.md": "trackers/TR-001.md",
                                     "handovers/_archive/z/h.md": "handovers/_archive/a/h.md"}


@pytest.mark.parametrize("rel", ["../tasks/a.md", "/tasks/a.md", "tasks/../a.md", "tasks\\a.md",
                                 "tasks/a.md:stream", "tasks/./a.md", "tasks//a.md",
                                 "local/secret.md", "tasks/archive/deep/a.md"])
def test_unknown_or_escaping_paths_refused(rel):
    with pytest.raises(ValueError):
        sync_files.classify(rel)


def test_linked_directory_refused_without_following_it(tmp_path):
    outside = tmp_path / "outside"
    write(outside, "a.md", b"secret")
    root = tmp_path / "backlog"
    root.mkdir()
    try:
        (root / "tasks").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("test account cannot create symlinks")
    inventory = sync_files.discover(root)
    assert not inventory.files
    assert "tasks" in inventory.refused
    with pytest.raises(sync_files.UnsafePath):
        sync_files.observe(root, "tasks/a.md")


def test_reparse_point_guard_covers_windows_junctions_without_following(tmp_path, monkeypatch):
    write(tmp_path, "tasks/a.md")
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == tmp_path / "tasks":
            class Reparse:
                st_mode = info.st_mode
                st_file_attributes = 0x400
            return Reparse()
        return info

    monkeypatch.setattr(Path, "lstat", lstat)
    assert "tasks" in sync_files.discover(tmp_path).refused
    with pytest.raises(sync_files.UnsafePath):
        sync_files.observe(tmp_path, "tasks/a.md")


def test_snapshot_changes_and_missing_file_are_distinct(tmp_path):
    path = write(tmp_path, "tasks/a.md", b"first")
    observed = sync_files.observe(tmp_path, "tasks/a.md")
    assert observed.content == b"first"
    assert sync_files.unchanged(tmp_path, observed)
    path.write_bytes(b"other")  # same length still requires a content check
    assert not sync_files.unchanged(tmp_path, observed)
    path.unlink()
    assert sync_files.observe(tmp_path, "tasks/a.md") is None
    assert not sync_files.unchanged(tmp_path, observed)


def test_bounded_reads_leave_source_untouched(tmp_path):
    path = write(tmp_path, "tasks/a.md", b"too large")
    with pytest.raises(ValueError, match="byte limit"):
        sync_files.observe(tmp_path, "tasks/a.md", limit=3)
    assert path.read_bytes() == b"too large"


def test_directory_in_place_of_file_is_not_a_missing_file(tmp_path):
    (tmp_path / "tasks" / "a.md").mkdir(parents=True)
    with pytest.raises(sync_files.UnsafePath):
        sync_files.observe(tmp_path, "tasks/a.md")


def test_line_ending_probe_uses_guard_for_directories_and_sampled_files(tmp_path):
    from taskmaster import store
    write(tmp_path, "tasks/a.md", b"line\r\n")
    seen = []
    def guard(rel):
        seen.append(rel)
        return sync_files.safe_path(tmp_path, rel)
    assert store.detect_dominant_crlf(tmp_path, path_guard=guard)
    assert "tasks" in seen and "tasks/a.md" in seen


def test_native_publisher_reuses_checked_path_boundary(tmp_path, monkeypatch):
    from taskmaster.native import projection
    called = []
    def refuse(root, rel):
        called.append((root, rel))
        raise sync_files.UnsafePath("test reparse refusal")
    monkeypatch.setattr(projection, "safe_path", refuse)
    exporter = object.__new__(projection.Exporter)
    exporter.backlog_dir = tmp_path
    with pytest.raises(sync_files.UnsafePath):
        exporter._path("tasks/a.md")
    assert called == [(tmp_path, "tasks/a.md")]
