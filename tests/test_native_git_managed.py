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
        report = managed.reconcile(owner, marker)
        assert report['state'] == 'ambiguous', report
    finally:
        lock.unlink()
    original = tracked.read_bytes()
    tracked.write_bytes(original + b'partially-unpacked\n')
    try:
        report = managed.reconcile(owner, marker)
        assert report['state'] == 'ambiguous', report
    finally:
        tracked.write_bytes(original)
    assert managed.reconcile(owner, marker)['state'] == 'failed'
    assert git(repo, 'rev-parse', 'HEAD').strip() == head == init_head


def _proven_marker(owner, repo, op_id, **extra):
    """A marker whose boundary is already proven (phase quiesced), for reconcile tests."""
    marker = {'op_id': op_id, 'request': ['t', op_id], 'kind': 'commit', 'phase': 'quiesced',
              'contained': managed.jobs.supported(), 'platform': sys.platform, 'job': None, 'token_hash': 'x',
              'pre': managed.snapshot(repo, managed.repository(repo)), 'generation': {}, 'target': None}
    marker.update(extra)
    return marker


def _recorded(repo):
    from taskmaster.coordinator.git import _blob_id
    content = (repo / REL).read_bytes()
    return {REL.removeprefix('.taskmaster/'): [_blob_id(content)]}


def test_commit_blobs_are_verified_against_recorded_hashes_not_disk(repo):
    """M2: a commit whose projection blobs differ from the recorded generation is ambiguous."""
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        marker = _proven_marker(owner, repo, 'm2', generation={'blobs': {REL.removeprefix('.taskmaster/'): ['0' * 40]}})
        git(repo, 'commit', '-q', '--allow-empty', '-m', 'managed', '--trailer', 'Taskmaster-Op: m2')
        with owner.publication:
            managed.write_state(owner, marker=marker)
        result = client.git_recover()
        assert result['state'] == 'ambiguous', result
        assert owner.git_pin['state'] == 'ambiguous'
        assert client.git_recover(accept_outcome=True)['state'] == 'accepted'


def test_commit_without_this_operation_trailer_is_not_claimed(repo):
    """L6: a concurrent user commit (no op-id trailer) is never recorded as the managed commit."""
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        marker = _proven_marker(owner, repo, 'l6', generation={'blobs': _recorded(repo)})
        git(repo, 'commit', '-q', '--allow-empty', '-m', 'a user commit')
        with owner.publication:
            managed.write_state(owner, marker=marker)
        result = client.git_recover()
        assert result['state'] == 'ambiguous', result
        assert any('Taskmaster-Op' in notice for notice in result['notices']), result
        client.git_recover(accept_outcome=True)


def test_managed_commit_carries_its_operation_trailer(repo):
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        client.execute(request(client, 'trailer', 'Trailer title'))
        result = client.git_run(kind='commit', message='tm: trailer')
        assert result['state'] == 'completed' and result['generation_verified'] is True, result
        assert f"Taskmaster-Op: {result['op_id']}" in git(repo, 'log', '-1', '--format=%B')


def test_user_staged_work_is_left_staged_and_uncommitted(repo):
    """M3: only the generation's paths are committed."""
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        (repo / 'user.txt').write_text('user work\n', encoding='utf-8')
        git(repo, 'add', 'user.txt')
        client.execute(request(client, 'staged', 'Staged title'))
        result = client.git_run(kind='commit', message='tm: only projections')
        assert result['state'] == 'completed', result
        assert 'user.txt' not in git(repo, 'show', '--name-only', '--format=', 'HEAD').split()
        assert 'user.txt' in git(repo, 'diff', '--cached', '--name-only').split()
        assert 'Staged title' in show(repo, 'HEAD')


def test_retry_of_an_older_request_never_reruns_git(repo):
    """M4: settled results are receipted per request, not only the last one."""
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        client.execute(request(client, 'a', 'Title A'))
        first = client.git_run(kind='commit', message='tm: a', request_id='req-a')
        client.execute(request(client, 'b', 'Title B'))
        second = client.git_run(kind='commit', message='tm: b', request_id='req-b')
        assert first['state'] == second['state'] == 'completed'
        count = commit_count(repo)
        client.execute(request(client, 'c', 'Title C'))
        again = client.git_run(kind='commit', message='tm: a', request_id='req-a')
        assert again['replayed'] is True and again['op_id'] == first['op_id'], again
        assert commit_count(repo) == count


def test_retry_matching_the_unsettled_marker_reports_that_operation(repo):
    """M4: the durable marker's own request is answered with its op, not a generic refusal."""
    with Coordinator(repo) as owner:
        marker = _proven_marker(owner, repo, 'pending-op', request=['explicit-git', 'req-p'], phase='launch',
                                contained=False, platform='test-other')
        with owner.publication:
            managed.write_state(owner, marker=marker)
        result = Client(repo, autostart=False, timeout=120).git_run(kind='commit', message='tm', request_id='req-p')
        assert result['state'] == 'recovery_required' and result['op_id'] == 'pending-op', result


