"""User intent: a managed Git commit/checkout captures exactly one coherent
projection generation, refuses unsafe starts, and reports Git failure honestly.
All repositories are disposable temp roots.
"""
from concurrent.futures import ThreadPoolExecutor
import sys

import pytest

from taskmaster.coordinator import git as managed
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.service import Coordinator
from native_git_helpers import (REL, commit_count, git, init_repo, install_hook, pausing_hook, show,
                                wait_for)
from test_native_service import root, request  # noqa: F401

pytestmark = pytest.mark.xdist_group('heavy_processes')


@pytest.fixture
def repo(root):
    init_repo(root)
    return root


def epic_update(client, key, value):
    envelope = request(client, key=key)
    envelope.update(operation='epic.update', arguments={'id': 'test-epic', 'field': 'name', 'value': value})
    return envelope


def test_managed_commit_captures_the_published_generation_and_replays_by_request(repo):
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        client.execute(request(client, 'title', 'Committed title'))
        before = commit_count(repo)
        result = client.git_run(kind='commit', message='tm: projections', request_id='commit-1')
        assert result['state'] == 'completed', result
        assert result['generation_verified'] is True
        assert commit_count(repo) == before + 1
        assert 'Committed title' in show(repo, 'HEAD')
        assert git(repo, 'status', '--porcelain', '--', '.taskmaster').strip() == ''
        status = client.git_status()
        assert status['active'] is None and status['pin'] is None and status['last']['op_id'] == result['op_id']
        # A lost response retried with the same id never commits again.
        again = client.git_run(kind='commit', message='tm: projections', request_id='commit-1')
        assert again['replayed'] is True and again['op_id'] == result['op_id']
        assert commit_count(repo) == before + 1
        assert owner.git_pin is None


