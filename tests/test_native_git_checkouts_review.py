"""User intent: regressions from the N13 steps 9-10 review. Bypassed Git must never roll the
store back to older/foreign bytes and an authored edit must never be silently discarded;
when unsure the path is held and the user adopts it explicitly (take_file / release).
All repositories and worktrees are disposable temp directories.
"""
from contextlib import closing

import pytest

from taskmaster.coordinator.protocol import connect
from taskmaster.coordinator.service import Coordinator
from taskmaster.native.queries import Repository
from native_git_helpers import REL, git
from test_native_git_checkouts import _commit_published, client_for, linked, repo, text  # noqa: F401
from test_native_service import root, request  # noqa: F401
from test_native_service_sync import title

pytestmark = pytest.mark.xdist_group('heavy_processes')
FILE = REL.removeprefix('.taskmaster/')


def body(root):
    with closing(connect(root, readonly=True)) as connection, Repository(connection).snapshot() as snapshot:
        return snapshot.get('task', 'test-epic-001', include_body=True)['body'] or ''


def two_generations(repo, client):
    """Title A then Title B, each published and committed by a plain commit."""
    client.execute(request(client, 'a', 'Title A'))
    _commit_published(repo, client)
    client.execute(request(client, 'b', 'Title B'))
    _commit_published(repo, client)


def held(result):
    return result['state'] == 'pending' and FILE in result['unresolved']


# ── H1/H2: older committed bytes, in-progress operations, reverts ──────────

@pytest.mark.parametrize('restore', ['checkout', 'restore'])
def test_older_revision_restored_without_moving_head_is_drift(repo, restore):
    with Coordinator(repo):
        client = client_for(repo)
        two_generations(repo, client)
        if restore == 'checkout':
            git(repo, 'checkout', 'HEAD~1', '--', REL)
        else:
            git(repo, 'restore', '--source', 'HEAD~1', '--', REL)
        assert 'Title A' in text(repo)
        result = client.sync()
        assert held(result), result
        assert title(repo) == 'Title B', 'older committed bytes rolled the store back'
        assert 'Title A' in text(repo) and FILE in client.git_status()['drift']['files']


def _side_branch(repo, tmp_path):
    """`side` from HEAD~1 appends a note: merging it yields bytes no commit holds."""
    path = tmp_path / 'side-wt'
    git(repo, 'worktree', 'add', '-q', '-b', 'side', str(path), 'HEAD~1')
    target = path / REL
    target.write_bytes(target.read_bytes() + b'\nSide note from an unmerged branch.\n')
    git(path, 'commit', '-q', '-am', 'side note')
    git(repo, 'worktree', 'remove', str(path))


@pytest.mark.parametrize('operation', [('merge', '--no-commit', '--no-ff', 'side'), ('merge', '--squash', 'side'),
                                       ('cherry-pick', '-n', 'side')], ids=['merge-no-commit', 'squash', 'pick-n'])
def test_in_progress_operation_holds_every_differing_file(repo, tmp_path, operation):
    with Coordinator(repo):
        client = client_for(repo)
        two_generations(repo, client)
        _side_branch(repo, tmp_path)
        git(repo, *operation)
        assert 'Side note' in text(repo) and 'Title B' in text(repo)
        result = client.sync()
        assert held(result), result
        assert 'Side note' not in body(repo) and title(repo) == 'Title B'


def test_revert_committed_by_a_plain_commit_is_held_until_taken(repo):
    with Coordinator(repo):
        client = client_for(repo)
        two_generations(repo, client)
        git(repo, 'revert', '--no-commit', 'HEAD')
        git(repo, 'commit', '-q', '-m', 'undo')
        assert git(repo, 'reflog', '-1', '--format=%gs').startswith('commit:')
        result = client.sync()
        assert held(result), result
        assert title(repo) == 'Title B', 'a committed revert rolled the store back'
        taken = client.sync(files=[FILE], take_file=True)
        assert taken['state'] == 'synchronized', taken
        assert title(repo) == 'Title A'


