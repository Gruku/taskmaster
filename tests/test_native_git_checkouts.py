"""User intent: linked worktrees share the main checkout's store with their own bases, and
Git operations that bypass the coordinator are detected as drift or reported on the next
sync, never imported as authority to roll the store back (N13 steps 9-10).
All repositories and worktrees are disposable temp directories.
"""
from contextlib import closing
import shutil
import sys

import pytest

from taskmaster.coordinator import checkouts
from taskmaster.coordinator import git as managed
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.git import GitRefused
from taskmaster.coordinator.service import Coordinator
from taskmaster.native import checkouts as store
from native_git_helpers import REL, commit_count, git, init_repo, show
from test_native_service import root, request  # noqa: F401
from test_native_service_sync import title

pytestmark = pytest.mark.xdist_group('heavy_processes')
FILE = REL.removeprefix('.taskmaster/')


@pytest.fixture
def repo(root):
    init_repo(root)
    return root


@pytest.fixture
def linked(repo):
    path = repo.parent / 'linked'
    git(repo, 'worktree', 'add', '-q', '-b', 'feature', str(path))
    return path


def client_for(repo):
    return Client(repo, autostart=False, timeout=120)


def text(path):
    return (path / REL).read_text(encoding='utf-8')


def replace_title(path, old, new):
    target = path / REL
    target.write_bytes(target.read_bytes().replace(old.encode(), new.encode()))


# ── Identity ───────────────────────────────────────────────────────────────

def test_checkout_identity_is_git_admin_dir_not_a_path(repo, linked, tmp_path):
    main = checkouts.resolve(repo)
    assert main.id == 'main' and not main.linked
    assert checkouts.resolve(repo, str(repo)).id == 'main'
    first = checkouts.resolve(repo, str(linked))
    assert first.linked and first.id.startswith('wt-') and first.root == linked.resolve()
    assert checkouts.resolve(repo, str(linked)).id == first.id  # stable
    # Removed and recreated at the same path under the same name: a new checkout.
    git(repo, 'worktree', 'remove', str(linked))
    git(repo, 'worktree', 'add', '-q', str(linked), 'feature')
    assert checkouts.resolve(repo, str(linked)).id != first.id
    # A subdirectory is not a checkout top level.
    with pytest.raises(GitRefused, match='top level'):
        checkouts.resolve(repo, str(linked / '.taskmaster'))


def test_common_root_mismatch_is_refused(repo, tmp_path):
    other = tmp_path / 'other-repo'
    other.mkdir()
    init_repo(other)
    with pytest.raises(GitRefused, match='common-root mismatch'):
        checkouts.resolve(repo, str(other))
    with Coordinator(repo):
        client = client_for(repo)
        with pytest.raises(ValueError, match='common-root mismatch'):
            client.sync(worktree=other)
        refused = client.git_run(kind='commit', message='tm', worktree=other)
        assert refused['state'] == 'refused' and 'common-root mismatch' in refused['reason']


# ── Linked checkout sync and publication ───────────────────────────────────

def _adopt(client, linked):
    """First sync of a fresh worktree: Git-provided older bytes are drift until released."""
    first = client.sync(worktree=linked)
    if first['state'] != 'synchronized':
        released = client.git_recover(release_drift='take_published', worktree=linked)
        assert released['state'] == 'clear', released
        first = client.sync(worktree=linked)
    assert first["state"] == "synchronized", (first.get("notices"), first.get("unresolved"), first.get("warnings"))
    return first


def test_linked_sync_establishes_bases_and_receives_the_generation(repo, linked):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        unchanged = client.sync(worktree=linked)
        assert unchanged['state'] == 'synchronized', unchanged
        assert unchanged['checkout']['linked'] is True
        with closing(owner._connect(readonly=True)) as connection:
            assert FILE in store.bases(connection, unchanged['checkout']['id'])  # identical bytes established it
        client.execute(request(client, 'main-title', 'Published title'))
        assert client.sync()['state'] == 'synchronized'
        assert 'Published title' in text(repo) and 'Service task' in text(linked)
        result = client.sync(worktree=linked)
        assert result['state'] == 'synchronized', result
        assert 'Published title' in text(linked)
        assert owner.git_pin is None