def test_writer_racing_git_staging_stays_out_of_the_commit(repo):
    pausing_hook(repo)
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        client.execute(request(client, 'first', 'Generation title'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(client.git_run, kind='commit', message='tm: generation')
            try:
                assert wait_for((repo / 'hook-entered').exists)
                receipt = client.execute(request(client, 'racing', 'Racing title'))['receipt']
                # Committed to the store, but publication is held by managed Git.
                pinned = owner.flush(receipt['commit_seq'], timeout=0.2)
                assert pinned['state'] == 'pending'
                assert any('managed Git operation is in progress' in n for n in pinned['notices']), pinned
                assert 'Racing title' not in (repo / REL).read_text(encoding='utf-8')
            finally:
                (repo / 'hook-release').touch()
            result = running.result(timeout=120)
        assert result['state'] == 'completed', result
        committed = show(repo, 'HEAD')
        assert 'Generation title' in committed and 'Racing title' not in committed
        assert owner.flush(receipt['commit_seq'])['state'] == 'exported'
        assert 'Racing title' in (repo / REL).read_text(encoding='utf-8')


def test_multi_file_generation_is_committed_whole(repo):
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        # Hold the exporter so both effects are unpublished when the commit starts.
        owner.publication.acquire()
        try:
            client.execute(request(client, 'task', 'Task side'))
            client.execute(epic_update(client, 'epic', 'Epic side'))
        finally:
            owner.publication.release()
        result = client.git_run(kind='commit', message='tm: both')
        assert result['state'] == 'completed' and result['generation_verified'], result
        assert 'Task side' in show(repo, 'HEAD')
        files = git(repo, 'ls-tree', '-r', '--name-only', 'HEAD', '--', '.taskmaster').split()
        assert [path for path in files if 'Epic side' in show(repo, 'HEAD', path)], files


def test_git_failure_is_reported_and_releases_the_pin(repo):
    install_hook(repo, 'pre-commit', 'echo refused by test hook >&2\nexit 1')
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        head = git(repo, 'rev-parse', 'HEAD').strip()
        client.execute(request(client, 'title', 'Unlucky title'))
        result = client.git_run(kind='commit', message='tm: refused')
        assert result['state'] == 'failed', result
        assert 'refused by test hook' in result['results'][-1]['stderr']
        assert git(repo, 'rev-parse', 'HEAD').strip() == head
        assert client.git_status()['active'] is None and owner.git_pin is None
        later = client.execute(request(client, 'later', 'After failure'))['receipt']
        assert owner.flush(later['commit_seq'])['state'] == 'exported'


def test_nothing_to_commit_is_a_failed_outcome(repo):
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        assert client.git_run(kind='commit', message='one')['state'] in ('completed', 'failed')
        assert client.git_run(kind='commit', message='two')['state'] == 'failed'


def test_refusals_leave_no_marker(repo):
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        lock = repo / '.git' / 'index.lock'
        lock.write_bytes(b'')
        try:
            result = client.git_run(kind='commit', message='tm')
            assert result['state'] == 'refused' and 'index.lock' in result['locks']
        finally:
            lock.unlink()
        assert client.git_run(kind='checkout', ref='no-such-branch')['state'] == 'refused'
        with pytest.raises(ValueError):
            client.git_run(kind='checkout', ref='--orphan')
        with pytest.raises(ValueError):
            client.git_run(kind='push', message='x')
        assert managed.read_state(owner, managed.MARKER_KEY) is None and owner.git_pin is None


def test_root_that_is_not_the_git_top_level_is_refused(root, tmp_path):
    # The Git top level is a parent of the coordinator root.
    init_repo(root.parent)
    with Coordinator(root):
        result = Client(root, autostart=False, timeout=120).git_run(kind='commit', message='tm')
        assert result['state'] == 'refused' and 'top level' in result['reason']


def test_pin_blocks_every_publication_path(repo):
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        owner.git_pin = {'state': 'recovery_required', 'reason': 'test pin'}
        receipt = client.execute(request(client, 'pinned', 'Pinned title'))['receipt']
        assert 'test pin' in owner.flush(receipt['commit_seq'], timeout=0.5)['notices'][0]
        assert client.sync(import_files=False)['state'] == 'pending'
        assert client.git_run(kind='commit', message='tm')['state'] == 'refused'
        assert 'Pinned title' not in (repo / REL).read_text(encoding='utf-8')
        # No marker exists: explicit recovery clears the stale in-memory pin.
        assert client.git_recover()['state'] == 'clear'
        assert owner.flush(receipt['commit_seq'])['state'] == 'exported'


def _tree(repo, rev):
    return set(git(repo, 'ls-tree', '-r', '--name-only', rev, '--', '.taskmaster').split())


def test_managed_checkout_reports_drift_and_never_rolls_back(repo):
    """H2: older checked-out projections are drift, never authority to roll the store back."""
    from test_native_service_sync import title
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        git(repo, 'branch', 'side')
        client.execute(request(client, 'main-title', 'Main title'))
        note = request(client, 'main-note')
        note.update(operation='note.create', arguments={'text': 'main only note'})
        client.execute(note)
        assert client.git_run(kind='commit', message='tm: main')['state'] == 'completed'
        added = _tree(repo, 'main') - _tree(repo, 'side')
        assert added, 'main must add a projection file side lacks'
        result = client.git_run(kind='checkout', ref='side')
        assert result['state'] == 'completed', result
        assert git(repo, 'symbolic-ref', 'HEAD').strip() == 'refs/heads/side'
        assert 'post_import' not in result
        assert title(repo) == 'Main title', 'the store must not roll back to the checked-out bytes'
        drift = set(result['drift']['paths'])
        assert REL.removeprefix('.taskmaster/') in drift and {p.removeprefix('.taskmaster/') for p in added} <= drift
        # Nothing is written into the checked-out tree: no apply, no repair.
        assert 'Main title' not in (repo / REL).read_text(encoding='utf-8')
        assert not any((repo / path).exists() for path in added)
        synced = client.sync()
        assert synced['state'] == 'pending', synced
        assert REL.removeprefix('.taskmaster/') in synced['unresolved']
        assert title(repo) == 'Main title'
        assert not any((repo / path).exists() for path in added)
        # A later write to a drifted entity stays unpublished until resolution.
        later = client.execute(request(client, 'later', 'Later title'))['receipt']
        assert owner.flush(later['commit_seq'], timeout=1)['state'] == 'pending'
        assert 'Later title' not in (repo / REL).read_text(encoding='utf-8')
        assert client.git_status()['drift']['files']
        # Managed Git refuses until the drift is resolved.
        assert client.git_run(kind='commit', message='tm: blocked')['state'] == 'refused'
        # Restoring the published generation resolves it; publication resumes.
        git(repo, 'checkout', '-q', 'main')
        assert client.sync()['state'] == 'synchronized'
        assert client.git_status()['drift'] is None
        assert owner.flush(later['commit_seq'])['state'] == 'exported'
        assert 'Later title' in (repo / REL).read_text(encoding='utf-8')
        assert owner.git_pin is None


def test_checkout_drift_can_be_released_explicitly(repo):
    """H2: an operator may release drift; only then does ordinary sync treat files normally."""
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        git(repo, 'branch', 'side')
        client.execute(request(client, 'main-title', 'Main title'))
        assert client.git_run(kind='commit', message='tm: main')['state'] == 'completed'
        assert client.git_run(kind='checkout', ref='side')['drift']['paths']
        released = client.git_recover(release_drift=True)
        assert released['state'] == 'clear' and released['released_drift'], released
        assert client.git_status()['drift'] is None


@pytest.mark.skipif(sys.platform != 'win32', reason='checks the Windows job path')
def test_status_reports_containment(repo):
    with Coordinator(repo):
        assert Client(repo, autostart=False).git_status()['contained'] is True


def test_non_windows_interruption_stays_a_recovery_requirement(repo, monkeypatch):
    """Without a verified boundary a launched marker is never cleared by restart."""
    with Coordinator(repo) as owner:
        marker = {'op_id': 'posix-op', 'request': ['t', 'r'], 'kind': 'commit', 'phase': 'launch',
                  'contained': False, 'platform': 'linux', 'job': None, 'token_hash': 'x',
                  'pre': {'head': None, 'ref': None, 'index': None, 'locks': []}, 'generation': {}, 'target': None}
        with owner.publication:
            managed.write_state(owner, marker=marker)
    for _ in range(2):  # ordinary restarts
        with Coordinator(repo) as owner:
            assert wait_for(lambda: owner.git_pin is not None and owner.git_pin['state'] == 'recovery_required')
            assert 'no verified child-lifetime boundary' in owner.git_pin['reason']
            assert managed.read_state(owner, managed.MARKER_KEY)['op_id'] == 'posix-op'
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        wait_for(lambda: owner.git_pin is not None and owner.git_pin['state'] == 'recovery_required')
        result = client.git_recover(acknowledge_quiescent=True)
        assert result['state'] in ('failed', 'ambiguous'), result
        assert any('acknowledged by operator' in notice for notice in result.get('notices', []))


def test_checkout_with_leftover_index_lock_or_worktree_change_is_ambiguous(repo):
    """H3: a checkout killed mid-unpack leaves HEAD/index unchanged but the tree partly
    switched; that is never a clean `failed`."""
    from types import SimpleNamespace
    head = init_head = git(repo, 'rev-parse', 'HEAD').strip()
    tracked = repo / '.gitignore'
    owner = SimpleNamespace(root=repo)
    pre = managed.snapshot(repo, managed.repository(repo))
    marker = {'op_id': 'co', 'request': ['t', 'r'], 'kind': 'checkout', 'pre': pre,
              'target': {'ref': 'side', 'commit': '1' * 40, 'branch': 'refs/heads/side'}}
    lock = repo / '.git' / 'index.lock'
    lock.write_bytes(b'')
    try:
        report = managed.reconcile(owner, marker, [])
        assert report['state'] == 'ambiguous', report
    finally:
        lock.unlink()
    original = tracked.read_bytes()
    tracked.write_bytes(original + b'partially-unpacked\n')
    try:
        report = managed.reconcile(owner, marker, [])
        assert report['state'] == 'ambiguous', report
    finally:
        tracked.write_bytes(original)
    assert managed.reconcile(owner, marker, [])['state'] == 'failed'
    assert git(repo, 'rev-parse', 'HEAD').strip() == head == init_head
