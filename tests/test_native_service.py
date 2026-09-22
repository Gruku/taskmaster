"""N12 IPC acceptance on disposable native twins; never activate a live root."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import threading

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native import contracts
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.service import Coordinator
from taskmaster.coordinator.protocol import HandshakeError
from native_twins import make_twins


@pytest.fixture
def root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, lambda: bs.backlog_add_task(title='Service task', epic='test-epic', phase='dev'), visibility=None)
    with twins.at(twins.native):
        yield twins.native


def request(client, key='one', title='Changed'):
    return {'protocol': 2, 'store_id': client.identity['store_id'], 'caller_scope': 'service-tests',
            'request_id': key, 'operation': 'task.patch', 'arguments': {'id': 'test-epic-001', 'set': {'title': title}},
            'expected_revisions': []}


def test_multiple_clients_share_owner_and_retry_receipt(root):
    with Coordinator(root) as owner:
        clients = [Client(root, autostart=False) for _ in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda c: c.execute(request(c)), clients))
        assert all(item['receipt'] == outcomes[0]['receipt'] for item in outcomes)
        assert {c.status()['nonce'] for c in clients} == {owner.nonce}
        recovered = clients[0].receipt('service-tests', 'one')
        assert recovered == {'state': 'committed', 'receipt': outcomes[0]['receipt']}
        assert clients[0].receipt('another-caller', 'one') == {'state': 'unknown'}
        with pytest.raises(contracts.Conflict, match='payload'):
            clients[0].execute(request(clients[0], title='Different retry'))


@pytest.mark.parametrize('truncated_replies', [1, 2])
def test_commit_then_truncated_response_retries_exact_key_or_reports_ambiguity(root, monkeypatch, truncated_replies):
    from contextlib import closing
    from taskmaster.coordinator import service
    from taskmaster.coordinator.protocol import connect, encode, ServiceUnavailable
    original_answer = service._Handler.answer
    remaining = [truncated_replies]
    def answer(handler, status, value):
        if status == 200 and 'receipt' in value.get('result', {}) and remaining[0]:
            remaining[0] -= 1
            raw = encode(value)
            handler.send_response(200)
            handler.send_header('Content-Length', str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw[:20])
            handler.wfile.flush()
            handler.close_connection = True
            return
        return original_answer(handler, status, value)
    monkeypatch.setattr(service._Handler, 'answer', answer)
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        envelope = request(client)
        submitted = []
        original_submit = owner.submit
        def submit(value):
            submitted.append(value)
            return original_submit(value)
        monkeypatch.setattr(owner, 'submit', submit)
        if truncated_replies == 1:
            assert client.execute(envelope)['receipt']['affected']
        else:
            with pytest.raises(ServiceUnavailable, match='same request_id'):
                client.execute(envelope)
        assert submitted == [envelope, envelope]
        with closing(connect(root, readonly=True)) as connection:
            assert connection.execute('SELECT COUNT(*) FROM command_receipts WHERE caller_scope=? AND request_id=?',
                                      ('service-tests', 'one')).fetchone()[0] == 1


def test_transport_capacity_refuses_without_starting_another_owner(root, monkeypatch):
    from taskmaster.coordinator import client as client_module
    from taskmaster.coordinator.protocol import ServiceUnavailable
    with Coordinator(root, handler_limit=1) as owner:
        # Hold the only handler permit, without flaky socket timing. Requests
        # still travel through the real server's overload response path.
        assert owner.server.handlers.acquire(blocking=False)
        def forbidden(*args):
            raise AssertionError('overloaded live owner must not spawn another process')
        monkeypatch.setattr(client_module, '_launch', forbidden)
        client = Client(root)
        try:
            with pytest.raises(ServiceUnavailable, match='capacity'):
                client.status()
        finally:
            owner.server.handlers.release()
        assert client.status()['nonce'] == owner.nonce


@pytest.mark.parametrize('failure_at', ['verify', 'exists', 'open'])
def test_unsafe_discovery_is_a_structured_refusal_without_owner_startup(root, monkeypatch, failure_at):
    from pathlib import Path
    from taskmaster.coordinator import client as client_module
    with Coordinator(root):
        client = Client(root)
        def unsafe(path, *args, **kwargs):
            if failure_at == 'verify' or path == root / '.taskmaster/local/coordinator/discovery.json':
                raise PermissionError('not owner-only')
            return original(path, *args, **kwargs)
        def forbidden(*args):
            raise AssertionError('unsafe discovery must not trigger owner startup')
        if failure_at == 'verify':
            monkeypatch.setattr(client_module, 'verify_private', unsafe)
        else:
            original = getattr(Path, failure_at)
            monkeypatch.setattr(Path, failure_at, unsafe)
        monkeypatch.setattr(client_module, '_launch', forbidden)
        with pytest.raises(HandshakeError, match='permissions') as failure:
            client.execute(request(client, key='unsafe-discovery'))
        assert failure.value.public_payload()['request_id'] == 'unsafe-discovery'
        assert failure.value.public_payload()['caller_scope'] == 'service-tests'


def test_multibyte_command_near_native_byte_limit_uses_the_same_ipc_budget(root):
    from taskmaster.coordinator.protocol import encode, MAX_MESSAGE_BYTES
    client = Client(root, autostart=False)
    envelope = request(client)
    envelope['arguments']['set'] = {'notes': '\u65e5' * 300000}
    validated, _ = contracts.validate(envelope)
    # Large valid UTF-8 commands must not double in size through ASCII escaping.
    raw = encode({'identity': dict(client.identity, nonce='a' * 48), 'method': 'execute',
                  'envelope': validated, 'visibility': 'native'})
    assert len(raw) < MAX_MESSAGE_BYTES
    assert json.loads(raw)['envelope'] == validated


def test_owner_retires_before_background_export_of_a_changed_store(root):
    from taskmaster.coordinator.protocol import connect
    from contextlib import closing
    calls, first = [], threading.Event()
    def export(*args, **kwargs):
        calls.append('export')
        first.set()
        return []
    with Coordinator(root, exporter=export) as owner:
        assert first.wait(5)
        with owner.publication:
            with closing(connect(root)) as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute("UPDATE native_manifest SET value='replacement-store' WHERE key='store_id'")
                connection.execute("UPDATE meta SET value='replacement-store' WHERE key='creation_token'")
                # The retained-table trigger invalidates staging on meta writes.
                # Model a different *ready* authority, not merely stale staging.
                connection.execute("UPDATE native_manifest SET value='ready' WHERE key='state'")
                connection.commit()
            before = len(calls)
            owner.export_needed.set()
        assert owner.stopping.wait(5)
        assert len(calls) == before


@pytest.mark.parametrize('replacement_ready', [True, False])
def test_barrier_rechecks_identity_after_waiting_for_publication(root, replacement_ready):
    from taskmaster.admission import UnsupportedStoreError
    from taskmaster.coordinator.protocol import connect, ServiceUnavailable
    from contextlib import closing
    entered, first = threading.Event(), threading.Event()
    def export(*args, **kwargs):
        first.set()
        return []
    with Coordinator(root, exporter=export) as owner, ThreadPoolExecutor(max_workers=1) as pool:
        assert first.wait(5)
        with owner.publication:
            def flush():
                entered.set()
                return owner.flush(0)
            pending = pool.submit(flush)
            assert entered.wait(5)
            with closing(connect(root)) as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute("UPDATE native_manifest SET value='replacement-store' WHERE key='store_id'")
                connection.execute("UPDATE meta SET value='replacement-store' WHERE key='creation_token'")
                if replacement_ready:
                    connection.execute("UPDATE native_manifest SET value='ready' WHERE key='state'")
                connection.commit()
        error = ServiceUnavailable if replacement_ready else UnsupportedStoreError
        message = 'authority changed' if replacement_ready else 'not ready'
        with pytest.raises(error, match=message):
            pending.result(timeout=5)
        assert owner.stopping.is_set()


def test_native_commit_returns_without_waiting_for_export(root):
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return []
    owner = Coordinator(root, exporter=blocked).start()
    try:
        assert entered.wait(5)
        client = Client(root, autostart=False, timeout=5)
        result = client.execute(request(client))
        assert result['projection']['state'] == 'pending'
        assert result['receipt']['affected'][0]['id'] == 'test-epic-001'
    finally:
        release.set()
        owner.close()


def test_queued_cancellation_has_no_effect_and_admitted_cancel_is_ambiguous(root):
    entered, release = threading.Event(), threading.Event()
    def checkpoint(stage):
        if stage == 'admitted':
            entered.set()
            assert release.wait(15)
    with Coordinator(root, checkpoint=checkpoint) as owner:
        client = Client(root, autostart=False)
        first = owner.submit(request(client))
        try:
            assert entered.wait(5)
            queued = owner.submit(request(client, key='queued', title='Never'))
            assert client.cancel('service-tests', 'queued') == {'state': 'cancelled_before_execution', 'may_have_committed': False}
            assert client.cancel('service-tests', 'one')['may_have_committed'] is True
        finally:
            release.set()
        first.result(timeout=5)
        with pytest.raises(contracts.CancelledBeforeExecution):
            queued.result(timeout=5)


def test_cancelling_a_queued_retry_does_not_deny_its_prior_commit(root):
    block, entered, release = threading.Event(), threading.Event(), threading.Event()
    def checkpoint(stage):
        if stage == 'admitted' and block.is_set():
            entered.set()
            assert release.wait(15)
    with Coordinator(root, checkpoint=checkpoint) as owner:
        client = Client(root, autostart=False)
        envelope = request(client)
        committed = client.execute(envelope)['receipt']
        block.set()
        blocker = owner.submit(request(client, key='blocker', title='Later change'))
        try:
            assert entered.wait(5)
            retry = owner.submit(envelope)
            assert client.cancel('service-tests', 'one') == {
                'state': 'already_committed', 'may_have_committed': True}
        finally:
            release.set()
        blocker.result(timeout=5)
        assert retry.result(timeout=5) == committed


@pytest.mark.parametrize('wrong_key', ['root', 'store_id', 'schema', 'protocol', 'service_protocol', 'nonce'])
def test_authentication_and_generation_mismatches_refuse_without_write(root, wrong_key):
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        record = owner.discovery()
        original_identity = dict(client.identity)
        if wrong_key == 'nonce':
            record = dict(record, nonce='0' * 48)
        else:
            old = client.identity[wrong_key]
            client.identity[wrong_key] = old + 1 if isinstance(old, int) else old + '-mismatch'
        try:
            with pytest.raises(HandshakeError):
                client._send(record, 'execute', envelope=request(client))
        finally:
            client.identity = original_identity
        assert client.receipt('service-tests', 'one') == {'state': 'unknown'}
        connection = http.client.HTTPConnection('127.0.0.1', record['port'])
        try:
            connection.request('POST', '/rpc', body=json.dumps({'method': 'execute'}))
            assert connection.getresponse().status == 403
        finally:
            connection.close()


def test_writer_exception_rolls_back_and_does_not_strand_following_request(root):
    failed = False
    def checkpoint(stage):
        nonlocal failed
        if stage == 'mutated' and not failed:
            failed = True
            raise RuntimeError('injected writer failure')
    with Coordinator(root, checkpoint=checkpoint):
        client = Client(root, autostart=False)
        from taskmaster.coordinator.protocol import ServiceUnavailable
        with pytest.raises(ServiceUnavailable, match='injected'):
            client.execute(request(client))
        assert client.execute(request(client))['receipt']['affected']


def test_explicit_legacy_visibility_uses_same_owner_and_exports(root):
    with Coordinator(root) as owner:
        client = Client(root, autostart=False, visibility='legacy')
        result = client.execute(request(client))
        assert result['projection']['state'] == 'exported'
        assert client.status()['nonce'] == owner.nonce
        path = next((root / '.taskmaster/tasks').rglob('test-epic-001.md'))
        assert 'Changed' in path.read_text(encoding='utf-8')


def test_cancelling_queued_work_releases_its_admission_slot(root):
    from taskmaster.coordinator.protocol import ServiceUnavailable
    entered, release = threading.Event(), threading.Event()
    def checkpoint(stage):
        if stage == 'admitted':
            entered.set()
            assert release.wait(15)
    with Coordinator(root, checkpoint=checkpoint, queue_limit=1) as owner:
        client = Client(root, autostart=False)
        first = owner.submit(request(client))
        try:
            assert entered.wait(5)
            second = owner.submit(request(client, key='two'))
            with pytest.raises(ServiceUnavailable, match='full'):
                owner.submit(request(client, key='three'))
            owner.cancel('service-tests', 'two')
            with pytest.raises(contracts.CancelledBeforeExecution):
                second.result(timeout=1)
            third = owner.submit(request(client, key='three'))
        finally:
            release.set()
        first.result(timeout=5)
        third.result(timeout=5)


def test_shutdown_cannot_overtake_an_in_progress_admission(root, monkeypatch):
    entered, release, closing_started = (threading.Event() for _ in range(3))
    owner = Coordinator(root).start()
    original_put = owner.queue.put_nowait
    def paused_put(work):
        entered.set()
        assert release.wait(15)
        return original_put(work)
    monkeypatch.setattr(owner.queue, 'put_nowait', paused_put)
    def close():
        closing_started.set()
        owner.close()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            submitted = pool.submit(owner.submit, request(Client(root, autostart=False)))
            try:
                assert entered.wait(5)
                closing = pool.submit(close)
                assert closing_started.wait(5)
                assert not owner.stopping.wait(0.15)
            finally:
                release.set()
            accepted = submitted.result(timeout=5)
            assert accepted.result(timeout=5)['affected']
            closing.result(timeout=15)
    finally:
        release.set()
        owner.close()


def test_shutdown_does_not_release_ownership_while_a_barrier_can_publish(root):
    from taskmaster.coordinator.ownership import Ownership, OwnershipUnavailable
    held, release = threading.Event(), threading.Event()
    owner = Coordinator(root).start()
    def pin():
        with owner.publication:
            held.set()
            assert release.wait(15)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pinned = pool.submit(pin)
        try:
            assert held.wait(5)
            closing = pool.submit(owner.close)
            assert owner.stopping.wait(5)
            assert owner.flush(0)['state'] == 'pending'
            with pytest.raises(OwnershipUnavailable):
                with Ownership(root):
                    pass
        finally:
            release.set()
        pinned.result(timeout=5)
        closing.result(timeout=15)
    with Ownership(root):
        pass
