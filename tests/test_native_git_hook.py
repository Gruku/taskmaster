"""User intent: the packaged pre-commit validator refuses unmanaged or stale projection
staging with guidance, admits the managed generation, and the CLI drives it.
Hooks are written only into disposable temp repositories.
"""
import hashlib
import json
from pathlib import Path
import sys

import pytest

from taskmaster.coordinator import git as managed
from taskmaster.coordinator import git_cli, git_hook
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.service import Coordinator
from native_git_helpers import REL, commit_count, git, init_repo, install_hook
from test_native_service import root, request  # noqa: F401

pytestmark = pytest.mark.xdist_group('heavy_processes')
PACKAGE = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo(root):
    init_repo(root)
    install_hook(root, 'pre-commit', f'PYTHONPATH="{PACKAGE.as_posix()}" "{Path(sys.executable).as_posix()}" '
                                     '-m taskmaster.coordinator.git_hook pre-commit')
    return root


def test_unmanaged_projection_staging_is_refused_with_guidance(repo):
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        receipt = client.execute(request(client, 'title', 'Unmanaged title'))['receipt']
        assert owner.flush(receipt['commit_seq'])['state'] == 'exported'
        before = commit_count(repo)
        git(repo, 'add', REL)
        refused = git(repo, 'commit', '-q', '-m', 'hand commit', check=False)
        assert commit_count(repo) == before
        completed = __import__('subprocess').run(['git', 'commit', '-q', '-m', 'hand commit'], cwd=repo,
                                                 capture_output=True, text=True)
        assert completed.returncode != 0 and 'git_cli commit' in completed.stderr, (refused, completed.stderr)
        git(repo, 'restore', '--staged', '.taskmaster')
        (repo / 'notes.txt').write_text('code change\n', encoding='utf-8')
        git(repo, 'add', 'notes.txt')
        git(repo, 'commit', '-q', '-m', 'code only')
        assert commit_count(repo) == before + 1
        # The managed path passes the same hook.
        result = client.git_run(kind='commit', message='tm: managed')
        assert result['state'] == 'completed', result
        assert commit_count(repo) == before + 2


def test_stale_staging_under_a_managed_token_is_refused(repo, monkeypatch):
    with Coordinator(repo) as owner:
        token = 'a' * 64
        marker = {'op_id': 'hook-test', 'request': ['t', 'r'], 'kind': 'commit', 'phase': 'launch',
                  'token_hash': hashlib.sha256(token.encode()).hexdigest()}
        with owner.publication:
            managed.write_state(owner, marker=marker)
        path = repo / REL
        path.write_bytes(path.read_bytes().replace(b'Service task', b'Unpublished edit'))
        git(repo, 'add', REL)
        monkeypatch.setenv(git_hook.TOKEN_ENV, token)
        ok, reason = git_hook.check(repo)
        assert not ok and REL in reason
        monkeypatch.setenv(git_hook.TOKEN_ENV, 'b' * 64)
        ok, reason = git_hook.check(repo)
        assert not ok and 'unmanaged' in reason
        with owner.publication:
            managed.write_state(owner, marker=None)


def test_cli_commit_status_and_recover(repo, capsys):
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        client.execute(request(client, 'cli', 'CLI title'))
        assert git_cli.main(['--root', str(repo), 'commit', '-m', 'tm: cli', '--request-id', 'cli-1']) == 0
        out = json.loads(capsys.readouterr().out)
        assert out['state'] == 'completed'
        assert git_cli.main(['--root', str(repo), 'status']) == 0
        assert json.loads(capsys.readouterr().out)['last']['request'] == ['explicit-git', 'cli-1']
        assert git_cli.main(['--root', str(repo), 'recover']) == 0
        assert json.loads(capsys.readouterr().out)['state'] == 'clear'