def test_hand_revert_to_an_earlier_committed_value_is_held(repo):
    """Documented semantics: bytes equal to an earlier committed version are held even when
    typed by hand; take_file adopts them."""
    with Coordinator(repo):
        client = client_for(repo)
        client.execute(request(client, 'a', 'Title A'))
        _commit_published(repo, client)
        older = (repo / REL).read_bytes()
        client.execute(request(client, 'b', 'Title B'))
        _commit_published(repo, client)
        (repo / REL).write_bytes(older)
        git(repo, 'commit', '-q', '-am', 'hand revert')
        assert held(client.sync())
        assert title(repo) == 'Title B'


def test_authored_commit_on_top_of_history_is_still_imported(repo):
    with Coordinator(repo):
        client = client_for(repo)
        two_generations(repo, client)
        target = repo / REL
        target.write_bytes(target.read_bytes().replace(b'Title B', b'Fresh authored title'))
        git(repo, 'commit', '-q', '-am', 'authored')
        result = client.sync()
        assert result['state'] == 'synchronized', result
        assert title(repo) == 'Fresh authored title'


# ── H3/M1: named-file syncs and bytes that change during a sync ────────────

def test_named_file_sync_still_detects_bypassed_git(repo):
    initial = git(repo, 'rev-parse', 'HEAD').strip()
    with Coordinator(repo):
        client = client_for(repo)
        client.execute(request(client, 'a', 'Main title'))
        _commit_published(repo, client)
        git(repo, 'reset', '-q', '--hard', initial)
        named = client.sync(files=[FILE])  # what MCP resync tools do
        assert held(named), named
        assert title(repo) == 'Main title', 'a named-file sync rolled the store back'
        # The observation was not advanced by the named sync: a full sync still sees it.
        assert held(client.sync())
        assert title(repo) == 'Main title'


def test_bytes_git_restores_after_classification_are_not_imported(repo, monkeypatch):
    from taskmaster.coordinator import checkouts
    original = checkouts.detect

    def racing(owner, checkout, selected, drift):
        found = original(owner, checkout, selected, drift)
        git(repo, 'checkout', 'HEAD~1', '--', REL)  # Git rewrites the file mid-sync
        return found
    with Coordinator(repo):
        client = client_for(repo)
        two_generations(repo, client)
        monkeypatch.setattr(checkouts, 'detect', racing)
        result = client.sync()
        assert held(result), result
        assert title(repo) == 'Title B', 'bytes Git put there after classification were imported'


def test_head_moving_during_a_sync_defers_the_import(repo, monkeypatch):
    from taskmaster.coordinator import checkouts
    original = checkouts.detect
    with Coordinator(repo):
        client = client_for(repo)
        _commit_published(repo, client)
        target = repo / REL
        target.write_bytes(target.read_bytes().replace(b'Service task', b'Authored while HEAD moves'))

        def racing(owner, checkout, selected, drift):
            found = original(owner, checkout, selected, drift)
            git(repo, 'commit', '-q', '--allow-empty', '-m', 'concurrent commit')
            return found
        monkeypatch.setattr(checkouts, 'detect', racing)
        result = client.sync()
        assert held(result) and any('HEAD moved' in notice for notice in result['notices']), result
        monkeypatch.setattr(checkouts, 'detect', original)
        assert client.sync()['state'] == 'synchronized'
        assert title(repo) == 'Authored while HEAD moves'


# ── M2/M3: explicit release verbs; a changed hold is never released ────────

def _fresh_linked_drift(repo, linked, client):
    client.execute(request(client, 'newer', 'Newer'))
    assert client.sync()['state'] == 'synchronized'
    assert held(client.sync(worktree=linked))  # the worktree's committed bytes are older


