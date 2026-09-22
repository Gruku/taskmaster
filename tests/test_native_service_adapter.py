"""New client adapter gates, prior to switching the production routing seam."""
import sqlite3
import threading

import pytest

from taskmaster.coordinator import adapter
from taskmaster.coordinator.protocol import ServiceUnavailable
from taskmaster.coordinator.service import Coordinator
from taskmaster.native import commands
from test_native_service import root  # noqa: F401


def opened(root, **kwargs):
    return adapter.open_call(root / '.taskmaster/local/store.db', root / '.taskmaster', 'adapter-tests', **kwargs)


def test_adapter_reads_are_readonly_and_do_not_start_or_discover_a_service(root, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('read constructed a coordinator client')
    monkeypatch.setattr(adapter, 'Client', forbidden)
    with opened(root) as call:
        assert call.connection.execute('PRAGMA query_only').fetchone()[0] == 1
        with call.read() as snapshot:
            assert snapshot.identity['store_id']
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            call.connection.execute("UPDATE native_manifest SET value='wrong' WHERE key='store_id'")


def test_adapter_native_command_is_commit_only_and_writer_runs_in_owned_thread(root, monkeypatch):
    from pathlib import Path
    from taskmaster import store
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return []
    original_execute = commands.execute
    writer_threads = []
    def execute(*args, **kwargs):
        writer_threads.append(threading.current_thread().name)
        return original_execute(*args, **kwargs)
    monkeypatch.setattr(commands, 'execute', execute)
    owner = Coordinator(root, exporter=blocked).start()
    try:
        assert entered.wait(5)
        def no_projection_work(*args, **kwargs):
            pytest.fail('normal native command rendered or scanned projection files')
        monkeypatch.setattr(store, 'render_entity_file', no_projection_work)
        monkeypatch.setattr(store, 'render_backlog_file', no_projection_work)
        monkeypatch.setattr(Path, 'glob', no_projection_work)
        monkeypatch.setattr(Path, 'rglob', no_projection_work)
        with opened(root) as call:
            receipt = call.execute('task.patch', {'id': 'test-epic-001', 'set': {'title': 'Through IPC'}})
            result = call.finish({'ok': True})
            assert receipt['affected'][0]['fields']['title'] == 'Through IPC'
            assert result['seq'] == receipt['commit_seq']
            assert 'export pending' in result['export_pending'][0]
            assert call.projections[-1]['state'] == 'pending'
        assert writer_threads == ['taskmaster-writer']
    finally:
        release.set()
        owner.close()


def test_adapter_explicit_compatibility_visibility_waits_for_same_service(root):
    with Coordinator(root), opened(root, visibility='legacy') as call:
        call.execute('task.patch', {'id': 'test-epic-001', 'set': {'title': 'Explicit visibility'}})
        assert call.projections[-1]['state'] == 'exported'
        assert not call.notices
        assert 'Explicit visibility' in (root / '.taskmaster/tasks/test-epic-001.md').read_text(encoding='utf-8')


def test_adapter_unavailable_owner_has_no_direct_writer_fallback(root, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ServiceUnavailable('owned service unavailable')
    def forbidden(*args, **kwargs):
        pytest.fail('adapter called an independent writer')
    monkeypatch.setattr(adapter, 'Client', unavailable)
    monkeypatch.setattr(commands, 'execute', forbidden)
    with opened(root) as call, pytest.raises(ServiceUnavailable, match='unavailable'):
        call.execute('task.patch', {'id': 'test-epic-001', 'set': {'title': 'Must not commit'}})
