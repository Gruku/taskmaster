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


class _Stat:
    def __init__(self, attributes, tag):
        import stat as _stat
        self.st_mode, self.st_file_attributes, self.st_reparse_tag = _stat.S_IFREG, attributes, tag


@pytest.mark.parametrize("attributes,tag,refused", [
    (0x20, 0, False),                 # ordinary file
    (0x420, 0x9000001A, False),       # cloud placeholder: not a name surrogate
    (0x420, 0x80000013, False),       # dedup: not a name surrogate
    (0x410, 0xA0000003, True),        # junction / mount point
    (0x420, 0xA000000C, True),        # symbolic link
    (0x420, 0, True),                 # reparse point with an unreadable tag
])
def test_only_name_surrogate_reparse_points_are_refused(monkeypatch, attributes, tag, refused):
    from taskmaster import projection_paths

    class Fake:
        def lstat(self):
            return _Stat(attributes, tag)

        def __str__(self):
            return "fake"

    if refused:
        with pytest.raises(projection_paths.UnsafePath):
            projection_paths.check_component(Fake())
    else:
        projection_paths.check_component(Fake())


def test_crlf_detection_treats_refused_or_missing_paths_as_no_vote(tmp_path):
    from taskmaster import store
    from taskmaster.projection_paths import safe_path, UnsafePath
    write(tmp_path, "tasks/a.md", b"a\r\nb\r\n")

    def guard(rel):
        if rel.startswith("bugs") or rel == "backlog.yaml":
            raise UnsafePath(f"refused {rel}")
        return safe_path(tmp_path, rel)

    assert store.detect_dominant_crlf(tmp_path, path_guard=guard) is True
    missing = tmp_path / "absent-root"
    assert store.detect_dominant_crlf(missing, path_guard=lambda rel: safe_path(missing, rel)) is False


def test_concurrent_fingerprint_saves_use_their_own_temp_files(tmp_path, monkeypatch):
    """Coordinator threads may save the fingerprint cache at once (a batched sync between
    batches, git.generation outside the publication lock): each writes its own temp file
    and replaces the cache atomically, so no save clobbers another's half-written temp."""
    import json
    import threading
    backlog = tmp_path / ".taskmaster"
    backlog.mkdir()
    temps = []
    original = Path.write_text

    def recording(self, *args, **kwargs):
        temps.append(self.name)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", recording)
    scans = []
    for index in range(8):
        scan = sync_files.Scan(backlog)
        scan._entries[f"tasks/t-{index:03d}.md"] = [[1, 2, 3, 4, 5], ["a" * 40] * 5]
        scans.append(scan)
    start = threading.Barrier(len(scans))

    def save(scan):
        start.wait()
        sync_files.save_scan(tmp_path, scan)
    threads = [threading.Thread(target=save, args=(scan,)) for scan in scans]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(temps) == len(scans) and len(set(temps)) == len(temps), temps
    cache = sync_files.cache_path(tmp_path)
    assert json.loads(cache.read_text(encoding="utf-8"))["version"] == sync_files.CACHE_VERSION
    assert [path.name for path in cache.parent.iterdir()] == [cache.name]  # no temp left behind
