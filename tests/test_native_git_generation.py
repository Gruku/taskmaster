# User intent: N16 - hidden edits and the stat fingerprint. The sync fingerprint must see an in-place
# rewrite whose mtime was restored (a real change time, not Windows' creation-time ctime); an edit
# no timestamp shows (a memory-mapped write) may only delay an import, and must never let a managed
# Git operation commit or overwrite bytes the store did not publish - so `generation` reads in full.
"""Real hidden edits against the sync fingerprint cache and managed Git's generation check."""
from __future__ import annotations

import mmap
import os
from pathlib import Path
import time

import pytest

from taskmaster.coordinator import git as managed_git, sync_files
from taskmaster.coordinator.service import Coordinator
from test_native_git_checkouts import client_for, repo  # noqa: F401
from test_native_service import root  # noqa: F401
from test_native_service_sync import title
from test_native_sync_perf import age, age_projection

pytestmark = [pytest.mark.xdist_group('heavy_processes'), pytest.mark.allow_projection_bypass]
REL = 'tasks/test-epic-001.md'


def _restored_mtime_edit(path: Path, old: bytes, new: bytes) -> None:
    before = path.stat()
    raw = path.read_bytes()
    assert len(old) == len(new) and old in raw
    with path.open('r+b') as handle:
        handle.write(raw.replace(old, new))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def _mapped_edit(path: Path, old: bytes, new: bytes) -> None:
    raw = path.read_bytes()
    at = raw.index(old)
    with path.open('r+b') as handle, mmap.mmap(handle.fileno(), 0) as mapped:
        mapped[at:at + len(new)] = new
        mapped.flush()


def _stat_of(path: Path) -> tuple:
    info = path.stat()
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino


def _settled(repo, client):
    from native_git_helpers import git
    assert client.sync()['state'] == 'synchronized'
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '--allow-empty', '-m', 'published generation')
    age_projection(repo)
    assert client.sync()['state'] == 'synchronized'  # records every fingerprint


# ── the fingerprint itself ──────────────────────────────────────────────────

def test_an_in_place_rewrite_with_its_mtime_restored_is_a_fingerprint_miss(tmp_path):
    path = tmp_path / 'tasks' / 'a-001.md'
    path.parent.mkdir()
    path.write_bytes(b'Service task body\n' * 4)
    age(path)
    scan = sync_files.Scan(tmp_path)
    scan.observe('tasks/a-001.md')
    known = scan.entries()
    assert known, 'an aged file is recorded'
    before = _stat_of(path)
    _restored_mtime_edit(path, b'Service', b'Servixe')
    if os.name == 'nt':
        assert _stat_of(path)[:3] == before[:3], 'lstat alone cannot see this edit (ctime is creation time)'
    assert sync_files.Scan(tmp_path, known).digests('tasks/a-001.md') is None


def test_a_mapped_write_moves_no_timestamp_the_documented_limit(tmp_path):
    path = tmp_path / 'tasks' / 'a-001.md'
    path.parent.mkdir()
    path.write_bytes(b'Service task body\n' * 4)
    age(path)
    scan = sync_files.Scan(tmp_path)
    scan.observe('tasks/a-001.md')
    _mapped_edit(path, b'Service', b'Servixe')
    hit = sync_files.Scan(tmp_path, scan.entries()).digests('tasks/a-001.md')
    if hit is None:
        pytest.skip('this filesystem moves a timestamp on a mapped write')
    assert hit.digest != sync_files.Digests.of(path.read_bytes()).digest  # stale: why generation reads in full


# ── sync: a restored-mtime edit is imported at once; a mapped edit only waits ─

def test_sync_imports_a_restored_mtime_edit_at_once(repo):
    with Coordinator(repo):
        client = client_for(repo)
        _settled(repo, client)
        _restored_mtime_edit(repo / '.taskmaster' / REL, b'Service task', b'Service tusk')
        assert client.sync()['state'] == 'synchronized'
        assert title(repo) == 'Service tusk'


# ── managed Git: generation never trusts a fingerprint ──────────────────────

def test_generation_reads_every_file_even_with_a_warm_cache(repo, monkeypatch):
    seen = []
    original = sync_files.Scan.observe
    monkeypatch.setattr(sync_files.Scan, 'observe',
                        lambda self, rel, **options: seen.append(rel) or original(self, rel, **options))
    with Coordinator(repo) as owner:
        client = client_for(repo)
        _settled(repo, client)
        seen.clear()
        _, files, mismatched = managed_git.generation(owner, 0)
    assert mismatched == [] and sorted(seen) == sorted(files)


def test_a_restored_mtime_edit_is_imported_then_committed_as_the_published_bytes(repo):
    """The fingerprint sees it now: the managed commit's own sync imports the edit, so the
    commit records exactly the generation the store published (the edited bytes)."""
    from native_git_helpers import git
    with Coordinator(repo):
        client = client_for(repo)
        _settled(repo, client)
        _restored_mtime_edit(repo / '.taskmaster' / REL, b'Service task', b'Service tusk')
        result = client.git_run(kind='commit', message='tm: commit the imported edit')
        assert result['state'] == 'completed', result
        assert title(repo) == 'Service tusk'
        committed = git(repo, 'show', f'HEAD:.taskmaster/{REL}').encode()
        on_disk = (repo / '.taskmaster' / REL).read_bytes()
        assert committed.replace(b'\r\n', b'\n') == on_disk.replace(b'\r\n', b'\n')


def test_a_mapped_edit_no_fingerprint_shows_never_reaches_a_managed_commit(repo):
    """The sync cache misses it (only the import waits, until the cache ages out), but
    `generation` reads every file: the commit is refused before Git runs, the edit is kept."""
    from native_git_helpers import git
    with Coordinator(repo) as owner:
        client = client_for(repo)
        _settled(repo, client)
        head = git(repo, 'rev-parse', 'HEAD').strip()
        _mapped_edit(repo / '.taskmaster' / REL, b'Service task', b'Service tusk')
        if sync_files.open_scan(repo, repo / '.taskmaster').digests(REL) is None:
            pytest.skip('this filesystem moves a timestamp on a mapped write')
        result = client.git_run(kind='commit', message='tm: must not commit unpublished bytes')
        assert result['state'] == 'refused', result
        assert REL in result.get('paths', []), result
        assert git(repo, 'rev-parse', 'HEAD').strip() == head and owner.git_pin is None
        assert title(repo) == 'Service task'
        assert b'Service tusk' in (repo / '.taskmaster' / REL).read_bytes()  # the edit is kept, not lost
