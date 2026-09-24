# User intent: regressions from the independent review of the N13 D1 stat-fingerprint fast path:
# a cached fingerprint may only ever skip work whose outcome it proves, never hide a Git rewrite,
# never vouch for a managed commit, never persist a blind spot forever, and a corrupt cache or
# an old client's missing budget must not break sync. Disposable temp repositories only.
import json
import os
import subprocess
import sys
import time

import pytest

from taskmaster.coordinator import git as managed
from taskmaster.coordinator import sync_files
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.service import Coordinator
from native_git_helpers import git
from test_native_service_sync import title
from test_native_sync_perf import REL, age, age_projection, repo, settle, write  # noqa: F401

pytestmark = pytest.mark.xdist_group('heavy_processes')


def client_for(repo):
    return Client(repo, autostart=False, timeout=120)


def _rewrite_mapped(path, old, new):
    """Same size, written through a memory mapping: no timestamp moves (measured on NTFS)."""
    import mmap
    raw = path.read_bytes()
    at = raw.index(old)
    assert len(old) == len(new) and old != new
    with path.open('r+b') as handle, mmap.mmap(handle.fileno(), 0) as mapped:
        mapped[at:at + len(new)] = new
        mapped.flush()


# ── 1: detect judges cached digests after HEAD is observed ─────────────────

def test_git_rewrite_between_discovery_and_detection_is_never_imported(repo, monkeypatch):
    """Discovery lstats every file; a `git checkout -m` landing before detection observes the
    new HEAD must not be judged by discovery's lstat (and the observation must not advance
    past a file the completion check could not confirm)."""
    path = repo / '.taskmaster' / REL
    with Coordinator(repo):
        client = client_for(repo)
        settle(repo, client)
        home = git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
        git(repo, 'checkout', '-q', '-b', 'other')
        path.write_bytes(path.read_bytes().replace(b'title: Service task', b'title: Other branch title'))
        git(repo, 'commit', '-q', '-am', 'other branch title')
        git(repo, 'checkout', '-q', home)
        age_projection(repo)
        for _ in range(2):
            assert client.sync()['state'] == 'synchronized'  # warm: every fingerprint recorded
        original, fired = sync_files.discover, []

        def racing(root, scan=None):
            inventory = original(root, scan)
            if not fired:
                fired.append(True)
                # Another process (the user's editor): coordinator threads may not write projections.
                subprocess.run([sys.executable, '-c', 'import sys; open(sys.argv[1], "ab").write(b"\\nA line typed '
                                'just before switching.\\n")', str(path)], check=True)
                git(repo, 'checkout', '-q', '-m', 'other')  # merged bytes no commit holds
            return inventory
        monkeypatch.setattr(sync_files, 'discover', racing)
        first = client.sync()
        second = client.sync()
        assert title(repo) == 'Service task', 'merged foreign-branch bytes were imported as authored'
        assert first['state'] == 'pending' and second['state'] == 'pending', (first, second)
        assert REL in second['unresolved']


# ── 2: managed Git's generation reads in full; the cache has a bounded age ──

def test_managed_commit_never_trusts_a_fingerprint_for_the_generation(repo):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        settle(repo, client)
        _rewrite_mapped(repo / '.taskmaster' / REL, b'Service task', b'Service tusk')
        result = client.git_run(kind='commit', message='tm: must not commit unverified bytes')
        assert result['state'] == 'refused', result
        assert REL in result.get('paths', []), result
        assert owner.git_pin is None


def test_a_change_no_fingerprint_shows_is_read_once_the_cache_ages_out(repo, monkeypatch):
    with Coordinator(repo):
        client = client_for(repo)
        settle(repo, client)
        _rewrite_mapped(repo / '.taskmaster' / REL, b'Service task', b'Service tusk')
        assert client.sync()['state'] == 'synchronized'
        assert title(repo) == 'Service task'  # the documented blind spot, within the TTL
        monkeypatch.setattr(sync_files, 'CACHE_TTL', 0, raising=False)
        result = client.sync()
        assert result['state'] == 'synchronized', result
        assert title(repo) == 'Service tusk'


# ── 3: only trustworthy fingerprints are recorded ─────────────────────────

def _observation(tmp_path, fingerprint):
    path = write(tmp_path, 'tasks/a-001.md', b'bytes\n')
    age(path)
    observed = sync_files.Scan(tmp_path).observe('tasks/a-001.md')
    return observed.__class__(**{**observed.__dict__, 'fingerprint': fingerprint(observed.fingerprint)})


@pytest.mark.parametrize('broken', [
    lambda f: (f[0], 0, f[2], f[3], f[4]),                                  # no stable file id
    lambda f: (f[0], f[1], f[2], 0, f[4]),                                  # no mtime
    lambda f: (f[0], f[1], f[2], time.time_ns() + 3_600_000_000_000, f[4]),  # clock skew: future
], ids=['inode-0', 'mtime-0', 'future-mtime'])
def test_untrustworthy_fingerprints_are_never_recorded(tmp_path, broken):
    observed = _observation(tmp_path, broken)
    scan = sync_files.Scan(tmp_path)
    scan.record(observed)
    assert scan.entries() == {}


def test_network_volumes_are_never_cached(tmp_path, monkeypatch):
    path = write(tmp_path, 'tasks/a-001.md', b'bytes\n')
    age(path)
    monkeypatch.setattr(sync_files, '_remote', lambda _path: True, raising=False)
    scan = sync_files.open_scan(tmp_path, tmp_path)
    scan.observe('tasks/a-001.md')
    assert scan.entries() == {}


# ── 4: a corrupt cache is only a miss ───────────────────────────────────────

def test_deeply_nested_cache_file_is_an_empty_cache(tmp_path):
    cache = sync_files.cache_path(tmp_path)
    cache.parent.mkdir(parents=True)
    cache.write_text('[' * 200_000 + ']' * 200_000, encoding='utf-8')
    scan = sync_files.open_scan(tmp_path, tmp_path / '.taskmaster')
    assert scan.known == {}
    cache.write_text(json.dumps({'version': sync_files.CACHE_VERSION, 'checkouts': {'x': [1, 2]}}),
                     encoding='utf-8')
    assert sync_files.open_scan(tmp_path, tmp_path / '.taskmaster').known == {}


# ── 5: a request without a budget keeps the budget its client was built for ──

def test_absent_budget_fits_an_old_clients_read_timeout(repo, monkeypatch):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        seen = []
        original = owner.sync

        def spy(**arguments):
            seen.append(arguments['timeout'])
            return original(**arguments)
        owner.sync = spy
        runs = []
        monkeypatch.setattr(managed, 'run', lambda owner_, **arguments: runs.append(arguments['sync_timeout'])
                            or {'state': 'refused', 'reason': 'spy'})
        # A client from before budgets were sent names none (and waits 30 s).
        client.call('sync', caller_scope='old-client', request_id='r1', import_files=True, through=0, files=None,
                    take_file=False)
        client.call('git_run', kind='commit', message='m', ref=None, caller_scope='old-client', request_id='g1')
        client.sync()
        client.git_run(kind='commit', message='m')
    from taskmaster.coordinator.protocol import REPLY_MARGIN, SYNC_TIMEOUT
    assert seen == [20, SYNC_TIMEOUT] and runs == [20, SYNC_TIMEOUT], (seen, runs)
    assert 20 < 30 < SYNC_TIMEOUT + REPLY_MARGIN
