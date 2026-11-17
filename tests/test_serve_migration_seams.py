# User intent: prove the shared seams team-server-014 extends in place (host id install, offline_owner with a held
# lease, publish_marker, copy_database, the allowlisted Git runner) behave as before by default and as designed when
# the new keyword is given.
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest

from taskmaster import server_fence as fence
from taskmaster import server_marker as sm
from taskmaster.native import cutover
from taskmaster.serve import gitrun
from taskmaster.serve import state_export as se
from native_git_helpers import init_repo
from test_native_service import root  # noqa: F401  (fixture)

HOST_ID = "9f0c3e7a5b1d4e2f8a6c0b9d7e5f3a1c"
OTHER_ID = "0123456789abcdef0123456789abcdef"
URL = "https://tm.example.com/mcp"


@pytest.fixture(autouse=True)
def _fresh_fence():
    fence.reset_for_tests()
    yield
    leaked = fence.reset_for_tests()
    assert not leaked, f"leaked host leases: {[lease.root for lease in leaked]}"


def project(tmp_path: Path) -> Path:
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    return tmp_path


# -- ensure_host_id(value=) ----------------------------------------------------------------------

def test_host_id_value_missing_is_written_owner_only(tmp_path):
    path = project(tmp_path)
    assert fence.ensure_host_id(path, value=HOST_ID) == HOST_ID
    assert fence.read_host_id(path) == HOST_ID
    if os.name != "nt":
        assert fence.host_id_path(path).stat().st_mode & 0o777 == 0o600


def test_host_id_value_equal_is_unchanged(tmp_path):
    path = project(tmp_path)
    fence.ensure_host_id(path, value=HOST_ID)
    stamp = fence.host_id_path(path).stat().st_mtime_ns
    assert fence.ensure_host_id(path, value=HOST_ID) == HOST_ID
    assert fence.host_id_path(path).stat().st_mtime_ns == stamp


def test_host_id_value_replaces_a_malformed_file(tmp_path):
    path = project(tmp_path)
    fence.ensure_host_id(path, value=HOST_ID)
    fence.host_id_path(path).write_text("garbage\n", encoding="ascii")
    assert fence.ensure_host_id(path, value=OTHER_ID) == OTHER_ID
    assert fence.read_host_id(path) == OTHER_ID


def test_host_id_value_different_raises_and_writes_nothing(tmp_path):
    path = project(tmp_path)
    fence.ensure_host_id(path, value=HOST_ID)
    before = fence.host_id_path(path).read_bytes()
    with pytest.raises(ValueError, match="different host id"):
        fence.ensure_host_id(path, value=OTHER_ID)
    assert fence.host_id_path(path).read_bytes() == before
    assert sorted(entry.name for entry in fence.host_id_path(path).parent.iterdir()) == ["host-id"]


def test_host_id_value_must_be_well_formed_and_not_rotate(tmp_path):
    path = project(tmp_path)
    with pytest.raises(ValueError):
        fence.ensure_host_id(path, value="XYZ")
    with pytest.raises(ValueError):
        fence.ensure_host_id(path, value=HOST_ID, rotate=True)
    assert fence.read_host_id(path) is None


def test_host_id_without_value_is_unchanged_behaviour(tmp_path):
    path = project(tmp_path)
    first = fence.ensure_host_id(path)
    assert fence.ensure_host_id(path) == first
    assert fence.ensure_host_id(path, rotate=True) != first


# -- publish_marker ------------------------------------------------------------------------------

def test_publish_marker_writes_the_canonical_marker_and_refreshes_a_stale_latch(tmp_path):
    path = project(tmp_path)
    (path / ".git").mkdir()
    sm.write_marker(path, URL, OTHER_ID)
    sm.record_latch(path, sm.read_marker(path))
    written = sm.publish_marker(path, "https://other.example.com/mcp", HOST_ID)
    assert written == sm.marker_path(path)
    marker = sm.read_marker(path)
    assert (marker.url, marker.host_id) == ("https://other.example.com/mcp", HOST_ID)
    latched = sm.read_latch(path)
    assert latched == marker
    assert written.read_bytes() == sm.render(marker).encode("utf-8")


