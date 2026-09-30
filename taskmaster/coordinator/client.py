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
from taskmaster.native import contracts, metrics
from .ownership import ownership_held, verify_private
from .protocol import (CoordinatorStopping, HandshakeError, MAX_RESPONSE_BYTES, REPLY_MARGIN, SYNC_TIMEOUT,
                       ServiceUnavailable, build_identity, compare_versions, describe, encode, identify, same_build,
                       valid_build, validate_sync_timeout)

_START_LOCK = threading.Lock()
_RETRY_PAUSE = 0.1


class _ForeignBuild(Exception):
    """A live coordinator of another build holds this repository."""
    def __init__(self, record):
        super().__init__('foreign coordinator build')
        self.record = record


class _Unresponsive(Exception):
    """A live owner of this build did not answer (overloaded, or retiring)."""
    def __init__(self, record):
        super().__init__('coordinator not responding')
        self.record = record


def _annotated(exc, guidance, request_id, caller_scope):
    """Name the request on a failure; offer receipt recovery only when it may have committed."""
    return type(exc)(f"{exc}; {guidance if exc.may_have_committed else 'no command ran'}; "
                     f'request_id={request_id!r}, caller_scope={caller_scope!r}',
                     request_id=request_id, caller_scope=caller_scope, may_have_committed=exc.may_have_committed)


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
        self.build = build_identity()
        if visibility not in ('native', 'legacy'):
            raise ValueError('visibility must be native or legacy')
        self.autostart, self.visibility, self.timeout = autostart, visibility, timeout
        # An absolute time.monotonic() deadline for the whole connect path, or None (sync_job).
        self._deadline = None

    def _bounded(self):
        """Before each blocking startup step under a call deadline: give it only the time left."""
        if self._deadline is None:
            return
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise ServiceUnavailable('call deadline reached while reaching the coordinator', may_have_committed=False)
        self.timeout = left

    def _clamped(self, deadline):
        """`deadline`, or the call deadline (sync_job) when that comes first."""
        return deadline if self._deadline is None else min(deadline, self._deadline)

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
        payload = encode(dict(identity=dict(self.identity, nonce=record['nonce'], build=self.build), method=method,
                              **arguments))
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
                          'UnsupportedStoreError': UnsupportedStoreError, 'CoordinatorStopping': CoordinatorStopping}
                error = errors.get(value.get('type'), ServiceUnavailable)(value.get('error', 'coordinator refused request'))
                if isinstance(error, (HandshakeError, CoordinatorStopping)):
                    error.may_have_committed = False  # refused before admission
                raise error
            return value['result']
        finally:
            connection.close()

    def _probe(self):
        """Return a ready discovery record, or None only on evidence that no owner is listening."""
        try:
            record = self._discovery()
            if not same_build(record.get('build'), self.build):
                if ownership_held(self.root):
                    raise _ForeignBuild(record)
                # The record of an owner that has exited: no owner is listening.
                raise FileNotFoundError('stale discovery of another build')
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
            # free: then the record is stale and its port may be reused. A retiring
            # owner looks the same until it releases the lock: keep re-probing.
            if ownership_held(self.root):
                raise _Unresponsive(record) from exc
            return None

    def _ready(self):
        if not metrics.ENABLED:
            return self._connect_ready()
        # Client-side IPC startup: entry -> a ready service answered `status`.
        started, launched = time.perf_counter(), []
        try:
            return self._connect_ready(launched)
        finally:
            metrics.emit('ipc_connect', ms=(time.perf_counter() - started) * 1000, launched=bool(launched))

    def _connect_ready(self, launched=None):
        # One budget covers waiting out a busy owner of another build and replacing it; under
        # a call deadline (sync_job) that deadline bounds every step, including the start lock.
        deadline = self._clamped(time.monotonic() + self.timeout)
        while True:
            try:
                self._bounded()
                record = self._probe()
                if record is not None:
                    return record
                if self._deadline is None:
                    _START_LOCK.acquire()
                elif not _START_LOCK.acquire(timeout=max(0.0, self._deadline - time.monotonic())):
                    raise ServiceUnavailable('another call in this process is starting the coordinator; call again',
                                             may_have_committed=False)
                try:
                    # Another client in this process may already have completed startup.
                    self._bounded()
                    record = self._probe() or self._start(launched)
                finally:
                    _START_LOCK.release()
                if record is not None:
                    return record
            except _ForeignBuild as foreign:
                self._retire(foreign.record, deadline)
            except _Unresponsive:
                if time.monotonic() >= deadline:
                    raise ServiceUnavailable('repository coordinator is running but not responding; retry later',
                                             may_have_committed=False) from None
                time.sleep(_RETRY_PAUSE)
                continue  # the next probe decides; this is not a failed startup
            if time.monotonic() >= deadline:
                raise ServiceUnavailable('coordinator startup unavailable; inspect .taskmaster/local/coordinator/'
                                         'service.log; no writer fallback', may_have_committed=False)

    def _await_successor(self, record, deadline):
        """Wait while the owner named by `record` still holds the lock and no successor
        has published: it is finishing in-flight work and releasing its leases."""
        deadline = self._clamped(deadline)
        while ownership_held(self.root) and time.monotonic() < deadline:
            try:
                if self._discovery().get('nonce') != record['nonce']:
                    return
            except FileNotFoundError:
                return
            time.sleep(0.05)

    def _start(self, launched):
        """Launch an owner of this build; None when a live owner of another build won instead."""
        child = _launch(self.root)
        if launched is not None:
            launched.append(child)
        deadline = self._clamped(time.monotonic() + min(self.timeout, 15))
        while time.monotonic() < deadline:
            try:
                self._bounded()
                record = self._discovery()
                if not same_build(record.get('build'), self.build):
                    # The retired owner's record stays until ours publishes. If our child
                    # already exited, a peer of another build won the kernel lock.
                    if child.poll() is not None:
                        return None
                    raise FileNotFoundError('discovery of this build not yet published')
                self._send(record, 'status')
                child.poll()  # Reap a startup loser, never signal an arbitrary PID.
                return record
            except (FileNotFoundError, ConnectionError, TimeoutError, http.client.HTTPException):
                code = child.poll()
                if code not in (None, 0):
                    break
                if code == 0 and not ownership_held(self.root):
                    return None  # it lost to an owner that has since exited (e.g. retiring): relaunch
                time.sleep(0.05)
        raise ServiceUnavailable('coordinator startup unavailable; inspect .taskmaster/local/coordinator/service.log; no writer fallback')

    def _retire(self, record, deadline):
        """The cross-build policy: never run a command on another build. Equal digests are one
        build (see `_probe`). Otherwise, by SemVer precedence against the owner's release:
          newer    - ask it to retire; while it is busy, wait and retry within the budget;
          same or unordered (same release, other code; or an unparseable version) - ask it
                     to retire only if idle, refusing at once when busy (limits churn between
                     two installs used side by side);
          older    - refuse; a newer owner is never downgraded.
        The owner decides again under its own locks. Every refusal here is pre-admission."""
        theirs = record.get('build')
        if not valid_build(theirs):
            raise HandshakeError(
                "the running coordinator predates the build handshake (a pre-release build) and cannot be retired; "
                'it exits after its idle timeout (TASKMASTER_SERVICE_IDLE_SECONDS, 300 s by default); to stop it '
                'sooner, end the session that started it or stop the process whose pid is in '
                '.taskmaster/local/coordinator/discovery.json', may_have_committed=False)
        order = compare_versions(self.build, theirs)
        if order == -1:
            raise HandshakeError(f'a newer taskmaster build ({describe(theirs)}) runs this repository\'s coordinator; '
                                 f'this client ({describe(self.build)}) will not downgrade it; restart this session '
                                 'to load the updated plugin', may_have_committed=False)
        if not self.autostart:
            raise HandshakeError(f'coordinator build {describe(theirs)} differs from this client build '
                                 f'{describe(self.build)}', may_have_committed=False)
        try:
            self._bounded()
            state = self._send(record, 'retire').get('state')
        except HandshakeError:
            raise
        except (ConnectionError, TimeoutError, http.client.HTTPException, ServiceUnavailable):
            # Re-probe: it may just have exited, its port may now be someone else's
            # (403), or a peer is starting.
            state = 'unreachable'
        if state == 'refused':
            raise HandshakeError(f'coordinator build {describe(theirs)} refused to retire', may_have_committed=False)
        if state == 'busy' and order != 1:
            raise ServiceUnavailable(f'coordinator build {describe(theirs)} is busy; this client '
                                     f'({describe(self.build)}, same release, other code) retires it only while idle; '
                                     'retry later', may_have_committed=False)
        deadline = self._clamped(deadline)
        if state == 'retiring':
            self._await_successor(record, deadline)  # a new record means re-probe
        elif time.monotonic() < deadline:
            time.sleep(min(_RETRY_PAUSE, max(0.0, deadline - time.monotonic())))
        if time.monotonic() >= deadline:
            detail = {'busy': 'is busy', 'retiring': 'is still retiring'}.get(state, 'is not responding')
            raise ServiceUnavailable(f'coordinator build {describe(theirs)} {detail}; this client '
                                     f'({describe(self.build)}) does not run commands on another build; '
                                     'retry later', may_have_committed=False)

    def call(self, method, *, wait=None, **arguments):
        # Retain identical arguments across transport retries. In particular,
        # never mint a new command request_id after an ambiguous disconnect.
        deadline = self._clamped(time.monotonic() + self.timeout)
        sent = False  # an earlier attempt reached a coordinator; its outcome is unknown
        while True:
            try:
                record = self._ready()
                return self._send(record, method, wait=wait, **arguments)
            except CoordinatorStopping as exc:
                # A retiring owner refused it before admission: wait for its successor.
                if time.monotonic() >= deadline:
                    raise self._after_lost_reply(exc) if sent else exc
                self._await_successor(record, deadline)
            except (ConnectionError, TimeoutError, http.client.HTTPException):
                if sent:
                    raise ServiceUnavailable('coordinator disconnected; retry the same request_id to recover its receipt') from None
                sent = True
            except ServiceUnavailable as exc:
                if sent:
                    raise self._after_lost_reply(exc) from exc
                raise

    @staticmethod
    def _after_lost_reply(exc):
        """A retry's refusal says nothing about the first attempt, whose reply was lost."""
        return ServiceUnavailable(f'the first attempt reached a coordinator but its reply was lost, so the command '
                                  f'may have committed; the retry failed: {exc}', may_have_committed=True)

    def execute(self, envelope):
        request, _ = contracts.validate(envelope)
        if request.get('request_id') is None:
            raise ValueError('IPC commands require a durable request_id')
        try:
            return self.call('execute', envelope=request, visibility=self.visibility)
        except ServiceUnavailable as exc:
            # Public adapters may have minted the ID on behalf of the caller;
            # expose it on ambiguity so the durable receipt is inspectable.
            raise _annotated(exc, 'retry the same request to recover its receipt',
                             request['request_id'], request['caller_scope']) from exc

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
            raise _annotated(exc, 'retry the same request', request_id, caller_scope) from exc

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
        options['timeout'] = budget  # always named: the reply is awaited budget + REPLY_MARGIN
        if not all(isinstance(value, str) and 1 <= len(value) <= 256 for value in (caller_scope, request_id)):
            raise ValueError('sync requires caller_scope and request_id')
        try:
            extra = {} if worktree is None else {'worktree': str(Path(worktree).resolve())}
            return self.call('sync', caller_scope=caller_scope, request_id=request_id, wait=budget + REPLY_MARGIN,
                             **options, **extra)
        except ServiceUnavailable as exc:
            raise _annotated(exc, 'inspect sync_status or retry the same sync id', request_id, caller_scope) from exc

    def sync_job(self, *, deadline, sync_id=None, files=None, margin=1.5):
        """Start or attach to a coordinator-driven `backlog_sync` job, or look one up, within a
        hard wall-clock `deadline` (time.monotonic()) covering startup, status, send and reply.

        One attempt, no transport retry: a job is found again by id (or by attaching), never
        lost. The deadline is threaded through every startup step, and the attempt runs on a
        worker thread the caller stops waiting for at the deadline, so no stalled step (a hung
        probe, a start lock held elsewhere) can hold the caller past it."""
        outcome = {}

        def attempt():
            try:
                outcome['value'] = self._sync_job(deadline, sync_id, files, margin)
            except BaseException as exc:  # noqa: BLE001 -- handed to the waiting caller
                outcome['error'] = exc

        worker = threading.Thread(target=attempt, name='taskmaster-sync-job-call', daemon=True)
        worker.start()
        worker.join(max(0.0, deadline - time.monotonic()))
        if worker.is_alive():
            raise ServiceUnavailable('coordinator did not answer within the call deadline', may_have_committed=False)
        if 'error' in outcome:
            raise outcome['error']
        return outcome['value']

    def _sync_job(self, deadline, sync_id, files, margin):
        self._deadline = deadline
        while True:
            record = None
            try:
                record = self._ready()
                left = deadline - time.monotonic()
                wait = min(margin, left / 3)  # time left to send the reply, whatever the budget
                if left <= 0.05:
                    raise ServiceUnavailable('coordinator reached too late in this call to wait for the sync',
                                             may_have_committed=False)
                self.timeout = left
                return self._send(record, 'sync_job', sync_id=sync_id, files=files,
                                  wait_seconds=round(max(0.0, left - wait), 3))
            except CoordinatorStopping:
                # A retiring owner refused it before admission: its successor takes it, in time.
                if record is not None:
                    self._await_successor(record, deadline)
                else:
                    time.sleep(min(_RETRY_PAUSE, max(0.0, deadline - time.monotonic())))
                if time.monotonic() >= deadline:
                    raise
            except (ConnectionError, TimeoutError, http.client.HTTPException) as exc:
                raise ServiceUnavailable(f'coordinator did not answer within the call deadline '
                                         f'({type(exc).__name__})') from None

    def sync_status(self, caller_scope, request_id):
        return self.call('sync_status', caller_scope=caller_scope, request_id=request_id)

    def git_run(self, *, kind, message=None, ref=None, caller_scope='explicit-git', request_id=None, timeout=600,
                worktree=None, sync_timeout=None):
        """Managed commit/checkout under the coordinator's publication hold.

        A retry with the same request_id never repeats Git: it answers
        `in_progress` or the settled result. `sync_timeout` bounds the pre-sync."""
        request_id = uuid.uuid4().hex if request_id is None else request_id
        budget = validate_sync_timeout(SYNC_TIMEOUT if sync_timeout is None else sync_timeout)
        extra_sync = {'sync_timeout': budget}  # always named, like sync's budget
        if not all(isinstance(value, str) and 1 <= len(value) <= 256 for value in (caller_scope, request_id)):
            raise ValueError('managed Git requires caller_scope and request_id')
        try:
            extra = {} if worktree is None else {'worktree': str(Path(worktree).resolve())}
            return self.call('git_run', kind=kind, message=message, ref=ref, caller_scope=caller_scope,
                             request_id=request_id, timeout=timeout, wait=budget + timeout + REPLY_MARGIN,
                             **extra, **extra_sync)
        except ServiceUnavailable as exc:
            raise _annotated(exc, 'inspect git_status or retry the same request', request_id, caller_scope) from exc

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
