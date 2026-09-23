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


# ── D7: line-ending-only differences Git makes ─────────────────────────────

def _mixed(content: bytes) -> bytes:
    """CRLF with the last lines LF: the shape of two live CodeMaestro handovers."""
    lines = content.replace(b'\r\n', b'\n').split(b'\n')
    head, tail = lines[:-3], lines[-3:]
    return b'\r\n'.join(head) + b'\r\n' + b'\n'.join(tail)


def _published_digest(repo, rel):
    import sqlite3
    with closing(sqlite3.connect((repo / '.taskmaster/local/store.db').as_uri() + '?mode=ro', uri=True)) as db:
        return db.execute('SELECT content_hash FROM projection WHERE file=?', (rel,)).fetchone()[0]


def _publish_mixed(repo, client, eol):
    import hashlib
    if eol == 'autocrlf':
        git(repo, 'config', 'core.autocrlf', 'true')
    else:
        (repo / '.gitattributes').write_text('* text=auto eol=lf\n', encoding='utf-8')
    # The repository stores LF blobs, as CodeMaestro's does.
    git(repo, 'add', '--renormalize', '-A')
    git(repo, 'commit', '-q', '--allow-empty', '-m', 'eol settings')
    assert client.sync()['state'] == 'synchronized'  # records the trusted bases
    target = repo / REL
    mixed = _mixed(target.read_bytes().replace(b'Service task', b'Mixed title'))
    assert b'\r\n' in mixed and b'\n' in mixed.replace(b'\r\n', b'')
    target.write_bytes(mixed)
    imported = client.sync()
    assert imported['state'] == 'synchronized', (imported['notices'], imported['unresolved'], imported['imports'])
    assert title(repo) == 'Mixed title'
    assert _published_digest(repo, FILE) == hashlib.sha1(mixed).hexdigest(), 'the store published the mixed bytes'
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'plain commit of the mixed generation')
    assert git(repo, 'ls-files', '--eol', REL).startswith('i/lf'), 'Git stores the normalised blob'
    assert client.sync()['state'] == 'synchronized'
    return mixed


@pytest.mark.parametrize('eol', ['autocrlf', 'eol-lf'])
def test_d7_git_line_ending_normalisation_is_not_drift_in_main(repo, eol):
    with Coordinator(repo):
        client = client_for(repo)
        mixed = _publish_mixed(repo, client, eol)
        (repo / REL).unlink()
        git(repo, 'checkout', '--', REL)  # Git writes its normalised form
        normalised = (repo / REL).read_bytes()
        assert normalised != mixed and normalised.replace(b'\r\n', b'\n') == mixed.replace(b'\r\n', b'\n')
        synced = client.sync()
        assert synced['state'] == 'synchronized', synced
        assert not synced['imports'], 'a line-ending-only difference is not an import'
        assert not (client.git_status()['drift'] or {}).get('files')
        assert title(repo) == 'Mixed title'
        # Managed Git agrees, and keeps agreeing on the next Git write.
        assert client.git_run(kind='commit', message='tm: nothing')['state'] in ('completed', 'failed')
        (repo / REL).unlink()
        git(repo, 'checkout', '--', REL)
        assert client.sync()['state'] == 'synchronized'


@pytest.mark.parametrize('eol', ['autocrlf', 'eol-lf'])
def test_d7_fresh_linked_worktree_of_a_mixed_generation_is_not_held(repo, eol):
    with Coordinator(repo):
        client = client_for(repo)
        _publish_mixed(repo, client, eol)
        linked = repo.parent / 'linked-eol'
        git(repo, 'worktree', 'add', '-q', '-b', 'feature', str(linked))
        synced = client.sync(worktree=linked)
        assert synced['state'] == 'synchronized', synced
        assert not synced['imports']


# ── D6: a managed way back after a managed checkout ────────────────────────

def _tree(repo, rev):
    return {path.removeprefix('.taskmaster/') for path in
            git(repo, 'ls-tree', '-r', '--name-only', rev, '--', '.taskmaster').split()}


def _newer_generation_on_main(repo, client):
    """main gains a newer title and a projection file `old` lacks, committed by managed Git."""
    head = git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    git(repo, 'branch', 'old')
    client.execute(request(client, 'main-title', 'Main title'))
    note = request(client, 'main-note')
    note.update(operation='note.create', arguments={'text': 'main only note'})
    client.execute(note)
    assert client.git_run(kind='commit', message='tm: main')['state'] == 'completed'
    added = _tree(repo, head) - _tree(repo, 'old')
    assert added and FILE not in added
    return head, added


def _published_files(repo):
    return {path.relative_to(repo / '.taskmaster').as_posix(): path.read_bytes()
            for path in (repo / '.taskmaster').rglob('*') if path.is_file()
            and not path.relative_to(repo / '.taskmaster').as_posix().startswith('local/')}