def test_publish_marker_rejects_a_bad_url_and_writes_nothing(tmp_path):
    path = project(tmp_path)
    (path / ".git").mkdir()
    with pytest.raises(sm.ServerConfigError):
        sm.publish_marker(path, "ftp://x", HOST_ID)
    assert not sm.marker_path(path).exists() and sm.read_latch(path) is None


# -- offline_owner(lease=) -----------------------------------------------------------------------

def test_offline_owner_with_a_held_lease_neither_claims_nor_releases(root, monkeypatch):  # noqa: F811
    init_repo(root)
    root = Path(root).resolve()
    with fence.host_lease(root) as lease:
        calls = []
        real = fence.claim_host
        monkeypatch.setattr(fence, "claim_host", lambda *a, **k: calls.append(a) or real(*a, **k))
        with se.offline_owner(root, lease=lease) as owner:
            assert owner is not None
        assert calls == []
        assert lease.held()
        assert fence.host_lease_held(root)


def test_offline_owner_without_a_lease_claims_and_releases_its_own(root):  # noqa: F811
    init_repo(root)
    root = Path(root).resolve()
    with se.offline_owner(root):
        assert fence.host_lease_held(root)
    assert not fence.host_lease_held(root)


# -- copy_database -------------------------------------------------------------------------------

def test_copy_database_copies_a_wal_store_with_a_live_writer(tmp_path):
    source = tmp_path / "store.db"
    writer = sqlite3.connect(source)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE t(a INTEGER)")
    writer.executemany("INSERT INTO t VALUES(?)", [(n,) for n in range(500)])
    writer.commit()
    writer.close()
    stop = threading.Event()

    def run():
        connection = sqlite3.connect(source)
        try:
            while not stop.is_set():
                connection.execute("INSERT INTO t VALUES(-1)")
                connection.commit()
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    try:
        target = tmp_path / "copy.db"
        result = cutover.copy_database(source, target)
    finally:
        stop.set()
        thread.join()
    assert set(result) == {"sha256", "size"} and result["size"] == target.stat().st_size
    with closing(sqlite3.connect(target)) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert copy.execute("SELECT COUNT(*) FROM t").fetchone()[0] >= 500
    assert not (tmp_path / "copy.db.partial").exists()
    assert not any(entry.name.startswith("copy.db-") for entry in tmp_path.iterdir())


def test_copy_database_leaves_no_partial_when_the_source_is_missing(tmp_path):
    with pytest.raises(Exception):
        cutover.copy_database(tmp_path / "absent.db", tmp_path / "copy.db")
    assert list(tmp_path.glob("copy.db*")) == []


def test_projection_reads_has_a_public_name_and_an_alias():
    assert cutover.projection_reads is cutover._projection_reads


# -- AllowlistedGit ------------------------------------------------------------------------------

class Refusing(gitrun.AllowlistedGit):
    refused_error = KeyError

    def refusal(self, args, env):
        return None if args[:1] == ["rev-parse"] else f"git {args[0]}"


def test_a_subclass_refusal_blocks_before_any_process_starts(tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(gitrun.managed, "probe", lambda *a, **k: spawned.append(a) or (0, b"", b""))
    git = Refusing(tmp_path, tmp_path)
    with pytest.raises(KeyError, match="argv refused: git push"):
        git.run("push", "origin", "main")
    assert spawned == []
    git.run("rev-parse", "HEAD")
    assert len(spawned) == 1


def test_config_other_than_the_permitted_pairs_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(gitrun.managed, "probe", lambda *a, **k: (0, b"", b""))
    git = Refusing(tmp_path, tmp_path)
    with pytest.raises(KeyError, match="argv refused: git -c"):
        git.run("rev-parse", "HEAD", config=("-c", "core.editor=evil"))


def test_the_export_git_is_a_subclass_with_its_old_errors():
    assert issubclass(se.Git, gitrun.AllowlistedGit)
    assert se.Git.refused_error is se.ExportRefused and se.Git.failed_error is se.ExportError
