"""User intent: prove that when the coordinator dies mid managed-Git, a replacement
cannot publish until the recorded job is retired and empty, and that it settles
the outcome from Git state without ever replaying the commit.

Only the test-owned coordinator process is killed (through its own Popen handle).
The Git helper and hook are left alive for the replacement to retire.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys

import pytest

from taskmaster.coordinator import git as managed
from taskmaster.coordinator import job as jobs
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.protocol import ServiceUnavailable
from taskmaster.coordinator.service import Coordinator
from native_git_helpers import REL, commit_count, git, gone, init_repo, install_hook, pausing_hook, wait_for
from test_native_service import root, request  # noqa: F401
from test_native_service_process import ready

pytestmark = [pytest.mark.skipif(sys.platform != 'win32', reason='Windows job boundary'),
              pytest.mark.xdist_group('heavy_processes')]

SCRIPT = """
import sys, threading, time
from pathlib import Path
from taskmaster.coordinator.service import Coordinator
root, stage = Path(sys.argv[1]), sys.argv[2]
def checkpoint(name):
    if name == stage:
        (root / f'reached-{name}').touch()
        threading.Event().wait()
with Coordinator(root, checkpoint=checkpoint):
    while True:
        time.sleep(0.1)
"""


def launch(root, stage):
    repo = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=str(repo), TASKMASTER_ROOT=str(root))
    return subprocess.Popen([sys.executable, '-c', SCRIPT, str(root), stage], cwd=repo, env=environment,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def retire_leftover(root):
    """Cleanup through the recorded private job only, never a PID."""
    from contextlib import closing
    import json
    from taskmaster.coordinator.protocol import connect
    with closing(connect(root, readonly=True)) as connection:
        row = connection.execute('SELECT value_json FROM sync_state WHERE key=?', (managed.MARKER_KEY,)).fetchone()
    if row and json.loads(row[0]).get('job'):
        leftover = jobs.Job.open(json.loads(row[0])['job'])
        if leftover is not None:
            with leftover:
                leftover.retire(10)


@pytest.mark.parametrize('window', ['pre_assignment', 'pre_permission', 'active_git', 'completed_pre_receipt'])
def test_replacement_cannot_publish_before_the_job_is_proven_empty(root, window):
    init_repo(root)
    stage = {'pre_assignment': 'git_helper_started', 'pre_permission': 'git_permit',
             'active_git': 'never', 'completed_pre_receipt': 'git_helper_done'}[window]
    if window == 'active_git':
        pausing_hook(root)
    else:
        install_hook(root, 'pre-commit', f'touch "{root.as_posix()}/hook-entered"\nexit 0')
    before = commit_count(root)
    head = git(root, 'rev-parse', 'HEAD').strip()
    first = launch(root, stage)
    replacement = None
    try:
        client = ready(root)
        client.execute(request(client, 'generation', 'Generation title'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(Client(root, autostart=False, timeout=120).git_run,
                                  kind='commit', message='tm: crash window')
            reached = (root / 'hook-entered') if window == 'active_git' else (root / f'reached-{stage}')
            assert wait_for(reached.exists), f'{window} window never reached'
            # A domain write during the operation: durable, never published by the dying owner.
            racing = client.execute(request(client, 'racing', 'Racing title'))['receipt']
            first.kill()  # only this test-created coordinator handle
            first.wait(timeout=10)
            with pytest.raises(ServiceUnavailable):
                running.result(timeout=60)
        marker = None
        from contextlib import closing
        import json
        from taskmaster.coordinator.protocol import connect
        with closing(connect(root, readonly=True)) as connection:
            marker = json.loads(connection.execute('SELECT value_json FROM sync_state WHERE key=?',
                                                   (managed.MARKER_KEY,)).fetchone()[0])
        assert marker['phase'] == ('prepared' if window == 'pre_assignment' else 'launch')
        seen = {}

        def checkpoint(name):
            if name == 'git_recovery_retiring':
                observer = jobs.Job.open(marker['job'])
                with observer:
                    seen['active_before'] = observer.active_processes()
                seen['pin_before'] = replacement.git_pin
                seen['published_before'] = 'Racing title' in (root / REL).read_text(encoding='utf-8')
                # Same thread holds publication; every publication path must still refuse.
                seen['flush_before'] = replacement.flush(racing['commit_seq'], timeout=0)
            elif name == 'git_recovery_quiesced':
                observer = jobs.Job.open(marker['job'])
                with observer:
                    seen['active_after'] = observer.active_processes()
                seen['published_at_quiesce'] = 'Racing title' in (root / REL).read_text(encoding='utf-8')
        replacement = Coordinator(root, checkpoint=checkpoint)
        assert replacement.git_pin is None
        replacement.start()
        assert wait_for(lambda: replacement.git_pin is None, timeout=60), replacement.git_pin
        if window != 'pre_assignment' or 'active_before' in seen:
            assert seen['pin_before'] is not None
            assert seen['published_before'] is False and seen['published_at_quiesce'] is False
            assert seen['flush_before']['state'] == 'pending'
            assert seen['active_after'] == 0
        if window in ('pre_permission', 'active_git', 'completed_pre_receipt'):
            assert seen['active_before'] >= 1, 'the helper held its job after coordinator death'
        if window == 'active_git':
            assert seen['active_before'] >= 3  # helper, git and the paused hook shell
        last = managed.read_state(replacement, managed.LAST_KEY)
        assert last['recovered'] is True and managed.read_state(replacement, managed.MARKER_KEY) is None
        if window == 'completed_pre_receipt':
            assert last['state'] == 'completed' and last['generation_verified'] is True
            assert commit_count(root) == before + 1
        else:
            assert last['state'] == 'failed', last
            assert git(root, 'rev-parse', 'HEAD').strip() == head and commit_count(root) == before
        if window in ('pre_assignment', 'pre_permission'):
            assert not (root / 'hook-entered').exists(), 'Git ran without permission'
        # Only now may the pending domain write publish.
        assert replacement.flush(racing['commit_seq'])['state'] == 'exported'
        assert 'Racing title' in (root / REL).read_text(encoding='utf-8')
        assert gone(marker['job'])
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=10)
        if replacement is not None and replacement.server is not None:
            replacement.close()
        retire_leftover(root)
        (root / 'hook-release').touch()


def test_launched_marker_with_missing_job_stays_pinned_until_acknowledged(root):
    init_repo(root)
    with Coordinator(root) as owner:
        head = git(root, 'rev-parse', 'HEAD').strip()
        snapshot = managed.snapshot(root, managed.repository(root))
        marker = {'op_id': 'gone-job', 'request': ['t', 'r'], 'kind': 'commit', 'phase': 'launch',
                  'contained': True, 'platform': sys.platform, 'job': jobs.new_name(), 'token_hash': 'x',
                  'pre': snapshot, 'generation': {}, 'target': None}
        with owner.publication:
            managed.write_state(owner, marker=marker)
    with Coordinator(root) as owner:
        assert wait_for(lambda: owner.git_pin and owner.git_pin['state'] == 'recovery_required')
        assert 'may have been launched' in owner.git_pin['reason']
        client = Client(root, autostart=False, timeout=60)
        assert client.git_recover()['state'] == 'recovery_required'
        result = client.git_recover(acknowledge_quiescent=True)
        assert result['state'] == 'failed' and owner.git_pin is None
        assert git(root, 'rev-parse', 'HEAD').strip() == head


def test_prepared_marker_with_missing_job_is_proven_not_launched(root):
    init_repo(root)
    with Coordinator(root) as owner:
        snapshot = managed.snapshot(root, managed.repository(root))
        marker = {'op_id': 'never-permitted', 'request': ['t', 'r'], 'kind': 'commit', 'phase': 'prepared',
                  'contained': True, 'platform': sys.platform, 'job': jobs.new_name(), 'token_hash': 'x',
                  'pre': snapshot, 'generation': {}, 'target': None}
        with owner.publication:
            managed.write_state(owner, marker=marker)
    # M1: the user commits meanwhile; the op never ran, so that commit is not its outcome.
    git(root, 'commit', '-q', '--allow-empty', '-m', 'user commit meanwhile')
    with Coordinator(root) as owner:
        assert wait_for(lambda: owner.git_pin is None)
        last = managed.read_state(owner, managed.LAST_KEY)
        assert last['state'] == 'failed' and 'commit' not in last, last
        assert any('not launched' in notice for notice in last['notices']), last


def test_ambiguous_outcome_stays_pinned_until_accepted(root):
    init_repo(root)
    with Coordinator(root) as owner:
        snapshot = managed.snapshot(root, managed.repository(root))
        # HEAD will not match: pretend the operation started from another commit.
        snapshot['head'] = '0' * 40
        marker = {'op_id': 'odd', 'request': ['t', 'r'], 'kind': 'commit', 'phase': 'quiesced',
                  'contained': True, 'platform': sys.platform, 'job': jobs.new_name(), 'token_hash': 'x',
                  'pre': snapshot, 'generation': {}, 'target': None}
        with owner.publication:
            managed.write_state(owner, marker=marker)
    for _ in range(2):
        with Coordinator(root) as owner:
            assert wait_for(lambda: owner.git_pin and owner.git_pin['state'] == 'ambiguous')
    with Coordinator(root) as owner:
        assert wait_for(lambda: owner.git_pin and owner.git_pin['state'] == 'ambiguous')
        result = Client(root, autostart=False).git_recover(accept_outcome=True)
        assert result['state'] == 'accepted' and owner.git_pin is None


def test_checkout_killed_mid_unpack_is_ambiguous_and_stays_pinned(root):
    """H3: a checkout retired while unpacking (a paused smudge filter holds index.lock)
    is reconciled as ambiguous, never `failed` with the pin released."""
    init_repo(root)
    base = root.as_posix()
    git(root, 'checkout', '-q', '-b', 'side')
    (root / '.gitattributes').write_text('slow.txt filter=slow\n', encoding='utf-8')
    (root / 'slow.txt').write_text('slow\n', encoding='utf-8')
    (root / 'plain.txt').write_text('plain\n', encoding='utf-8')
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'side files')
    git(root, 'checkout', '-q', 'main')
    git(root, 'config', 'filter.slow.smudge',
        f'touch "{base}/smudge-entered"; while [ ! -f "{base}/hook-release" ]; do sleep 0.05; done; cat')
    head = git(root, 'rev-parse', 'HEAD').strip()
    first = launch(root, 'never')
    replacement = None
    try:
        client = ready(root)
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(Client(root, autostart=False, timeout=120).git_run, kind='checkout', ref='side')
            assert wait_for((root / 'smudge-entered').exists), 'checkout never reached the smudge filter'
            assert (root / '.git' / 'index.lock').exists()
            first.kill()
            first.wait(timeout=10)
            with pytest.raises(ServiceUnavailable):
                running.result(timeout=60)
        replacement = Coordinator(root)
        replacement.start()
        assert wait_for(lambda: replacement.git_pin is not None and replacement.git_pin['state'] == 'ambiguous',
                        timeout=60), replacement.git_pin
        assert git(root, 'rev-parse', 'HEAD').strip() == head
        marker = managed.read_state(replacement, managed.MARKER_KEY)
        assert marker['outcome']['state'] == 'ambiguous'
        (root / '.git' / 'index.lock').unlink()
        assert Client(root, autostart=False).git_recover(accept_outcome=True)['state'] == 'accepted'
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=10)
        if replacement is not None and replacement.server is not None:
            replacement.close()
        retire_leftover(root)
        (root / 'hook-release').touch()