def test_fresh_worktree_with_older_committed_bytes_is_drift_until_released(repo, linked):
    with Coordinator(repo):
        client = client_for(repo)
        client.execute(request(client, 'newer', 'Newer title'))
        client.sync()
        git(repo, 'worktree', 'remove', str(linked))
        git(repo, 'worktree', 'add', '-q', str(linked), 'feature')  # checks out the older committed bytes
        pending = client.sync(worktree=linked)
        assert pending['state'] == 'pending' and FILE in pending['unresolved'], pending
        assert 'Service task' in text(linked), 'a Git-provided file is never overwritten unasked'
        assert title(repo) == 'Newer title', 'older checked-out bytes never roll the store back'
        released = client.git_recover(release_drift='take_published', worktree=linked)
        assert FILE in released['released_drift'], released
        assert client.sync(worktree=linked)['state'] == 'synchronized'
        assert 'Newer title' in text(linked) and title(repo) == 'Newer title'


def test_linked_edit_imports_against_its_own_base(repo, linked):
    with Coordinator(repo):
        client = client_for(repo)
        _adopt(client, linked)
        replace_title(linked, 'Service task', 'Edited in linked worktree')
        result = client.sync(worktree=linked)
        assert result['state'] == 'synchronized', result
        assert title(repo) == 'Edited in linked worktree'
        assert result['imports'][0]['state'] == 'accepted'
        # The main checkout is republished from the store, never compared to the linked bytes.
        assert client.sync()['state'] == 'synchronized'
        assert 'Edited in linked worktree' in text(repo)


def test_unbased_dirty_linked_file_is_never_overwritten_or_imported(repo, linked):
    with Coordinator(repo):
        client = client_for(repo)
        client.execute(request(client, 'store', 'Store title'))
        client.sync()
        replace_title(linked, 'Service task', 'Hand edit before any sync')
        before = (linked / REL).read_bytes()
        result = client.sync(worktree=linked)
        assert result['state'] == 'pending' and FILE in result['unresolved'], result
        assert (linked / REL).read_bytes() == before
        assert title(repo) == 'Store title'
        taken = client.sync(worktree=linked, files=[FILE], take_file=True)
        assert title(repo) == 'Hand edit before any sync', taken


def test_linked_conflict_is_held_only_in_that_checkout(repo, linked):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        _adopt(client, linked)
        replace_title(linked, 'Service task', 'Linked side')
        client.execute(request(client, 'store', 'Store side'))
        result = client.sync(worktree=linked)
        assert result['state'] == 'pending' and FILE in result['unresolved'], result
        assert 'Linked side' in text(linked)
        # Main publication is not held by the linked checkout's conflict.
        later = client.execute(request(client, 'later', 'Later store title'))['receipt']
        assert owner.flush(later['commit_seq'])['state'] == 'exported'
        assert 'Later store title' in text(repo)


def test_interrupted_linked_publication_is_detected_on_next_sync(repo, linked):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        _adopt(client, linked)
        client.execute(request(client, 'crash', 'Crash title'))
        client.sync()

        def crash(stage):
            if stage == 'checkout_publish':
                raise RuntimeError('simulated coordinator death during linked publication')
        owner.checkpoint = crash
        with pytest.raises(Exception, match='simulated'):
            client.sync(worktree=linked)
        owner.checkpoint = lambda stage: None
        record = checkouts.read_record(owner, checkouts.resolve(repo, str(linked)).id)
        assert record['intent']['files'], record
        result = client.sync(worktree=linked)
        assert result['state'] == 'synchronized', result
        assert any('interrupted publication' in warning for warning in result['warnings']), result
        assert 'Crash title' in text(linked)


def test_aside_left_by_interrupted_publication_is_restored_or_kept(tmp_path):
    from taskmaster.native import projection
    backlog = tmp_path / '.taskmaster'
    (backlog / 'tasks').mkdir(parents=True)
    path = backlog / FILE
    aside = path.with_name(path.name + '.aside.co-tok')
    aside.write_bytes(b'base bytes')
    assert projection.recover_checkout_file(backlog, FILE, 'tok', projection._digest(b'target')) == 'restored'
    assert path.read_bytes() == b'base bytes' and not aside.exists()
    aside.write_bytes(b'someone else')
    path.write_bytes(b'unrelated newcomer')
    assert projection.recover_checkout_file(backlog, FILE, 'tok', projection._digest(b'target')) == 'aside kept'
    assert aside.read_bytes() == b'someone else' and path.read_bytes() == b'unrelated newcomer'


