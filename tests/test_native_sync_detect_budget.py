"""User intent: the Git classification of a sync (`checkouts.detect`) honours the sync's time
budget and the coordinator's stopping signal. On a large cold checkout it used to read every
file with no budget, so one sync ran for many minutes and a coordinator asked to stop could not
exit. Now it answers pending ("retry the same sync id"), imports nothing, and a retry continues
from the files the interrupted pass already fingerprinted.
"""
import time

import pytest

from taskmaster.coordinator import checkouts
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.service import Coordinator
from test_native_git_checkouts import repo  # noqa: F401
from test_native_service import root  # noqa: F401
from test_native_service_sync import title

pytestmark = pytest.mark.xdist_group('heavy_processes')
REL = 'tasks/test-epic-001.md'


def _slow_reads(monkeypatch, seconds, before=None):
    """Every file the classification reads in full costs `seconds` (a cold disk)."""
    real = checkouts._scan_read
    reads = []

    def slow(scan, rel):
        reads.append(rel)
        if before is not None:
            before(len(reads))
        time.sleep(seconds)
        return real(scan, rel)
    monkeypatch.setattr(checkouts, '_scan_read', slow)
    return reads


def _cold(repo):
    """Forget the fingerprint cache so the classification reads every file again."""
    from taskmaster.coordinator import sync_files
    sync_files.cache_path(repo).unlink(missing_ok=True)


def _more_tasks(repo, count):
    text = (repo / '.taskmaster' / REL).read_text(encoding='utf-8')
    for number in range(2, 2 + count):
        ident = f'test-epic-{number:03d}'
        (repo / '.taskmaster' / f'tasks/{ident}.md').write_text(text.replace('test-epic-001', ident),
                                                                encoding='utf-8')


def _edit(repo):
    path = repo / '.taskmaster' / REL
    path.write_bytes(path.read_bytes().replace(b'Service task', b'Edited by hand'))


def test_an_exhausted_budget_during_git_classification_answers_pending_and_imports_nothing(repo, monkeypatch):
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        assert client.sync()['state'] == 'synchronized'
        _edit(repo)
        _cold(repo)
        _slow_reads(monkeypatch, 1.0)
        started = time.monotonic()
        result = client.sync(timeout=1)
        took = time.monotonic() - started
    assert result['state'] == 'pending', result
    assert any('Git classification' in notice and 'retry the same sync id' in notice
               for notice in result['notices']), result['notices']
    assert took < 4, took
    assert title(repo) == 'Service task'


def test_a_stopping_coordinator_ends_the_git_classification(repo, monkeypatch):
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        assert client.sync()['state'] == 'synchronized'
        _edit(repo)
        _cold(repo)
        _slow_reads(monkeypatch, 0.5, before=lambda count: owner.stopping.set() if count == 1 else None)
        started = time.monotonic()
        result = owner.sync(caller_scope='detect-tests', request_id='stopping', timeout=120)
        took = time.monotonic() - started
    assert result['state'] == 'pending', result
    assert any('Git classification' in notice for notice in result['notices']), result['notices']
    assert took < 5, took
    assert title(repo) == 'Service task'


def test_retries_of_the_same_sync_id_continue_the_classification_to_completion(repo, monkeypatch):
    _more_tasks(repo, 8)
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        assert client.sync()['state'] == 'synchronized'
        _edit(repo)
        _cold(repo)
        reads = _slow_reads(monkeypatch, 0.4)
        results = []
        for _ in range(15):
            results.append(client.sync(request_id='continued', timeout=1.5))
            if results[-1]['state'] == 'synchronized':
                break
    assert results[-1]['state'] == 'synchronized', results[-1]
    assert len(results) > 1, 'the first attempt was expected to run out of budget'
    # A retry reads only what the interrupted passes had not fingerprinted yet.
    assert len(reads) < 2 * len(set(reads)), reads
    assert title(repo) == 'Edited by hand'
