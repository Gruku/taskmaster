"""User intent: full sync, resync and managed Git must finish at CodeMaestro scale (N13 D1)
without weakening link refusal, rechecks or classified-digest handoff. The guards here count
file reads and realpath calls per sync on a synthetic store (never wall-clock), and pin the
exact limits of the stat fast path.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import time

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import checkouts, sync_files
from taskmaster.coordinator import git as managed
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.service import Coordinator
from native_git_helpers import git, init_repo
from native_twins import make_twins
from test_native_service_sync import title

pytestmark = pytest.mark.xdist_group('heavy_processes')
TASKS = 24
REL = 'tasks/test-epic-001.md'


def write(root, rel, content=b'authored\n'):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def age(path, seconds=120):
    """Older than the racy window: a stat fingerprint may be recorded for it."""
    past = time.time_ns() - seconds * 1_000_000_000
    os.utime(path, ns=(past, past))


def age_projection(root):
    for path in (Path(root) / '.taskmaster').rglob('*'):
        if path.is_file() and 'local' not in path.relative_to(root / '.taskmaster').parts:
            age(path)


@contextmanager
def counting(monkeypatch):
    """Counts content reads (every projection read path) and realpath calls, and the
    realpath calls made for a file (`file_realpath`) rather than a directory."""
    counts = {'reads': 0, 'realpath': 0, 'file_realpath': 0}

    def counted(module, name, key):
        original = getattr(module, name)

        def wrapper(*args, **kwargs):
            counts[key] += 1
            if key == 'realpath' and str(args[0]).endswith(('.md', '.yaml')):
                counts['file_realpath'] += 1
            return original(*args, **kwargs)
        monkeypatch.setattr(module, name, wrapper)
    counted(sync_files, '_read_observed', 'reads')
    counted(checkouts, 'read', 'reads')
    counted(managed, '_read_projection', 'reads')
    counted(os.path, 'realpath', 'realpath')
    yield counts


# ── Scan: the per-sync view of one projection directory ──────────────────────

def test_stat_hit_reuses_digests_only_for_the_same_file_identity(tmp_path, monkeypatch):
    path = write(tmp_path, 'tasks/a-001.md', b'first version\n')
    age(path)
    first = sync_files.Scan(tmp_path)
    observed = first.observe('tasks/a-001.md')
    assert observed.content == b'first version\n'
    known = first.entries()
    assert 'tasks/a-001.md' in known
    with counting(monkeypatch) as counts:
        again = sync_files.Scan(tmp_path, known)
        digests = again.digests('tasks/a-001.md')
    assert digests is not None and digests.digest == observed.digest
    assert counts['reads'] == 0
    # Another file with the same size and timestamps (a copy with preserved times) is a
    # different file identity: never a hit.
    other = write(tmp_path, 'other.md', b'other version\n')
    stat = path.stat()
    os.utime(other, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    os.replace(other, path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert sync_files.Scan(tmp_path, known).digests('tasks/a-001.md') is None


def test_recently_written_file_is_never_recorded(tmp_path):
    write(tmp_path, 'tasks/a-001.md', b'just written\n')
    scan = sync_files.Scan(tmp_path)
    assert scan.observe('tasks/a-001.md') is not None
    assert 'tasks/a-001.md' not in scan.entries(), 'a same-tick edit could keep this fingerprint'


def test_link_refusal_still_applies_to_a_cached_file(tmp_path, monkeypatch):
    path = write(tmp_path, 'tasks/a-001.md')
    age(path)
    first = sync_files.Scan(tmp_path)
    first.observe('tasks/a-001.md')
    known = first.entries()
    original = sync_files._check_component

    def refuse(candidate):
        if Path(candidate).name == 'a-001.md':
            raise sync_files.UnsafePath('linked/reparse projection path refused: test')
        return original(candidate)
    monkeypatch.setattr(sync_files, '_check_component', refuse)
    with pytest.raises(sync_files.UnsafePath):
        sync_files.Scan(tmp_path, known).digests('tasks/a-001.md')
    inventory = sync_files.discover(tmp_path)
    assert 'tasks/a-001.md' in inventory.refused


def test_directory_safety_is_checked_once_per_directory(tmp_path, monkeypatch):
    def realpaths(root, files):
        for index in range(files):
            write(root, f'tasks/a-{index:03}.md')
        with counting(monkeypatch) as counts:
            inventory = sync_files.discover(root)
        assert len(inventory.files) == files
        return counts['realpath']
    assert realpaths(tmp_path / 'one', 1) == realpaths(tmp_path / 'many', 12)  # per directory, not per file


# ── Full sync, resync and managed Git on a synthetic N-file store ─────────────

@pytest.fixture
def repo(tmp_path, monkeypatch):
    def seed():
        bs.backlog_add_task(title='Service task', epic='test-epic', phase='dev')
        for index in range(TASKS - 1):
            bs.backlog_add_task(title=f'Bulk task {index}', epic='test-epic', phase='dev')
    twins = make_twins(tmp_path, monkeypatch, seed, visibility=None)
    with twins.at(twins.native):
        init_repo(twins.native)
        yield twins.native


def settle(repo, client):
    assert client.sync()['state'] == 'synchronized'
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '--allow-empty', '-m', 'published generation')
    age_projection(repo)
    warmed = client.sync()  # reads every file once and records fingerprints
    assert warmed['state'] == 'synchronized', warmed


def test_warm_no_edit_full_sync_reads_no_file_and_resolves_per_directory(repo, monkeypatch):
    files = [path for path in (repo / '.taskmaster/tasks').glob('*.md')]
    assert len(files) >= TASKS
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        settle(repo, client)
        with counting(monkeypatch) as counts:
            result = client.sync()
            assert result['state'] == 'synchronized', result
            synced = dict(counts)
            _, _, mismatched = managed.generation(owner, result['through'])
        assert not mismatched
        assert synced['reads'] == 0, synced
        assert synced['file_realpath'] == 0, synced  # directories once per sync, never files
        assert counts['reads'] == 0, 'the managed-Git generation check reuses fingerprints too'


def test_edit_after_warm_sync_is_imported(repo):
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        settle(repo, client)
        path = repo / '.taskmaster' / REL
        path.write_bytes(path.read_bytes().replace(b'Service task', b'Edited after warm sync'))
        result = client.sync()
        assert result['state'] == 'synchronized', result
        assert title(repo) == 'Edited after warm sync'


def test_named_resync_reads_even_when_the_fingerprint_matches(repo):
    """A deliberate in-place rewrite that restores size and mtime is invisible to the
    full-sync fast path; a named resync always reads, so it is the explicit escape."""
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        settle(repo, client)
        path = repo / '.taskmaster' / REL
        before = path.stat()
        raw = path.read_bytes()
        changed = raw.replace(b'Service task', b'Service tusk')
        assert len(changed) == len(raw)
        with path.open('r+b') as handle:
            handle.write(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        result = client.sync(files=[REL])
        assert result['state'] == 'synchronized', result
        assert title(repo) == 'Service tusk'


def test_warm_linked_sync_reads_no_file(repo, monkeypatch):
    linked = repo.parent / 'linked'
    git(repo, 'worktree', 'add', '-q', '-b', 'feature', str(linked))
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        settle(repo, client)
        first = client.sync(worktree=linked)
        assert first['state'] == 'synchronized', first
        age_projection(linked)
        assert client.sync(worktree=linked)['state'] == 'synchronized'
        with counting(monkeypatch) as counts:
            result = client.sync(worktree=linked)
        assert result['state'] == 'synchronized', result
        assert counts['reads'] == 0, counts


def test_first_linked_sync_reads_each_file_once(repo, monkeypatch):
    """Establishing a fresh worktree's bases: detect reads each file, and prepare's
    `establish` and publication's base record reuse those digests (the recorded base
    bytes are the trusted published bytes, equal by digest)."""
    from contextlib import closing
    from taskmaster.native import checkouts as store
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        settle(repo, client)
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '--allow-empty', '-m', 'generation for the worktree')
        linked = repo.parent / 'linked'
        git(repo, 'worktree', 'add', '-q', '-b', 'feature', str(linked))
        age_projection(linked)
        files = len(sync_files.discover(linked / '.taskmaster').files)
        with counting(monkeypatch) as counts:
            result = client.sync(worktree=linked)
        assert result['state'] == 'synchronized', result
        assert counts['reads'] <= files, (counts, files)
        with closing(owner._connect(readonly=True)) as connection:
            assert len(store.bases(connection, result['checkout']['id'])) >= files
