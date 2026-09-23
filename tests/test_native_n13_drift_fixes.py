# User intent: regressions for the N13 CodeMaestro rehearsal drift/checkout defects: D5 a
# derived index (ideas/IDEAS.md) must be re-rendered, never imported; D6 a managed checkout
# must have a managed way back; D7 line-ending-only differences Git makes are not drift.
# All repositories and worktrees are disposable temp directories.
from contextlib import closing

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.service import Coordinator
from native_git_helpers import REL, git, init_repo
from native_twins import make_twins
from test_native_service import request
from test_native_service_sync import title

pytestmark = pytest.mark.xdist_group('heavy_processes')
IDEAS = 'ideas/IDEAS.md'
FILE = REL.removeprefix('.taskmaster/')


@pytest.fixture
def repo(tmp_path, monkeypatch):
    def seed():
        bs.backlog_add_task(title='Service task', epic='test-epic', phase='dev')
        bs.backlog_idea_create(title='An idea worth keeping')
    twins = make_twins(tmp_path, monkeypatch, seed, visibility=None)
    with twins.at(twins.native):
        init_repo(twins.native)
        yield twins.native


def client_for(repo):
    return Client(repo, autostart=False, timeout=120)


def ideas_bytes(repo):
    return (repo / '.taskmaster' / IDEAS).read_bytes()


def _old_ideas_branch(repo):
    """Branch `old` whose committed IDEAS.md is not the store's render (an older index)."""
    head = git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    git(repo, 'checkout', '-q', '-b', 'old')
    path = repo / '.taskmaster' / IDEAS
    path.write_bytes(path.read_bytes() + b'- stale line from an older generation\n')
    git(repo, 'commit', '-q', '-am', 'older ideas index')
    git(repo, 'checkout', '-q', head)
    return head


# ── D5: derived index drift ────────────────────────────────────────────────

def test_d5_take_published_rerenders_a_drifted_derived_index(repo):
    published = ideas_bytes(repo)
    _old_ideas_branch(repo)
    with Coordinator(repo):
        client = client_for(repo)
        moved = client.git_run(kind='checkout', ref='old')
        assert moved['state'] == 'completed', moved
        assert IDEAS in moved['drift']['paths']
        released = client.git_recover(release_drift='take_published')
        assert IDEAS in released['released_drift'], released
        assert IDEAS not in released['kept'], released
        assert ideas_bytes(repo) == published
        assert IDEAS not in ((client.git_status()['drift'] or {}).get('files') or {})


def test_d5_import_is_refused_for_a_derived_index_and_sync_agrees_with_git(repo):
    _old_ideas_branch(repo)
    with Coordinator(repo):
        client = client_for(repo)
        assert client.git_run(kind='checkout', ref='old')['state'] == 'completed'
        released = client.git_recover(release_drift='import')
        assert IDEAS not in released['released_drift'], released
        assert 'take-published' in released['kept'][IDEAS], released
        assert IDEAS in client.git_status()['drift']['files']
        synced = client.sync()
        assert synced['state'] == 'pending' and IDEAS in synced['unresolved'], synced
        refused = client.git_run(kind='commit', message='tm: blocked')
        assert refused['state'] == 'refused', refused


def test_d5_bypassed_restore_of_a_derived_index_is_reported_and_rerendered(repo):
    published = ideas_bytes(repo)
    _old_ideas_branch(repo)
    with Coordinator(repo):
        client = client_for(repo)
        assert client.sync()['state'] == 'synchronized'
        git(repo, 'checkout', 'old', '--', f'.taskmaster/{IDEAS}')
        synced = client.sync()
        # sync must agree with managed Git, which refuses this generation.
        assert synced['state'] == 'pending' and IDEAS in synced['unresolved'], synced
        assert any('take-published' in notice for notice in synced['notices']), synced
        released = client.git_recover(release_drift='take_published')
        assert IDEAS in released['released_drift'], released
        assert ideas_bytes(repo) == published
        assert client.sync()['state'] == 'synchronized'
        # Managed Git agrees: the generation check passes (the re-render equals HEAD, so
        # there is nothing to commit and the commit settles as failed, not refused).
        committed = client.git_run(kind='commit', message='tm: index')
        assert committed['state'] == 'failed' and committed['post']['head'] == committed['pre']['head'], committed


def test_d5_linked_derived_index_is_never_imported_and_take_published_rerenders(repo):
    published = ideas_bytes(repo)
    _old_ideas_branch(repo)
    linked = repo.parent / 'linked-old'
    git(repo, 'worktree', 'add', '-q', str(linked), 'old')
    with Coordinator(repo):
        client = client_for(repo)
        first = client.sync(worktree=linked)
        assert first['state'] == 'pending' and IDEAS in first['unresolved'], first
        imported = client.git_recover(release_drift='import', worktree=linked)
        assert IDEAS not in imported['released_drift'] and 'take-published' in imported['kept'][IDEAS], imported
        taken = client.git_recover(release_drift='take_published', worktree=linked)
        assert IDEAS in taken['released_drift'], taken
        assert client.sync(worktree=linked)['state'] == 'synchronized'
        assert (linked / '.taskmaster' / IDEAS).read_bytes() == published