def test_take_published_keeps_a_linked_hold_whose_bytes_changed(repo, linked):
    with Coordinator(repo):
        client = client_for(repo)
        _fresh_linked_drift(repo, linked, client)
        target = linked / REL
        target.write_bytes(target.read_bytes().replace(b'Service task', b'My careful edit'))
        result = client.git_recover(release_drift='take_published', worktree=linked)
        assert FILE in result['kept'] and FILE not in result['released_drift'], result
        assert result['checkout']['path'] == str(linked.resolve())
        assert held(client.sync(worktree=linked))
        assert b'My careful edit' in target.read_bytes(), 'a hand edit was overwritten by publication'
        taken = client.sync(worktree=linked, files=[FILE], take_file=True)
        assert title(repo) == 'My careful edit', taken


def test_take_published_retains_the_bytes_it_lets_publication_replace(repo, linked):
    with Coordinator(repo):
        client = client_for(repo)
        _fresh_linked_drift(repo, linked, client)
        older = (linked / REL).read_bytes()
        result = client.git_recover(release_drift='take_published', worktree=linked)
        assert result['state'] == 'clear' and FILE in result['released_drift'], result
        assert client.sync(worktree=linked)['state'] == 'synchronized'
        assert 'Newer' in text(linked) and title(repo) == 'Newer'
        from taskmaster.native.checkouts import digest
        kept = client.git_status()['discarded']
        assert any(item['file'] == FILE and item['digest'] == digest(older) for item in kept), kept


def test_main_take_published_replaces_only_the_held_bytes(repo):
    initial = git(repo, 'rev-parse', 'HEAD').strip()
    with Coordinator(repo):
        client = client_for(repo)
        client.execute(request(client, 'main', 'Main title'))
        _commit_published(repo, client)
        git(repo, 'checkout', '-q', '-b', 'old', initial)
        assert held(client.sync())
        result = client.git_recover(release_drift='take_published')
        assert result['checkout']['id'] == 'main' and FILE in result['released_drift'], result['kept']
        assert 'Main title' in text(repo) and title(repo) == 'Main title'
        assert client.git_status()['drift'] is None
        assert client.sync()['state'] == 'synchronized'
        assert any(item['file'] == FILE for item in client.git_status()['discarded'])


def test_linked_import_verb_lets_ordinary_sync_import_the_held_bytes(repo, linked):
    from test_native_git_checkouts import _adopt
    with Coordinator(repo):
        client = client_for(repo)
        _adopt(client, linked)
        client.execute(request(client, 'newer', 'Main newer'))
        assert client.sync()['state'] == 'synchronized'
        assert client.sync(worktree=linked)['state'] == 'synchronized' and 'Main newer' in text(linked)
        git(linked, 'checkout', '--', REL)  # Git restores the committed (older) bytes
        assert held(client.sync(worktree=linked))
        assert title(repo) == 'Main newer'
        result = client.git_recover(release_drift='import', worktree=linked)
        assert FILE in result['released_drift'], result
        imported = client.sync(worktree=linked)
        assert imported['state'] == 'synchronized', imported
        assert title(repo) == 'Service task'


def test_release_drift_requires_an_explicit_verb(repo):
    with Coordinator(repo):
        with pytest.raises(ValueError, match='take_published'):
            client_for(repo).git_recover(release_drift=True)


def test_cli_release_drift_verbs_and_checkout_echo(repo, monkeypatch, capsys):
    import json
    from taskmaster.coordinator import client as client_module, git_cli
    calls = []

    class Recorder:
        def __init__(self, root, timeout=None):
            pass

        def git_recover(self, **arguments):
            calls.append(arguments)
            return {'state': 'clear'}
    monkeypatch.setattr(client_module, 'Client', Recorder)
    with pytest.raises(SystemExit):
        git_cli.main(['--root', str(repo), 'recover', '--release-drift'])
    capsys.readouterr()
    assert git_cli.main(['--root', str(repo), 'recover', '--release-drift', 'take-published']) == 0
    assert calls[-1]['release_drift'] == 'take_published' and calls[-1]['worktree'] is None
    assert json.loads(capsys.readouterr().out)['checkout'] == str(repo)
    git_cli.main(['--root', str(repo), 'recover', '--release-drift', 'import'])
    assert calls[-1]['release_drift'] == 'import'
