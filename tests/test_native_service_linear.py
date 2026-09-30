"""N12 Linear retry acceptance: fake transport only, real coordinator/writer."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import threading

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.protocol import connect
from taskmaster.coordinator.service import Coordinator
from taskmaster.integrations.linear.client import LinearAPIError
from test_native_service import root, request  # noqa: F401


@pytest.fixture(autouse=True)
def no_real_linear(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('this test may never construct a live Linear transport')
    monkeypatch.setattr('taskmaster.integrations.linear.client.LinearClient', forbidden)


def configure(root, monkeypatch):
    monkeypatch.setenv('TASKMASTER_TEST_LINEAR_TOKEN', 'test-token-not-a-credential')
    (root / '.taskmaster/linear.yaml').write_text(
        'version: 1\ndefault_workspace: test\nworkspaces:\n'
        '  - alias: test\n    team_id: team-test\n    token_env: TASKMASTER_TEST_LINEAR_TOKEN\n'
        '    status_mapping:\n      todo: status-todo\n      in-progress: status-progress\n'
        '    priority_mapping:\n      medium: 3\n', encoding='utf-8')


def command(client, key, operation, arguments):
    envelope = request(client, key)
    envelope.update(operation=operation, arguments=arguments)
    return client.execute(envelope)['receipt']


def link_and_queue(client):
    command(client, 'link', 'linear.link', {'task_id': 'test-epic-001', 'external_key': 'ENG-1', 'workspace_alias': 'test'})
    command(client, 'edit', 'task.update', {'id': 'test-epic-001', 'field': 'title', 'value': 'First push'})


def rows(root):
    with closing(connect(root, readonly=True)) as connection:
        return connection.execute('SELECT seq,state,attempts,claimed_by FROM linear_queue ORDER BY seq').fetchall()


def factory(calls, action=None):
    class Fake:
        def __init__(self, *, token):
            assert token == 'test-token-not-a-credential'
        def issue_upsert(self, team, payload):
            assert threading.current_thread().name == 'taskmaster-linear'
            calls.append((team, dict(payload)))
            if action:
                action(payload)
            return {'id': payload['id'], 'identifier': 'ENG-1'}
    return Fake


def test_explicit_retry_uses_worker_and_duplicate_receipt_does_not_push_again(root, monkeypatch):
    configure(root, monkeypatch)
    calls = []
    with Coordinator(root, linear_client_factory=factory(calls)):
        client = Client(root, autostart=False)
        link_and_queue(client)
        assert not calls  # Ordinary native mutations never authorize remote I/O.
        result = client.linear_retry(caller_scope='linear-tests', request_id='retry-one')
        assert result['counts']['ok'] == 1
        assert rows(root)[0][1:] == ('done', 0, None)
        assert client.linear_retry(caller_scope='linear-tests', request_id='retry-one') == result
        assert len(calls) == 1
        (root / '.taskmaster/linear.yaml').unlink()
        monkeypatch.delenv('TASKMASTER_TEST_LINEAR_TOKEN')
        assert client.linear_retry(caller_scope='linear-tests', request_id='retry-one') == result
        with pytest.raises(ValueError, match='different payload|different Linear retry'):
            client.linear_retry(caller_scope='linear-tests', request_id='retry-one', target_id='test-epic-001')


def test_public_linear_retry_routes_to_owned_worker(root, monkeypatch):
    configure(root, monkeypatch)
    calls = []
    with Coordinator(root, linear_client_factory=factory(calls)):
        link_and_queue(Client(root, autostart=False))
        result = json.loads(bs.backlog_linear(action='retry', target_id='test-epic-001'))
        assert result['ok'] and result['counts']['ok'] == 1
        assert len(calls) == 1


def test_public_pending_retry_recovers_same_batch_even_from_a_new_session(root, monkeypatch):
    from taskmaster.coordinator.linear_worker import LinearWorker
    configure(root, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def blocked(payload):
        entered.set()
        assert release.wait(10)
    monkeypatch.setattr(LinearWorker, 'RESPONSE_WAIT_SECONDS', 0)
    with Coordinator(root, linear_client_factory=factory(calls, blocked)):
        link_and_queue(Client(root, autostart=False))
        try:
            pending = json.loads(bs.backlog_linear(action='retry', target_id='test-epic-001'))
            assert pending['pending'] and pending['request_id'] and pending['caller_scope']
            assert entered.wait(5)
        finally:
            release.set()
        monkeypatch.setattr(LinearWorker, 'RESPONSE_WAIT_SECONDS', 20)
        monkeypatch.setattr(bs, 'SESSION_ID', 'a-new-session')
        recovered = json.loads(bs.backlog_linear(action='retry', target_id='test-epic-001',
                                                request_id=pending['request_id'], caller_scope=pending['caller_scope']))
        assert recovered['ok'] and recovered['counts']['ok'] == 1
        assert len(calls) == 1


def test_edit_during_remote_push_is_not_settled_unsent_and_writer_is_free(root, monkeypatch):
    configure(root, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def blocked(payload):
        # The request holds neither a read snapshot nor the database writer.
        with closing(connect(root)) as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.rollback()
        if len(calls) == 1:
            entered.set()
            assert release.wait(15)
    with Coordinator(root, linear_client_factory=factory(calls, blocked)) as owner:
        client = Client(root, autostart=False)
        link_and_queue(client)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.linear_retry, caller_scope='linear-tests', request_id='blocked')
            try:
                assert entered.wait(5)
                assert not owner.pending  # No database write is in flight.
                assert not owner.idle_expired(0)  # Even a zero idle threshold must retain remote work.
                from taskmaster.coordinator.ownership import OwnershipUnavailable
                with pytest.raises(OwnershipUnavailable):
                    with Coordinator(root):
                        pass
                command(client, 'racing-edit', 'task.update', {'id': 'test-epic-001', 'field': 'title', 'value': 'Second push'})
                assert [row[1] for row in rows(root)] == ['claimed', 'pending']
            finally:
                release.set()
            assert future.result(timeout=10)['counts']['ok'] == 1
        assert [row[1] for row in rows(root)] == ['done', 'pending']
        assert client.linear_retry(caller_scope='linear-tests', request_id='next')['counts']['ok'] == 1
        assert [payload['title'] for _, payload in calls] == ['First push', 'Second push']


def test_target_filter_precedes_batch_limit_and_never_resets_other_claims(root, monkeypatch):
    configure(root, monkeypatch)
    # Explicit fixture state; no coordinator or concurrent writer exists yet.
    with closing(connect(root)) as connection:
        connection.executemany("INSERT INTO linear_queue(op,target_id,payload,state,attempts) VALUES('task_upsert',?,'{}','claimed',3)",
                               [(f'other-{index}',) for index in range(510)])
    calls = []
    with Coordinator(root, linear_client_factory=factory(calls)):
        client = Client(root, autostart=False)
        link_and_queue(client)
        before = rows(root)[:510]
        result = client.linear_retry(caller_scope='linear-tests', request_id='target-only', target_id='test-epic-001')
        assert result['counts']['ok'] == 1
        assert rows(root)[:510] == before
        assert len(calls) == 1


def test_live_claim_is_not_unparked_and_stale_owner_cannot_settle(root, monkeypatch):
    configure(root, monkeypatch)
    with Coordinator(root, linear_client_factory=factory([])):
        client = Client(root, autostart=False)
        link_and_queue(client)
        held = command(client, 'claim', 'linear.claim', {'owner': 'first', 'reset': True})['result']
        second = command(client, 'other-claim', 'linear.claim', {'owner': 'second', 'reset': True})['result']
        assert not second['items'] and second['in_flight_skipped'] == 1
        seq = held['items'][0]['seq']
        refused = command(client, 'wrong-settle', 'linear.settle', {'owner': 'second', 'seq': seq, 'status': 'ok'})
        assert refused['result'] == {'applied': False}
        assert rows(root)[0][1:] == ('claimed', 0, 'first')


def test_tracker_body_and_unknown_fields_survive_successful_settlement(root, monkeypatch):
    configure(root, monkeypatch)
    with Coordinator(root, linear_client_factory=factory([])):
        client = Client(root, autostart=False)
        link_and_queue(client)
    # Explicit fixture-only state while no owner exists. There is currently no
    # public tracker prose editor. Settlement must retain imported prose/fields.
    with closing(connect(root)) as connection:
        connection.execute('BEGIN IMMEDIATE')
        key = connection.execute("SELECT entity_key FROM entity_core WHERE kind='tracker' AND public_id='linear-test-eng-1'").fetchone()[0]
        connection.execute('UPDATE entity_documents SET body=? WHERE entity_key=?', ('Keep tracker narrative', key))
        connection.execute('INSERT INTO entity_extensions(entity_key,field,value_json) VALUES(?,?,?)',
                           (key, 'private_context', json.dumps({'keep': True})))
        connection.commit()
    with Coordinator(root, linear_client_factory=factory([])):
        client = Client(root, autostart=False)
        assert client.linear_retry(caller_scope='linear-tests', request_id='body')['counts']['ok'] == 1
        from taskmaster.native.queries import Repository
        with closing(connect(root, readonly=True)) as connection:
            with Repository(connection).snapshot() as snapshot:
                tracker = snapshot.get('tracker', 'linear-test-eng-1', include_body=True)
        assert tracker['body'] == 'Keep tracker narrative'
        assert tracker['fields']['private_context'] == {'keep': True}
        assert tracker['fields']['linear_issue_id'] == 'ENG-1'


def test_missing_token_does_not_claim_or_park_fresh_retry(root, monkeypatch):
    configure(root, monkeypatch)
    with Coordinator(root, linear_client_factory=factory([])):
        client = Client(root, autostart=False)
        link_and_queue(client)
        before = rows(root)
        monkeypatch.delenv('TASKMASTER_TEST_LINEAR_TOKEN')
        with pytest.raises(ValueError, match='not set'):
            client.linear_retry(caller_scope='linear-tests', request_id='no-token')
        assert rows(root) == before


def test_worker_exception_is_recoverable_with_the_original_finite_batch(root, monkeypatch):
    from taskmaster.coordinator.protocol import ServiceUnavailable
    configure(root, monkeypatch)
    calls = []
    def fail_once(payload):
        if len(calls) == 1:
            raise RuntimeError('injected worker failure before remote success')
    with Coordinator(root, linear_client_factory=factory(calls, fail_once)):
        client = Client(root, autostart=False)
        link_and_queue(client)
        with pytest.raises(ServiceUnavailable, match='recoverable'):
            client.linear_retry(caller_scope='linear-tests', request_id='recoverable')
        assert rows(root)[0][1] == 'claimed'
        # The ordinary writer remains usable after the worker failure.
        command(client, 'unrelated', 'note.create', {'text': 'Writer still available'})
        result = client.linear_retry(caller_scope='linear-tests', request_id='recoverable')
        assert result['counts']['ok'] == 1 and rows(root)[0][1] == 'done'
        assert len(calls) == 2


@pytest.mark.parametrize('migrated_hint', [False, True])
def test_relink_before_retry_preserves_the_new_tracker_push(root, monkeypatch, migrated_hint):
    configure(root, monkeypatch)
    calls = []
    with Coordinator(root, linear_client_factory=factory(calls)):
        client = Client(root, autostart=False)
        link_and_queue(client)
        command(client, 'unlink', 'linear.unlink', {'task_id': 'test-epic-001'})
        command(client, 'relink', 'linear.link', {'task_id': 'test-epic-001', 'external_key': 'ENG-2', 'workspace_alias': 'test'})
    if migrated_hint:
        # Old queue rows permit NULL hints; seed one while no owner is running.
        with closing(connect(root)) as connection:
            connection.execute('UPDATE linear_queue SET tracker_id=NULL')
    with Coordinator(root, linear_client_factory=factory(calls)):
        client = Client(root, autostart=False)
        result = client.linear_retry(caller_scope='linear-tests', request_id='relinked')
        assert result['counts']['ok'] == 1
        assert len(calls) == 1 and calls[0][1]['id'] == 'ENG-2'
        assert all(row[1] == 'done' for row in rows(root))


def test_retained_linear_intent_never_pushes_a_now_jira_linked_task(root, monkeypatch):
    from taskmaster.taskmaster_v3 import build_tracker_doc
    from taskmaster.native.migrate import _put_entity
    from taskmaster.native.commands import _write_field
    configure(root, monkeypatch)
    calls = []
    with Coordinator(root, linear_client_factory=factory(calls)):
        link_and_queue(Client(root, autostart=False))
    # Seed a migrated non-Linear linkage while the service is stopped. The old
    # queue hint deliberately still names the former Linear tracker.
    document = build_tracker_doc(external_system='jira', instance_alias='test', external_key='ENG-3',
                                 title='Existing Jira issue', status='todo')
    with closing(connect(root)) as connection:
        connection.execute('BEGIN IMMEDIATE')
        _put_entity(connection, dict(kind='tracker', id='jira-test-eng-3', doc=document, body='',
                                     rev=1, updated_seq=0, archived=0, deleted=0,
                                     epic=None, status=document['status']))
        key = connection.execute("SELECT entity_key FROM entity_core WHERE kind='task' AND public_id='test-epic-001'").fetchone()[0]
        _write_field(connection, key, 'task', 'tracker_id', 'jira-test-eng-3')
        connection.commit()
    with Coordinator(root, linear_client_factory=factory(calls)):
        result = Client(root, autostart=False).linear_retry(caller_scope='linear-tests', request_id='not-linear')
        assert result['counts']['skipped'] == 1
        assert not calls and rows(root)[0][1] == 'done'


@pytest.mark.parametrize('permanent,bucket,state', [(False, 'transient', 'pending'), (True, 'permanent', 'failed')])
def test_remote_error_is_settled_once_and_only_explicit_retry_resets_it(root, monkeypatch, permanent, bucket, state):
    configure(root, monkeypatch)
    calls = []
    def fail(payload):
        raise LinearAPIError('fake remote failure', permanent=permanent)
    with Coordinator(root, linear_client_factory=factory(calls, fail)):
        client = Client(root, autostart=False)
        link_and_queue(client)
        result = client.linear_retry(caller_scope='linear-tests', request_id='failed')
        assert result['counts'][bucket] == 1
        assert rows(root)[0][1:3] == (state, 1)
        assert client.linear_retry(caller_scope='linear-tests', request_id='failed') == result
        assert len(calls) == 1
        assert client.linear_retry(caller_scope='linear-tests', request_id='explicit-retry')['counts'][bucket] == 1
        assert len(calls) == 2 and rows(root)[0][2] == 1
