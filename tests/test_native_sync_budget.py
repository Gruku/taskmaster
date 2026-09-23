"""User intent: the sync time budget is a bounded parameter end to end (IPC client, service,
MCP resync, managed-Git pre-sync, CLI), with a default derived from measured CodeMaestro cost
(N13 D1), and an exhausted budget still reports `pending` honestly.
"""
import time

import pytest

from taskmaster.coordinator import sync_worker
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.service import Coordinator
from test_native_git_checkouts import repo  # noqa: F401
from test_native_service import root  # noqa: F401
from test_native_service_sync import title

pytestmark = pytest.mark.xdist_group('heavy_processes')
REL = 'tasks/test-epic-001.md'


def test_sync_timeout_is_bounded_and_carried_over_ipc(repo, monkeypatch):
    seen = []
    original = sync_worker._synchronize

    def spy(owner, **arguments):
        seen.append(arguments.get('timeout'))
        return original(owner, **arguments)
    monkeypatch.setattr(sync_worker, '_synchronize', spy)
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        assert client.sync(timeout=45)['state'] == 'synchronized'
        assert client.sync()['state'] == 'synchronized'
        for bad in (0, -1, 3601, True, '20'):
            with pytest.raises(ValueError, match='sync timeout'):
                client.sync(timeout=bad)
    assert seen == [45, sync_worker.SYNC_TIMEOUT]
    assert sync_worker.SYNC_TIMEOUT >= 60


def test_transport_waits_for_the_sync_budget(repo, monkeypatch):
    with Coordinator(repo) as owner:
        def slow(**arguments):
            time.sleep(1.5)
            return {'state': 'pending', 'timeout': arguments['timeout']}
        monkeypatch.setattr(owner, 'sync', slow)
        client = Client(repo, autostart=False, timeout=1)
        assert client.sync(timeout=10) == {'state': 'pending', 'timeout': 10}


def test_managed_git_pre_sync_uses_the_requested_budget(repo, monkeypatch):
    with Coordinator(repo) as owner:
        seen = []

        def refused(**arguments):
            seen.append(arguments.get('timeout'))
            return {'state': 'pending', 'notices': ['sync pending: time budget exhausted']}
        monkeypatch.setattr(owner, 'sync', refused)
        client = Client(repo, autostart=False, timeout=120)
        result = client.git_run(kind='commit', message='budget', sync_timeout=90)
        assert result['state'] == 'refused' and result['sync']['state'] == 'pending'
        result = client.git_run(kind='commit', message='default budget')
        assert result['state'] == 'refused'
        with pytest.raises(ValueError, match='sync timeout'):
            client.git_run(kind='commit', message='bad', sync_timeout=0)
    assert seen == [90, sync_worker.SYNC_TIMEOUT]


def test_exhausted_budget_reports_pending_with_the_file(repo):
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        assert client.sync()['state'] == 'synchronized'
        path = repo / '.taskmaster' / REL
        path.write_bytes(path.read_bytes().replace(b'Service task', b'Too slow'))
        owner.checkpoint = lambda stage: time.sleep(1.2) if stage == 'sync_files_selected' else None
        result = client.sync(timeout=1)
        assert result['state'] == 'pending'
        assert any('time budget' in notice for notice in result['notices']), result
        assert title(repo) == 'Service task'
