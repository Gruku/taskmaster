# User intent: regressions for the independent review of the D6 set-aside (managed checkout
# round trip): a file set aside must never be deleted unless its bytes are verified as the
# store's own, every failure path must put published files back, and stray aside dirs are
# swept. All repositories are disposable temp directories.
from contextlib import closing
import errno
import os
from pathlib import Path

import pytest

from taskmaster.coordinator.contained import ManagedChild
from taskmaster.coordinator.service import Coordinator
from taskmaster.native import projection
from native_git_helpers import REL, git
from test_native_n13_drift_fixes import FILE, _newer_generation_on_main, client_for, repo  # noqa: F401

pytestmark = pytest.mark.xdist_group('heavy_processes')
TYPED = b'typed between plan and move\n'
AGAIN = b'saved again after the move\n'
FOREIGN = b'not the store bytes\n'


def _away_released(repo, client):
    """HEAD on `old` with main's generation published over it (dirty and untracked files
    Git alone would refuse to replace): the next checkout back sets files aside."""
    head, added = _newer_generation_on_main(repo, client)
    assert client.git_run(kind='checkout', ref='old')['state'] == 'completed'
    assert client.git_recover(release_drift='take_published')['state'] == 'clear'
    published = {rel: (repo / '.taskmaster' / rel).read_bytes() for rel in [FILE, *added]}
    return head, published


def _editor_write(path, data):
    """An editor save: a raw OS write, outside every code path Taskmaster owns."""
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_BINARY', 0))
    try:
        os.write(handle, data)
    finally:
        os.close(handle)


def _is_file(path):
    return str(path).replace('\\', '/').endswith(FILE)


def _aside_copies(repo):
    base = repo / '.git' / 'taskmaster-aside'
    return sorted(path.read_bytes() for path in base.rglob('*') if path.is_file()) if base.exists() else []


def _no_aside_left(repo):
    return _aside_copies(repo) == []


def test_m1_changed_file_that_cannot_be_put_back_is_kept_and_pins(repo, monkeypatch):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        head, published = _away_released(repo, client)
        target = repo / REL

        def checkpoint(stage):
            if stage == 'git_marker_written':
                _editor_write(target, TYPED)  # an editor save after the plan, before the move
        real = projection._move

        def move(source, dest):
            moved = real(source, dest)
            if _is_file(source):
                _editor_write(target, AGAIN)  # ... and again right after the move
            return moved
        owner.checkpoint = checkpoint
        monkeypatch.setattr(projection, '_move', move)
        result = client.git_run(kind='checkout', ref=head)
        owner.checkpoint = lambda stage: None
        monkeypatch.setattr(projection, '_move', real)
        assert result['state'] == 'recovery_required', result
        assert owner.git_pin is not None and client.git_status()['active'] is not None
        assert target.read_bytes() == AGAIN
        assert TYPED in _aside_copies(repo)
        recovered = client.git_recover()
        assert recovered['state'] == 'failed', recovered
        assert target.read_bytes() == AGAIN
        assert _aside_copies(repo) == [TYPED], 'changed bytes are never dropped'
        unsettled = client.git_status()['aside']
        assert unsettled and FILE in unsettled[0]['files'], unsettled
        assert any('could not be settled' in notice for notice in recovered['notices']), recovered
        assert all((repo / '.taskmaster' / rel).read_bytes() == data for rel, data in published.items() if rel != FILE)
        assert owner.git_pin is None
        assert git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip() == 'old'


def test_m1_crash_between_move_and_digest_check_never_drops_changed_bytes(repo, monkeypatch):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        head, published = _away_released(repo, client)
        target = repo / REL

        def checkpoint(stage):
            if stage == 'git_marker_written':
                _editor_write(target, TYPED)
        real = projection._move

        def move(source, dest):
            moved = real(source, dest)
            if _is_file(source):
                raise RuntimeError('simulated death between the move and its digest check')
            return moved
        owner.checkpoint = checkpoint
        monkeypatch.setattr(projection, '_move', move)
        with pytest.raises(Exception, match='simulated'):
            client.git_run(kind='checkout', ref=head)
        owner.checkpoint = lambda stage: None
        monkeypatch.setattr(projection, '_move', real)
        _editor_write(target, AGAIN)  # the editor recreates the file before recovery
        recovered = client.git_recover()
        assert recovered['state'] == 'failed', recovered
        assert target.read_bytes() == AGAIN
        assert _aside_copies(repo) == [TYPED]
        assert owner.git_pin is None


