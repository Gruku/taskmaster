# User intent: regression for N13 rehearsal defect D8 - a managed commit in a linked worktree
# must not be refused because Git wrote a published mixed-EOL file there in its own uniform
# line endings (sync already accepts that, per D7); the commit must still hold exactly the
# generation's content up to line endings, and foreign content must still be refused.
# All repositories and worktrees are disposable temp directories.
import hashlib
from pathlib import Path
import sys

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import git as managed
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.service import Coordinator
from native_git_helpers import REL, git, init_repo, install_hook
from native_twins import make_twins
from test_native_n13_drift_fixes import _publish_mixed

pytestmark = pytest.mark.xdist_group('heavy_processes')
PACKAGE = Path(__file__).resolve().parents[1]
OTHER = '.taskmaster/tasks/test-epic-002.md'
FILE = REL.removeprefix('.taskmaster/')


@pytest.fixture
def repo(tmp_path, monkeypatch):
    def seed():
        bs.backlog_add_task(title='Service task', epic='test-epic', phase='dev')
        bs.backlog_add_task(title='Second task', epic='test-epic', phase='dev')
    twins = make_twins(tmp_path, monkeypatch, seed, visibility=None)
    with twins.at(twins.native):
        init_repo(twins.native)
        yield twins.native


def _validator(repo):
    """The packaged pre-commit validator, installed after the plain setup commits."""
    hook = install_hook(repo, 'pre-commit', f'PYTHONPATH="{PACKAGE.as_posix()}" "{Path(sys.executable).as_posix()}" '
                                            '-m taskmaster.coordinator.git_hook pre-commit')
    # Absolute, so the validator also runs for commits in linked worktrees.
    git(repo, 'config', 'core.hooksPath', hook.parent.as_posix())


def _blob(content):
    return hashlib.sha1(b'blob %d\0' % len(content) + content).hexdigest()


def _lf(content):
    return content.replace(b'\r\n', b'\n')


def _linked(repo, client, name):
    """A fresh linked worktree: Git writes the mixed file in its own uniform form (D8)."""
    linked = repo.parent / name
    git(repo, 'worktree', 'add', '-q', '-b', 'feature', str(linked))
    assert client.sync(worktree=linked)['state'] == 'synchronized'
    return linked


@pytest.mark.parametrize('eol', ['autocrlf', 'eol-lf'])
def test_d8_managed_commit_in_linked_worktree_of_a_mixed_generation(repo, eol):
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        mixed = _publish_mixed(repo, client, eol)
        _validator(repo)
        assert (repo / REL).read_bytes() == mixed, 'main still publishes the mixed bytes'
        linked = _linked(repo, client, 'linked-d8')
        written = (linked / REL).read_bytes()
        assert written != mixed and _lf(written) == _lf(mixed)
        other = linked / OTHER
        other.write_bytes(other.read_bytes().replace(b'Second task', b'Edited in linked'))
        synced = client.sync(worktree=linked)
        assert synced['state'] == 'synchronized' and synced['imports'], synced

        result = client.git_run(kind='commit', message='tm: linked', worktree=linked)
        assert result['state'] == 'completed', result
        assert result.get('generation_verified') is not False, result
        # The mixed file is committed as Git's normalised blob of the generation's text.
        assert git(linked, 'ls-tree', 'feature', '--', REL).split()[2] == _blob(_lf(mixed))
        assert 'Edited in linked' in git(linked, 'show', f'feature:{OTHER}')
        assert client.sync(worktree=linked)['state'] == 'synchronized'


def test_d8_generation_accepts_eol_only_but_refuses_foreign_text(repo):
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        mixed = _publish_mixed(repo, client, 'autocrlf')
        linked = _linked(repo, client, 'linked-d8-foreign')
        recorded, _, mismatched = managed.generation(owner, 0, linked / '.taskmaster')
        assert FILE not in mismatched, mismatched
        # The blobs Git may stage for the disk bytes: exact, or LF-normalised (= the generation's text).
        assert _blob(_lf(mixed)) in recorded['blobs'][FILE]
        # Foreign text written behind sync's back is still not the generation.
        path = linked / REL
        path.write_bytes(path.read_bytes().replace(b'Mixed title', b'Foreign title'))
        _, _, mismatched = managed.generation(owner, 0, linked / '.taskmaster')
        assert FILE in mismatched


def test_d8_hook_validator_accepts_eol_only_staging_and_refuses_foreign_text(repo, monkeypatch):
    from taskmaster.coordinator import git_hook
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        mixed = _publish_mixed(repo, client, 'autocrlf')
        # HEAD records other bytes, so Git's uniform form of the generation is staged.
        path = repo / REL
        path.write_bytes(_lf(mixed).replace(b'Mixed title', b'Older title'))
        git(repo, 'commit', '-q', '-am', 'older bytes')
        path.write_bytes(_lf(mixed).replace(b'\n', b'\r\n'))
        git(repo, 'add', REL)
        token = 'd' * 64
        marker = {'op_id': 'd8-hook', 'request': ['t', 'r'], 'kind': 'commit', 'phase': 'launch',
                  'token_hash': hashlib.sha256(token.encode()).hexdigest()}
        with owner.publication:
            managed.write_state(owner, marker=marker)
        try:
            monkeypatch.setenv(git_hook.TOKEN_ENV, token)
            ok, reason = git_hook.check(repo)
            assert ok, reason
            path.write_bytes(path.read_bytes().replace(b'Mixed title', b'Foreign title'))
            git(repo, 'add', REL)
            ok, reason = git_hook.check(repo)
            assert not ok and REL in reason
        finally:
            with owner.publication:
                managed.write_state(owner, marker=None)