def test_operator_can_accept_when_the_job_or_git_state_cannot_be_inspected(repo, monkeypatch):
    """M5: acknowledge_quiescent + accept_outcome settles as `accepted` (unreconciled)."""
    from taskmaster.coordinator import job as jobs
    with Coordinator(repo) as owner:
        marker = _proven_marker(owner, repo, 'm5', phase='launch', contained=True, platform=sys.platform,
                                job=jobs.new_name())
        with owner.publication:
            managed.write_state(owner, marker=marker)
    def denied(name):
        raise jobs.JobUnavailable(5, 'OpenJobObject failed: Access is denied.')
    monkeypatch.setattr(jobs, 'supported', lambda: True)
    monkeypatch.setattr(jobs.Job, 'open', staticmethod(denied))
    monkeypatch.setattr(managed, 'repository', lambda root: (_ for _ in ()).throw(managed.GitRefused('git broken')))
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        assert wait_for(lambda: owner.git_pin is not None and owner.git_pin['state'] == 'recovery_required')
        assert client.git_recover(acknowledge_quiescent=True)['state'] == 'recovery_required'
        result = client.git_recover(acknowledge_quiescent=True, accept_outcome=True)
        assert result['state'] == 'accepted' and result['reconciled'] is False, result
        assert any('unreconciled' in notice for notice in result['notices']), result
        assert owner.git_pin is None and managed.read_state(owner, managed.MARKER_KEY) is None


def test_managed_git_disables_background_maintenance(repo):
    """L4: retirement must not kill an auto gc/maintenance/fsmonitor mid-write."""
    from types import SimpleNamespace
    owner = SimpleNamespace(root=repo)
    for kind, extra in (('commit', {'message': 'm'}), ('checkout', {'ref': 'main'})):
        commands = managed._commands(owner, kind, [], extra.get('message'), extra.get('ref'), 'token', 'op')
        for command in commands:
            argv = command['argv']
            for setting in ('gc.auto=0', 'maintenance.auto=false', 'core.fsmonitor=false'):
                assert argv[argv.index(setting) - 1] == '-c', argv


def test_generation_through_is_the_synchronized_target(repo):
    """L9: a write landing after the barrier must not overstate the captured generation."""
    captured = {}
    owner_box = {}

    def checkpoint(name):
        if name == 'git_marker_written':
            captured['marker'] = managed.read_state(owner_box['owner'], managed.MARKER_KEY)
    with Coordinator(repo, checkpoint=checkpoint) as owner:
        owner_box['owner'] = owner
        client = Client(repo, autostart=False, timeout=120)
        client.execute(request(client, 'gen', 'Generation title'))
        original = owner.sync

        def racing_sync(**arguments):
            result = original(**arguments)
            if arguments['request_id'].endswith(':pre'):
                captured['synced'] = result['through']
                Client(repo, autostart=False, timeout=120).execute(request(client, 'late', 'Late title'))
            return result
        owner.sync = racing_sync
        assert client.git_run(kind='commit', message='tm: gen')['state'] == 'completed'
    assert captured['marker']['generation']['through'] == captured['synced']


def test_uncontained_helper_outcome_needs_acknowledgement(repo, monkeypatch):
    """L8/L10: the real POSIX (uncontained) path runs, but a helper exit is not proof that
    hook descendants stopped: the outcome waits for acknowledge_quiescent."""
    from taskmaster.coordinator import job as jobs
    monkeypatch.setattr(jobs, 'supported', lambda: False)
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        client.execute(request(client, 'posix', 'Posix title'))
        before = commit_count(repo)
        result = client.git_run(kind='commit', message='tm: posix')
        assert result['state'] == 'recovery_required', result
        assert result['outcome_preview']['state'] == 'completed', result
        marker = managed.read_state(owner, managed.MARKER_KEY)
        assert marker['contained'] is False and marker['job'] is None
        assert owner.git_pin['state'] == 'recovery_required'
        assert commit_count(repo) == before + 1
        assert client.git_recover()['state'] == 'recovery_required'
        settled = client.git_recover(acknowledge_quiescent=True)
        assert settled['state'] == 'completed' and settled['generation_verified'] is True, settled
        assert owner.git_pin is None and commit_count(repo) == before + 1


def test_exporter_stays_idle_under_a_startup_pin(repo):
    """L10: the background exporter never publishes while a startup marker is unresolved."""
    with Coordinator(repo) as owner:
        marker = {'op_id': 'held', 'request': ['t', 'held'], 'kind': 'commit', 'phase': 'launch',
                  'contained': False, 'platform': 'test-other', 'job': None, 'token_hash': 'x',
                  'pre': managed.snapshot(repo, managed.repository(repo)), 'generation': {}, 'target': None}
        with owner.publication:
            managed.write_state(owner, marker=marker)
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        assert wait_for(lambda: owner.git_pin is not None and owner.git_pin['state'] == 'recovery_required')
        receipt = client.execute(request(client, 'idle', 'Idle title'))['receipt']
        owner.export_needed.set()
        assert not wait_for(lambda: 'Idle title' in (repo / REL).read_text(encoding='utf-8'), timeout=2)
        assert client.git_recover(acknowledge_quiescent=True)['state'] == 'failed'
        assert owner.flush(receipt['commit_seq'])['state'] == 'exported'
        assert 'Idle title' in (repo / REL).read_text(encoding='utf-8')
