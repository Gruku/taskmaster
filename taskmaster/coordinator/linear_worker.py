"""Explicit Linear retries on the coordinator's separate remote-work thread.

Ordinary writes only enqueue local intent. No automatic remote call happens on
startup or export; an explicit retry authorizes a finite batch. Domain/queue
mutations go through the same writer as every other native command. A remote
ack lost before local settlement is at-least-once, not remote exactly-once.
"""
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import queue
import threading

from taskmaster.native import contracts
from taskmaster.native.queries import Repository
from .protocol import CoordinatorStopping, ServiceUnavailable


class LinearWorker:
    RESPONSE_WAIT_SECONDS = 20

    def __init__(self, coordinator, *, client_factory=None):
        self.coordinator = coordinator
        self.client_factory = client_factory
        self.queue = queue.Queue(maxsize=16)
        self.jobs = {}
        self.guard = threading.Lock()

    def submit(self, caller_scope, request_id, target_id=''):
        # Reuse the ordinary validation/receipt contract for the stable logical
        # claim. Reusing an ID with a new target fails before another push.
        envelope = self._envelope(caller_scope, request_id, 'linear.claim', {
            'owner': self._owner(caller_scope, request_id), 'target_id': target_id, 'reset': True})
        envelope, _ = contracts.validate(envelope)
        key = (caller_scope, request_id)
        with self.guard:
            previous = self.jobs.get(key)
            if previous is not None:
                old, future = previous
                if old != envelope:
                    raise contracts.Conflict('request_id reused with a different Linear retry')
                return future
            if self.coordinator.stopping.is_set():
                raise CoordinatorStopping('coordinator is stopping; retry the same request_id')
            future = Future()
            try:
                self.queue.put_nowait((key, envelope, future))
            except queue.Full:
                raise ServiceUnavailable('Linear retry queue is full; retry the same request_id') from None
            self.jobs[key] = (envelope, future)
            return future

    def _owner(self, caller_scope, request_id):
        raw = json.dumps([caller_scope, request_id], separators=(',', ':')).encode()
        return 'linear:' + hashlib.sha256(raw).hexdigest()

    def _envelope(self, caller_scope, request_id, operation, arguments):
        return dict(protocol=2, store_id=self.coordinator.identity['store_id'], caller_scope=caller_scope,
                    request_id=request_id, operation=operation, arguments=arguments, expected_revisions=[])

    def response(self, caller_scope, request_id, target_id=''):
        future = self.submit(caller_scope, request_id, target_id)
        try:
            return future.result(timeout=self.RESPONSE_WAIT_SECONDS)
        except FutureTimeout:
            return {'ok': True, 'pending': True, 'request_id': request_id, 'caller_scope': caller_scope,
                    'note': 'remote work continues; retry this same request_id to inspect its outcome'}

    def run(self):
        owner = self.coordinator
        while not owner.stopping.is_set():
            try:
                key, envelope, future = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                future.set_result(self._drain(envelope))
            except Exception as exc:
                future.set_exception(exc)
            finally:
                with self.guard:
                    self.jobs.pop(key, None)  # durable receipts resolve later retries
                self.queue.task_done()
        # No queued caller should wait forever when the owner is retiring.
        with self.guard:
            for _, future in self.jobs.values():
                future.set_exception(ServiceUnavailable('coordinator stopped; retry the same request_id'))
            self.jobs.clear()

    def _drain(self, envelope):
        from taskmaster.native import receipts
        # A completed retry is recoverable without today's configuration or
        # credentials. For a fresh batch preserve legacy's missing-token
        # preflight: do not park/change its queue before reporting that error.
        _, fingerprint = contracts.validate(envelope)
        with closing(self.coordinator._connect(readonly=True)) as connection:
            previous = receipts.lookup(connection, envelope, fingerprint)
        config = self._config() if previous is None else None
        receipt = self.coordinator.submit(envelope).result()
        batch = receipt['result']
        counts = dict(ok=0, skipped=0, transient=0, permanent=0, unknown=0)
        for item in batch['items']:
            if self.coordinator.stopping.is_set():
                raise ServiceUnavailable('Linear retry interrupted; retry the same request_id')
            settle_id = self._owner(envelope['caller_scope'], envelope['request_id']) + ':' + str(item['seq'])
            with closing(self.coordinator._connect(readonly=True)) as connection:
                prior = connection.execute('SELECT outcome_json FROM command_receipts WHERE store_id=? AND caller_scope=? AND request_id=?',
                                           (envelope['store_id'], 'linear-settle', settle_id)).fetchone()
                held = connection.execute('SELECT state,claimed_by FROM linear_queue WHERE seq=?', (item['seq'],)).fetchone()
            if prior:
                result = json.loads(prior[0])['result']
            elif held == ('claimed', envelope['arguments']['owner']):
                if config is None:
                    config = self._config()
                outcome = self._push(item, config)
                arguments = dict(outcome, seq=item['seq'], owner=envelope['arguments']['owner'])
                result = self.coordinator.submit(self._envelope('linear-settle', settle_id, 'linear.settle', arguments)).result()['result']
            else:
                continue  # A later explicit retry owns this row; never settle it.
            if result.get('applied'):
                counts[result['bucket']] += 1
        return dict(ok=True, counts=counts, in_flight_skipped=batch['in_flight_skipped'],
                    request_id=envelope['request_id'], caller_scope=envelope['caller_scope'])

    def _config(self):
        from taskmaster import taskmaster_v3 as domain
        config = domain.load_linear_config(self.coordinator.root / '.taskmaster/backlog.yaml')
        if config is None:
            raise ValueError('linear.yaml not found — run backlog_linear_bootstrap_apply first.')
        domain.resolve_linear_token(domain.get_linear_workspace(config))
        return config

    def _push(self, item, config):
        from taskmaster import taskmaster_v3 as domain
        from taskmaster.integrations.linear.client import LinearAPIError, LinearClient
        from taskmaster.integrations.linear.mapper import compute_push_hash, tm_task_to_linear_payload
        if item['op'] != 'task_upsert':
            return dict(status='error:permanent', reason=f"unknown op {item['op']!r}")
        # Read after claim, with no read transaction surviving the remote call.
        with closing(self.coordinator._connect(readonly=True)) as connection:
            with Repository(connection).snapshot() as snapshot:
                try:
                    task = snapshot.get('task', item['target_id'])['fields']
                except KeyError:
                    return dict(status='skipped:not_found')
                tracker_id = task.get('tracker_id')
                if (not isinstance(tracker_id, str) or not tracker_id.startswith('linear-')
                        or tracker_id != item['tracker_id']):
                    return dict(status='skipped:no_tracker')
                try:
                    tracker = snapshot.get('tracker', tracker_id)['fields']
                except KeyError:
                    return dict(status='error:permanent', reason=f'cannot read tracker {tracker_id}: not found')
                if tracker.get('external_system') != 'linear':
                    return dict(status='skipped:no_tracker')
        client = None
        try:
            workspace = domain.get_linear_workspace(config, tracker.get('instance_alias'))
            issue_id = tracker.get('linear_issue_id') or tracker.get('external_key')
            payload = tm_task_to_linear_payload(task, workspace, linear_issue_id=issue_id)
            digest = compute_push_hash(payload)
            if digest == tracker.get('push_hash'):
                return dict(status='skipped:unchanged')
            token = domain.resolve_linear_token(workspace)
            client = (self.client_factory or LinearClient)(token=token)
            result = client.issue_upsert(workspace['team_id'], payload)
            updates = dict(last_pushed=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                           push_hash=digest, linear_issue_id=result.get('id') or issue_id,
                           title=task.get('title', ''), status=task.get('status', ''))
            return dict(status='ok', tracker_id=tracker_id, updates=updates)
        except ValueError as exc:
            return dict(status='error:permanent', reason=str(exc))
        except LinearAPIError as exc:
            return dict(status='error:permanent' if exc.permanent else 'error:transient', reason=str(exc))
        finally:
            # LinearClient currently owns an httpx client without a public close.
            transport = getattr(client, '_http', None)
            if transport is not None:
                transport.close()
