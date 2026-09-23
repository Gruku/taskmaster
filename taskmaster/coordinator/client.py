"""Thin authenticated coordinator client; no direct-writer fallback."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from taskmaster.admission import UnsupportedStoreError
from taskmaster.native import contracts
from .ownership import ownership_held, verify_private
from .protocol import (HandshakeError, MAX_RESPONSE_BYTES, REPLY_MARGIN, SYNC_TIMEOUT, ServiceUnavailable,
                       encode, identify, validate_sync_timeout)

_START_LOCK = threading.Lock()


def _launch(root):
    package_root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, TASKMASTER_ROOT=str(root))
    environment['PYTHONPATH'] = str(package_root) + (os.pathsep + environment['PYTHONPATH'] if environment.get('PYTHONPATH') else '')
    flags = ({'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
             if os.name == 'nt' else {'start_new_session': True})
    return subprocess.Popen([sys.executable, '-m', 'taskmaster.coordinator.service', '--root', str(root)],
                            cwd=package_root, env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)


class Client:
    def __init__(self, root, *, autostart=True, visibility='native', timeout=30):
        self.root = Path(root).resolve(strict=True)
        self.identity = identify(self.root)
        if visibility not in ('native', 'legacy'):
            raise ValueError('visibility must be native or legacy')
        self.autostart, self.visibility, self.timeout = autostart, visibility, timeout

    def _discovery(self):
        path = self.root / '.taskmaster/local/coordinator/discovery.json'
        try:
            if not path.exists():
                raise FileNotFoundError(path)
            verify_private(path.parent)
            verify_private(path)
            with path.open('rb') as stream:
                raw = stream.read(8193)
            if len(raw) > 8192:
                raise HandshakeError('oversized coordinator discovery')
            record = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise HandshakeError('invalid coordinator discovery; inspect service recovery status') from exc
        except PermissionError as exc:
            raise HandshakeError('coordinator discovery permissions are unsafe or inaccessible; no writer fallback') from exc
        if not isinstance(record, dict) or any(record.get(k) != v for k, v in self.identity.items()):
            raise HandshakeError('discovery root/store/schema/protocol mismatch; no writer fallback')
        if (type(record.get('port')) is not int or not 0 < record['port'] < 65536 or
                not isinstance(record.get('token'), str) or len(record['token']) != 64 or
                not isinstance(record.get('nonce'), str) or len(record['nonce']) != 48):
            raise HandshakeError('invalid coordinator address or generation')
        return record

    def _send(self, record, method, *, wait=None, **arguments):
        """`wait` extends the reply timeout for a call whose own budget is longer."""
        payload = encode(dict(identity=dict(self.identity, nonce=record['nonce']), method=method, **arguments))
        connection = http.client.HTTPConnection('127.0.0.1', record['port'],
                                                timeout=self.timeout if wait is None else max(self.timeout, wait))
        try:
            connection.request('POST', '/rpc', body=payload,
                               headers={'Content-Type': 'application/json', 'Authorization': f"Bearer {record['token']}"})
            response = connection.getresponse()
            try:
                length = int(response.getheader('Content-Length', ''))
            except ValueError as exc:
                raise http.client.HTTPException('coordinator response has no valid length') from exc
            if not 0 < length <= MAX_RESPONSE_BYTES:
                raise ServiceUnavailable('invalid coordinator response length; command may have committed; retain request_id')
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            # Bounded HTTPResponse.read may return a short body without raising
            # IncompleteRead. Treat truncation/malformed replies as ambiguous
            # transport failures so the identical durable request is retried.
            if len(raw) != length:
                raise http.client.HTTPException('incomplete coordinator response')
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise http.client.HTTPException('malformed coordinator response') from exc
            if not isinstance(value, dict) or (response.status == 200 and 'result' not in value):
                raise http.client.HTTPException('invalid coordinator response shape')
            if response.status != 200:
                errors = {'Conflict': contracts.Conflict, 'CancelledBeforeExecution': contracts.CancelledBeforeExecution,
                          'ValueError': ValueError, 'KeyError': KeyError, 'HandshakeError': HandshakeError,
                          'UnsupportedStoreError': UnsupportedStoreError}
                raise errors.get(value.get('type'), ServiceUnavailable)(value.get('error', 'coordinator refused request'))
            return value['result']
        finally:
            connection.close()

    def _probe(self):
        """Return a ready discovery record, or None only on evidence that no owner is listening."""
        try:
            record = self._discovery()
            self._send(record, 'status')
            return record
        except (FileNotFoundError, ConnectionRefusedError):
            if not self.autostart:
                raise ServiceUnavailable('repository coordinator unavailable; start it or retry later') from None
            return None
        except (ConnectionError, TimeoutError, http.client.HTTPException) as exc:
            if not self.autostart:
                raise ServiceUnavailable('repository coordinator unavailable; start it or retry later') from None
            # A reset, timeout or garbled reply at a recorded address is a live
            # but unhealthy owner (e.g. overloaded), unless the kernel lock is
            # free: then the record is stale and its port may be reused.
            if ownership_held(self.root):
                raise ServiceUnavailable('repository coordinator is running but not responding; retry later') from exc
            return None

    def _ready(self):
        record = self._probe()
        if record is not None:
            return record
        with _START_LOCK:
            # Another client in this process may already have completed startup.
            record = self._probe()
            if record is not None:
                return record
            child = _launch(self.root)
            deadline = time.monotonic() + min(self.timeout, 15)
            while time.monotonic() < deadline:
                try:
                    record = self._discovery()
                    self._send(record, 'status')
                    child.poll()  # Reap a startup loser, never signal an arbitrary PID.
                    return record
                except (FileNotFoundError, ConnectionError, TimeoutError, http.client.HTTPException):
                    if child.poll() not in (None, 0):
                        break
                    time.sleep(0.05)
        raise ServiceUnavailable('coordinator startup unavailable; inspect .taskmaster/local/coordinator/service.log; no writer fallback')

    def call(self, method, *, wait=None, **arguments):
        # Retain identical arguments across transport retries. In particular,
        # never mint a new command request_id after an ambiguous disconnect.
        for attempt in range(2):
            record = self._ready()
            try:
                return self._send(record, method, wait=wait, **arguments)
            except (ConnectionError, TimeoutError, http.client.HTTPException):
                if attempt:
                    raise ServiceUnavailable('coordinator disconnected; retry the same request_id to recover its receipt') from None

    def execute(self, envelope):
        request, _ = contracts.validate(envelope)
        if request.get('request_id') is None:
            raise ValueError('IPC commands require a durable request_id')
        try:
            return self.call('execute', envelope=request, visibility=self.visibility)
        except ServiceUnavailable as exc:
            # Public adapters may have minted the ID on behalf of the caller;
            # expose it on ambiguity so the durable receipt is inspectable.
            raise type(exc)(
                f"{exc}; retry the same request to recover its receipt; "
                f"request_id={request['request_id']!r}, caller_scope={request['caller_scope']!r}",
                request_id=request['request_id'], caller_scope=request['caller_scope'],
                may_have_committed=exc.may_have_committed) from exc

    def status(self):
        return self.call('status')

    def receipt(self, caller_scope, request_id):
        return self.call('receipt', caller_scope=caller_scope, request_id=request_id)

    def linear_retry(self, *, caller_scope, request_id=None, target_id=''):
        request_id = uuid.uuid4().hex if request_id is None else request_id
        if not all(isinstance(value, str) and 1 <= len(value) <= 256 for value in (caller_scope, request_id)):
            raise ValueError('Linear retry requires caller_scope and request_id')
        if target_id:
            contracts._identifier(target_id, 'target_id')
        try:
            return self.call('linear_retry', caller_scope=caller_scope, request_id=request_id, target_id=target_id)
        except ServiceUnavailable as exc:
            raise type(exc)(f'{exc}; retry the same request; request_id={request_id!r}, caller_scope={caller_scope!r}',
                            request_id=request_id, caller_scope=caller_scope,
                            may_have_committed=exc.may_have_committed) from exc

    def linear_bootstrap(self, *, entry, default_workspace):
        """Add a Linear workspace to linear.yaml under the coordinator's publication
        boundary. Not a store command: an identical retry answers `unchanged`."""
        from .linear_config import validate
        validate(entry, default_workspace)
        return self.call('linear_bootstrap', entry=entry, default_workspace=default_workspace)

    def flush(self, through):
        return self.call('flush', through=through)

    def sync(self, *, import_files=True, through=0, files=None, take_file=False,
             caller_scope='explicit-sync', request_id=None, worktree=None, timeout=None):
        """`worktree` names a linked checkout of this repository: its edits are imported
        against its own bases and the published generation is copied into it.
        `timeout` is the sync's budget (default protocol.SYNC_TIMEOUT); the reply is
        awaited that long, and an exhausted budget answers `pending`."""
        from taskmaster.native.sync import validate_input
        request_id = uuid.uuid4().hex if request_id is None else request_id
        options = dict(import_files=import_files, through=through, files=files, take_file=take_file)
        validate_input(options)
        budget = validate_sync_timeout(SYNC_TIMEOUT if timeout is None else timeout)
        if timeout is not None:
            options['timeout'] = timeout
        if not all(isinstance(value, str) and 1 <= len(value) <= 256 for value in (caller_scope, request_id)):
            raise ValueError('sync requires caller_scope and request_id')
        try:
            extra = {} if worktree is None else {'worktree': str(Path(worktree).resolve())}
            return self.call('sync', caller_scope=caller_scope, request_id=request_id, wait=budget + REPLY_MARGIN,
                             **options, **extra)
        except ServiceUnavailable as exc:
            raise type(exc)(f'{exc}; inspect sync_status or retry the same sync id; '
                            f'request_id={request_id!r}, caller_scope={caller_scope!r}',
                            request_id=request_id, caller_scope=caller_scope,
                            may_have_committed=exc.may_have_committed) from exc

    def sync_status(self, caller_scope, request_id):
        return self.call('sync_status', caller_scope=caller_scope, request_id=request_id)

    def git_run(self, *, kind, message=None, ref=None, caller_scope='explicit-git', request_id=None, timeout=600,
                worktree=None, sync_timeout=None):
        """Managed commit/checkout under the coordinator's publication hold.

        A retry with the same request_id never repeats Git: it answers
        `in_progress` or the settled result. `sync_timeout` bounds the pre-sync."""
        request_id = uuid.uuid4().hex if request_id is None else request_id
        budget = validate_sync_timeout(SYNC_TIMEOUT if sync_timeout is None else sync_timeout)
        extra_sync = {} if sync_timeout is None else {'sync_timeout': sync_timeout}
        if not all(isinstance(value, str) and 1 <= len(value) <= 256 for value in (caller_scope, request_id)):
            raise ValueError('managed Git requires caller_scope and request_id')
        try:
            extra = {} if worktree is None else {'worktree': str(Path(worktree).resolve())}
            return self.call('git_run', kind=kind, message=message, ref=ref, caller_scope=caller_scope,
                             request_id=request_id, timeout=timeout, wait=budget + timeout + REPLY_MARGIN,
                             **extra, **extra_sync)
        except ServiceUnavailable as exc:
            raise type(exc)(f'{exc}; inspect git_status or retry the same request; '
                            f'request_id={request_id!r}, caller_scope={caller_scope!r}',
                            request_id=request_id, caller_scope=caller_scope,
                            may_have_committed=exc.may_have_committed) from exc

    def git_status(self):
        return self.call('git_status')

    def git_recover(self, *, acknowledge_quiescent=False, accept_outcome=False, release_drift=None, worktree=None):
        extra = {} if worktree is None else {'worktree': str(Path(worktree).resolve())}
        return self.call('git_recover', **extra, acknowledge_quiescent=acknowledge_quiescent, accept_outcome=accept_outcome,
                         release_drift=release_drift)

    def cancel(self, caller_scope, request_id):
        return self.call('cancel', caller_scope=caller_scope, request_id=request_id)

    def shutdown(self):
        return self.call('shutdown')