def test_publication_never_replaces_bytes_that_are_not_the_base(tmp_path):
    from taskmaster.native import projection
    backlog = tmp_path / '.taskmaster'
    (backlog / 'tasks').mkdir(parents=True)
    path = backlog / FILE
    path.write_bytes(b'user edit')
    outcome = projection.publish_checkout_file(backlog, FILE, b'published', projection._digest(b'base'), 't1')
    assert outcome == 'changed' and path.read_bytes() == b'user edit'
    path.write_bytes(b'base')
    assert projection.publish_checkout_file(backlog, FILE, b'published', projection._digest(b'base'), 't2') \
        == 'published'
    assert path.read_bytes() == b'published'
    assert not list((backlog / 'tasks').glob('*.co-*'))


# ── Bypassed Git in the main checkout (step 10) ────────────────────────────

def _commit_published(repo, client):
    assert client.sync()['state'] == 'synchronized'
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '--allow-empty', '-m', 'plain commit of the published generation')
    assert client.sync()['state'] == 'synchronized'  # observes the plain commit


@pytest.mark.parametrize('operation', ['checkout', 'switch', 'reset'])
def test_bypassed_branch_change_restoring_older_bytes_is_drift_not_rollback(repo, operation):
    initial = git(repo, 'rev-parse', 'HEAD').strip()
    with Coordinator(repo) as owner:
        client = client_for(repo)
        client.execute(request(client, 'main', 'Main title'))
        _commit_published(repo, client)
        if operation == 'checkout':
            git(repo, 'checkout', '-q', '-b', 'old', initial)
        elif operation == 'switch':
            git(repo, 'switch', '-q', '-c', 'old', initial)
        else:
            git(repo, 'reset', '-q', '--hard', initial)
        assert 'Service task' in text(repo)
        result = client.sync()
        assert result['state'] == 'pending' and FILE in result['unresolved'], result
        assert title(repo) == 'Main title', 'a restored older generation never rolls the store back'
        assert 'Service task' in text(repo), 'the checked-out file is neither imported nor overwritten'
        assert FILE in client.git_status()['drift']['files']
        later = client.execute(request(client, 'later', 'Later title'))['receipt']
        assert owner.flush(later['commit_seq'], timeout=1)['state'] == 'pending'
        # Returning to the published generation resolves it.
        if operation == 'reset':
            git(repo, 'reset', '-q', '--hard', 'HEAD@{1}')
        else:
            git(repo, 'checkout', '-q', 'main')
        assert client.sync()['state'] == 'synchronized'
        assert client.git_status()['drift'] is None
        assert 'Later title' in text(repo) and title(repo) == 'Later title'


def test_plain_commit_of_an_authored_edit_is_imported(repo):
    with Coordinator(repo):
        client = client_for(repo)
        _commit_published(repo, client)
        replace_title(repo, 'Service task', 'Committed hand edit')
        git(repo, 'commit', '-q', '-am', 'hand edit')
        result = client.sync()
        assert result['state'] == 'synchronized', result
        assert title(repo) == 'Committed hand edit'


def test_restore_from_head_is_drift(repo):
    with Coordinator(repo):
        client = client_for(repo)
        _commit_published(repo, client)
        client.execute(request(client, 'newer', 'Newer uncommitted title'))
        assert client.sync()['state'] == 'synchronized'
        git(repo, 'checkout', '--', REL)  # restores the committed (older) bytes
        result = client.sync()
        assert result['state'] == 'pending' and FILE in result['unresolved'], result
        assert title(repo) == 'Newer uncommitted title'


def test_unmanaged_commit_of_other_bytes_is_reported(repo):
    with Coordinator(repo):
        client = client_for(repo)
        _commit_published(repo, client)
        published = (repo / REL).read_bytes()
        replace_title(repo, 'Service task', 'Never published')
        git(repo, 'commit', '-q', '-am', 'commits bytes the store never published')
        (repo / REL).write_bytes(published)  # the working file is the generation again
        result = client.sync()
        assert any('unmanaged commit' in warning and FILE in warning for warning in result['warnings']), result
        assert title(repo) == 'Service task'


def test_released_main_drift_is_left_to_ordinary_sync(repo):
    initial = git(repo, 'rev-parse', 'HEAD').strip()
    with Coordinator(repo):
        client = client_for(repo)
        client.execute(request(client, 'main', 'Main title'))
        _commit_published(repo, client)
        git(repo, 'checkout', '-q', '-b', 'old', initial)
        assert client.sync()['state'] == 'pending'
        assert FILE in client.git_recover(release_drift='import')['released_drift']
        client.sync()  # explicit: the released bytes are now treated as an authored file
        assert client.git_status()['drift'] is None


# ── Managed Git in a linked checkout ───────────────────────────────────────

