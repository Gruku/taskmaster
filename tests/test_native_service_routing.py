"""Production routing gates for the N12 switch (not the low-level N11 oracle)."""
import json
import threading
import urllib.error
import urllib.request
from contextlib import closing

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.protocol import ServiceUnavailable, connect
from taskmaster.coordinator.service import Coordinator
from taskmaster.native import commands
from taskmaster.native_routing import runtime
from test_native_service import root  # noqa: F401


def test_production_read_handle_is_query_only_without_a_running_owner(root):
    with runtime.open_call(root / '.taskmaster/local/store.db', root / '.taskmaster', 'read-only') as call:
        assert call.connection.execute('PRAGMA query_only').fetchone()[0] == 1
        with call.read() as snapshot:
            assert snapshot.identity['store_id']
    assert not (root / '.taskmaster/local/coordinator/discovery.json').exists()


def test_public_mutation_runs_only_in_the_owned_writer_and_returns_pending(root, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return []
    original = commands.execute
    def execute(*args, **kwargs):
        assert threading.current_thread().name == 'taskmaster-writer'
        return original(*args, **kwargs)
    monkeypatch.setattr(commands, 'execute', execute)
    def no_flush(*args, **kwargs):
        raise AssertionError('normal native tools must not wait for publication')
    monkeypatch.setattr(Coordinator, 'flush', no_flush)
    owner = Coordinator(root, exporter=blocked).start()
    try:
        assert entered.wait(5)
        result = bs.backlog_update_task(task_id='test-epic-001', field='title', value='Native IPC route')
        assert not result.startswith('Error:'), result
        assert 'export pending' in result
    finally:
        release.set()
        owner.close()


def test_public_viewer_mutation_returns_before_background_export(root, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return []
    original = commands.execute
    def execute(*args, **kwargs):
        assert threading.current_thread().name == 'taskmaster-writer'
        return original(*args, **kwargs)
    monkeypatch.setattr(commands, 'execute', execute)
    def no_flush(*args, **kwargs):
        raise AssertionError('normal native viewer writes must not wait for publication')
    monkeypatch.setattr(Coordinator, 'flush', no_flush)
    owner = Coordinator(root, exporter=blocked).start()
    server = thread = None
    try:
        server, port = bs._make_server(host='127.0.0.1', port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        assert entered.wait(5)
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/tasks/test-epic-001',
                                         data=json.dumps({'title': 'Viewer through IPC'}).encode(), method='PATCH',
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            assert json.loads(response.read())['export_pending']
    finally:
        release.set()
        if server is not None:
            if thread is not None and thread.is_alive():
                server.shutdown()
                thread.join()
            server.server_close()
        owner.close()


def test_public_completion_retry_logs_once_and_exports_progress_after_release(root, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(20)
        return []
    owner = Coordinator(root, exporter=blocked).start()
    captured = []
    original = Client.execute
    def execute(client, envelope):
        captured.append(envelope)
        return original(client, envelope)
    monkeypatch.setattr(Client, 'execute', execute)
    try:
        assert entered.wait(5)
        for result in (
            bs.backlog_update_task(task_id='test-epic-001', field='lane', value='express'),
            bs.backlog_record_gate(task_id='test-epic-001', gate='review-gate', verdict='pass'),
            bs.backlog_pick_task(task_id='test-epic-001'),
        ):
            assert not result.startswith('Error:'), result
        result = bs.backlog_complete_task(task_id='test-epic-001', session_title='IPC completion once', done='- durable work')
        assert not result.startswith('Error:'), result
        assert 'export pending' in result
        envelope = next(value for value in captured if value['operation'] == 'task.complete')
        client = Client(root, autostart=False)
        receipt = original(client, envelope)['receipt']
        with closing(connect(root, readonly=True)) as connection:
            paragraphs = connection.execute("SELECT value_json FROM sync_state WHERE key LIKE 'progress.pending.%'").fetchall()
        assert sum('IPC completion once' in row[0] for row in paragraphs) == 1
        owner.exporter = None
        release.set()
        assert client.flush(receipt['commit_seq'])['state'] == 'exported'
        text = (root / '.taskmaster/local/PROGRESS.md').read_text(encoding='utf-8')
        assert text.count('IPC completion once') == 1
    finally:
        release.set()
        owner.close()


def test_public_conflict_notices_and_resolution_go_through_the_coordinator(root):
    with Coordinator(root):
        client = Client(root, autostart=False)
        def flush_current():
            with closing(connect(root, readonly=True)) as connection:
                high = connection.execute('SELECT COALESCE(MAX(seq),0) FROM domain_events').fetchone()[0]
            return client.flush(high)
        assert flush_current()['state'] == 'exported'
        path = root / '.taskmaster/tasks/test-epic-001.md'
        edited = path.read_bytes() + b'\nHand edited before the native command.\n'
        path.write_bytes(edited)
        result = bs.backlog_update_task(task_id='test-epic-001', field='notes', value='Store version through IPC')
        assert not result.startswith('Error:'), result
        # Establish the durable flag, whether the background exporter or this
        # first barrier discovers the hand edit. Later barriers must still name
        # it after its job has moved out of the pending/claimed states.
        flush_current()
        for _ in range(3):
            barrier = flush_current()
            assert barrier['state'] == 'pending'
            assert any('tasks/test-epic-001.md' in notice for notice in barrier['notices'])
        assert path.read_bytes() == edited
        listing = bs.backlog_resolve_conflict()
        assert 'tasks/test-epic-001.md' in listing
        unrelated = bs.backlog_status()
        assert 'Warning:' in unrelated and 'tasks/test-epic-001.md' in unrelated
        result = bs.backlog_resolve_conflict(file='tasks/test-epic-001.md', take='store')
        assert result.startswith('Resolved'), result
        assert flush_current()['state'] == 'exported'
        assert bs.backlog_resolve_conflict() == 'No flagged files.'
        assert b'Store version through IPC' in path.read_bytes()
        assert b'Hand edited' not in path.read_bytes()


def unavailable_transport(monkeypatch):
    requests = []
    def forbidden(*args, **kwargs):
        raise AssertionError('an unavailable coordinator must never enable a direct writer fallback')
    monkeypatch.setattr(commands, 'execute', forbidden)
    def fail(client, method, **arguments):
        assert method == 'execute'
        requests.append(arguments['envelope']['request_id'])
        raise ServiceUnavailable('transport unavailable; outcome may have committed')
    monkeypatch.setattr(Client, 'call', fail)
    return requests


def test_public_tool_reports_unavailable_owner_with_the_recoverable_request_id(root, monkeypatch):
    requests = unavailable_transport(monkeypatch)
    result = bs.backlog_update_task(task_id='test-epic-001', field='title', value='Must not bypass service')
    assert result.startswith('Error:'), result
    assert len(requests) == 1 and requests[0] in result


def test_viewer_returns_structured_503_for_ambiguous_coordinator_failure(root, monkeypatch):
    requests = unavailable_transport(monkeypatch)
    server, port = bs._make_server(host='127.0.0.1', port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/tasks/test-epic-001',
                                         data=json.dumps({'title': 'Must not bypass service'}).encode(), method='PATCH',
                                         headers={'Content-Type': 'application/json'})
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            assert response.status == 503
            payload = json.loads(response.read())
        assert payload['may_have_committed'] is True
        assert payload['request_id'] == requests[0]
        assert 'same request' in payload['error'].lower()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