def _assert_home(repo, client, head, before):
    assert git(repo, 'symbolic-ref', 'HEAD').strip() == f'refs/heads/{head}'
    assert title(repo) == 'Main title'
    assert client.git_status()['drift'] is None
    synced = client.sync()
    assert synced['state'] == 'synchronized', synced
    assert not synced['imports'], 'returning home imports nothing'
    assert _published_files(repo) == before
    assert git(repo, 'status', '--porcelain', '--', '.taskmaster').strip() == ''


def test_d6_managed_checkout_back_while_drift_is_held(repo):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        head, added = _newer_generation_on_main(repo, client)
        before = _published_files(repo)
        away = client.git_run(kind='checkout', ref='old')
        assert away['state'] == 'completed' and FILE in away['drift']['paths'], away
        back = client.git_run(kind='checkout', ref=head)
        assert back['state'] == 'completed', back
        _assert_home(repo, client, head, before)
        assert owner.git_pin is None


def test_d6_managed_checkout_back_after_take_published(repo):
    """take_published leaves published bytes that `old` tracks differently (dirty) or not at
    all (untracked): Git alone refuses to overwrite them; they are the store's own bytes."""
    with Coordinator(repo) as owner:
        client = client_for(repo)
        head, added = _newer_generation_on_main(repo, client)
        before = _published_files(repo)
        assert client.git_run(kind='checkout', ref='old')['state'] == 'completed'
        released = client.git_recover(release_drift='take_published')
        assert released['state'] == 'clear', released
        assert all((repo / '.taskmaster' / rel).exists() for rel in added)
        back = client.git_run(kind='checkout', ref=head)
        assert back['state'] == 'completed', back
        _assert_home(repo, client, head, before)
        assert owner.git_pin is None


def test_d6_managed_checkout_back_never_overwrites_authored_bytes(repo):
    with Coordinator(repo):
        client = client_for(repo)
        head, added = _newer_generation_on_main(repo, client)
        assert client.git_run(kind='checkout', ref='old')['state'] == 'completed'
        # An edit typed over held drift, and a foreign file where main tracks a projection.
        edited = (repo / REL).read_bytes().replace(b'Service task', b'Typed while away')
        (repo / REL).write_bytes(edited)
        foreign = sorted(added)[0]
        (repo / '.taskmaster' / foreign).parent.mkdir(parents=True, exist_ok=True)
        (repo / '.taskmaster' / foreign).write_bytes(b'not the store\'s bytes\n')
        back = client.git_run(kind='checkout', ref=head)
        assert back['state'] in ('failed', 'refused'), back
        assert git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip() == 'old'  # never moved
        assert (repo / REL).read_bytes() == edited
        assert (repo / '.taskmaster' / foreign).read_bytes() == b'not the store\'s bytes\n'
        assert title(repo) == 'Main title'


def test_d6_crash_after_setting_files_aside_is_undone_by_recovery(repo):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        head, added = _newer_generation_on_main(repo, client)
        assert client.git_run(kind='checkout', ref='old')['state'] == 'completed'
        assert client.git_recover(release_drift='take_published')['state'] == 'clear'
        published = {rel: (repo / '.taskmaster' / rel).read_bytes() for rel in [FILE, *added]}

        def crash(stage):
            if stage == 'git_aside_moved':
                raise RuntimeError('simulated coordinator death after the aside')
        owner.checkpoint = crash
        with pytest.raises(Exception, match='simulated'):
            client.git_run(kind='checkout', ref=head)
        owner.checkpoint = lambda stage: None
        assert not (repo / REL).exists(), 'the files were set aside before the crash'
        recovered = client.git_recover()
        assert recovered['state'] == 'failed', recovered
        assert git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip() == 'old'
        assert {rel: (repo / '.taskmaster' / rel).read_bytes() for rel in published} == published
        assert not (repo / '.git' / 'taskmaster-aside').exists() or not any((repo / '.git' / 'taskmaster-aside').iterdir())
        assert owner.git_pin is None
        # The round trip still completes afterwards.
        assert client.git_run(kind='checkout', ref=head)['state'] == 'completed'


def test_d6_linked_checkout_round_trip(repo):
    with Coordinator(repo):
        client = client_for(repo)
        head, added = _newer_generation_on_main(repo, client)
        linked = repo.parent / 'linked-rt'
        git(repo, 'worktree', 'add', '-q', '-b', 'feature', str(linked), head)
        assert client.sync(worktree=linked)['state'] == 'synchronized'
        before = _published_files(linked)
        away = client.git_run(kind='checkout', ref='old', worktree=linked)
        assert away['state'] == 'completed' and away['drift']['count'], away
        back = client.git_run(kind='checkout', ref='feature', worktree=linked)
        assert back['state'] == 'completed', back
        synced = client.sync(worktree=linked)
        assert synced['state'] == 'synchronized', synced
        assert not synced['imports'] and _published_files(linked) == before
        assert title(repo) == 'Main title'