def test_managed_commit_in_a_linked_worktree(repo, linked):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        _adopt(client, linked)
        client.execute(request(client, 'linked-commit', 'Linked commit title'))
        main_head = git(repo, 'rev-parse', 'HEAD').strip()
        before = commit_count(linked)
        result = client.git_run(kind='commit', message='tm: linked', worktree=linked)
        assert result['state'] == 'completed' and result['generation_verified'] is True, result
        assert commit_count(linked) == before + 1
        assert 'Linked commit title' in show(linked, 'feature')
        assert git(repo, 'rev-parse', 'HEAD').strip() == main_head, 'the main checkout is not committed'
        assert f"Taskmaster-Op: {result['op_id']}" in git(linked, 'log', '-1', '--format=%B')
        assert owner.git_pin is None and client.git_status()['active'] is None
        # The managed commit was observed: the next sync sees no bypass.
        assert client.sync(worktree=linked)['state'] == 'synchronized'


def test_managed_checkout_in_a_linked_worktree_holds_drift_only_there(repo, linked):
    initial = git(repo, 'rev-parse', 'HEAD').strip()
    with Coordinator(repo) as owner:
        client = client_for(repo)
        client.execute(request(client, 'feature', 'Feature title'))
        _adopt(client, linked)
        git(linked, 'add', '-A')
        git(linked, 'commit', '-q', '-m', 'plain commit in linked')
        git(repo, 'branch', 'old', initial)
        assert client.sync(worktree=linked)['state'] == 'synchronized'
        result = client.git_run(kind='checkout', ref='old', worktree=linked)
        assert result['state'] == 'completed', result
        assert FILE in result['drift']['paths'] and result['drift']['checkout']['linked']
        assert 'Service task' in text(linked) and title(repo) == 'Feature title'
        holds = client.git_status()['checkouts'][result['drift']['checkout']['id']]['holds']
        assert holds.get(FILE) == 'drift'
        # Main publication continues; the linked checkout stays held.
        later = client.execute(request(client, 'later', 'Later title'))['receipt']
        assert owner.flush(later['commit_seq'])['state'] == 'exported'
        assert 'Later title' in text(repo)
        assert client.sync(worktree=linked)['state'] == 'pending'
        assert 'Service task' in text(linked)
        assert client.git_run(kind='commit', message='blocked', worktree=linked)['state'] == 'refused'


def test_recovery_reconciles_in_the_recorded_linked_checkout(repo, linked):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        _adopt(client, linked)
        checkout = checkouts.resolve(repo, str(linked))
        content = (linked / REL).read_bytes()
        marker = {'op_id': 'wt-op', 'request': ['t', 'wt-op'], 'kind': 'commit', 'phase': 'quiesced',
                  'contained': managed.jobs.supported(), 'platform': sys.platform, 'job': None, 'token_hash': 'x',
                  'pre': managed.snapshot(linked, managed.repository(linked)),
                  'generation': {'blobs': {FILE: [managed._blob_id(content)]}}, 'target': None,
                  'checkout': checkout.public()}
        git(linked, 'commit', '-q', '--allow-empty', '-m', 'managed', '--trailer', 'Taskmaster-Op: wt-op')
        main_head = git(repo, 'rev-parse', 'HEAD').strip()
        with owner.publication:
            managed.write_state(owner, marker=marker)
        result = client.git_recover()
        assert result['state'] == 'completed' and result['generation_verified'] is True, result
        assert git(repo, 'rev-parse', 'HEAD').strip() == main_head


def test_hook_in_a_linked_worktree_validates_against_the_main_store(repo, linked):
    from taskmaster.coordinator import git_hook
    with Coordinator(repo):
        client = client_for(repo)
        _adopt(client, linked)
    replace_title(linked, 'Service task', 'Unmanaged staging')
    git(linked, 'add', REL)
    ok, reason = git_hook.check(linked)
    assert not ok and 'unmanaged staging' in reason, reason


def test_vanished_worktree_rows_are_forgotten_only_when_git_dropped_it(repo, linked, tmp_path):
    second = tmp_path / 'second'
    git(repo, 'worktree', 'add', '-q', '-b', 'second', str(second))
    with Coordinator(repo) as owner:
        client = client_for(repo)
        _adopt(client, linked)
        gone = checkouts.resolve(repo, str(linked)).id
        git(repo, 'worktree', 'remove', str(linked))
        shutil.rmtree(linked, ignore_errors=True)
        result = client.sync(worktree=second)
        assert any('forgot removed worktree' in warning for warning in result['warnings']), result
        with closing(owner._connect(readonly=True)) as connection:
            assert store.record(connection, gone) is None and not store.bases(connection, gone)