def test_m2_helper_start_failure_puts_set_aside_files_back(repo, monkeypatch):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        head, published = _away_released(repo, client)

        def start(self):
            raise OSError('simulated helper start failure')
        monkeypatch.setattr(ManagedChild, 'start', start)
        result = client.git_run(kind='checkout', ref=head)
        assert result['state'] == 'refused' and 'could not start' in result['reason'], result
        assert any('put back' in notice for notice in result['notices']), result
        assert {rel: (repo / '.taskmaster' / rel).read_bytes() for rel in published} == published
        assert _no_aside_left(repo)
        assert client.git_status()['active'] is None and owner.git_pin is None


def test_l1_transient_sharing_violation_on_move_is_retried(repo, monkeypatch):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        head, published = _away_released(repo, client)
        real, failed = projection._move, []

        def move(source, dest):
            if not failed:
                failed.append(source)
                raise PermissionError(errno.EACCES, 'simulated sharing violation', str(source))
            return real(source, dest)
        monkeypatch.setattr(projection, '_move', move)
        result = client.git_run(kind='checkout', ref=head)
        assert failed and result['state'] == 'completed', result
        assert owner.git_pin is None and _no_aside_left(repo)


def test_l1_move_error_restores_and_settles_as_refused(repo, monkeypatch):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        head, published = _away_released(repo, client)
        real, calls = projection._move, []

        def move(source, dest):
            calls.append(source)
            if len(calls) == 2:
                raise OSError(errno.ENOSPC, 'simulated disk full', str(source))
            return real(source, dest)
        monkeypatch.setattr(projection, '_move', move)
        result = client.git_run(kind='checkout', ref=head)
        assert result['state'] == 'refused', result
        assert {rel: (repo / '.taskmaster' / rel).read_bytes() for rel in published} == published
        assert _no_aside_left(repo)
        assert client.git_status()['active'] is None and owner.git_pin is None
        assert git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip() == 'old'


def test_l2_a_file_without_a_retained_base_is_never_set_aside(repo):
    with Coordinator(repo) as owner:
        client = client_for(repo)
        head, published = _away_released(repo, client)
        synced = owner.sync

        def sync(**kwargs):
            # A legacy exporter dropped the retained base after the pre-sync observed it.
            outcome = synced(**kwargs)
            with closing(owner._connect()) as connection:
                connection.execute('DELETE FROM projection_base WHERE file=?', (FILE,))
                connection.commit()
            return outcome
        owner.sync = sync
        result = client.git_run(kind='checkout', ref=head)
        assert result['state'] in ('failed', 'refused'), result
        assert (repo / REL).read_bytes() == published[FILE]
        assert git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip() == 'old'
        assert _no_aside_left(repo)


def test_l3_startup_sweeps_stray_aside_dirs(repo):
    rel = 'ideas/IDEA-001.md'
    with Coordinator(repo):
        assert client_for(repo).sync()['state'] == 'synchronized'
    target = repo / '.taskmaster' / rel
    base = repo / '.git' / 'taskmaster-aside'
    published = target.read_bytes()
    own = base / 'a-stray' / rel
    own.parent.mkdir(parents=True)
    own.write_bytes(published)
    target.unlink()
    foreign = base / 'b-stray' / rel
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(FOREIGN)
    with Coordinator(repo):
        status = client_for(repo).git_status()
        assert target.read_bytes() == published, 'a verified stray copy is put back'
        assert not own.exists() and foreign.read_bytes() == FOREIGN
        assert [Path(entry['dir']).name for entry in status['aside']] == ['b-stray'], status['aside']
        assert list(status['aside'][0]['files']) == [rel], status['aside']
    foreign.unlink()
    with Coordinator(repo):
        assert client_for(repo).git_status()['aside'] is None, 'a resolved entry is forgotten'
    assert not (base / 'b-stray').exists()
