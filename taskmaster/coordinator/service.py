"""Repository-local native writer and asynchronous projection owner.

No activation or migration happens here. Only verified native stores can start.
The kernel ownership lock, not a PID or discovery record, elects the service.
"""
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import Future
from contextlib import closing
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import secrets
import socket
import threading
import time

from taskmaster.native import commands, contracts
from taskmaster.admission import UnsupportedStoreError
from .ownership import Ownership, OwnershipUnavailable
from .protocol import (MAX_MESSAGE_BYTES, MAX_RESPONSE_BYTES, ServiceUnavailable, check_handshake,
                               connect, encode, identify, identify_connection)

LOG = logging.getLogger(__name__)


@dataclass
class Work:
    request: dict
    future: Future = field(default_factory=Future)
    cancelled: threading.Event = field(default_factory=threading.Event)
    admitted: bool = False


class AdmissionQueue:
    """Bounded FIFO with an explicit removable pre-admission state."""
    def __init__(self, limit):
        self.limit, self.items = limit, deque()
        self.condition = threading.Condition()

    def put_nowait(self, work):
        with self.condition:
            if len(self.items) >= self.limit:
                raise queue.Full
            self.items.append(work)
            self.condition.notify()

    def get(self, timeout):
        with self.condition:
            if not self.condition.wait_for(lambda: bool(self.items), timeout):
                raise queue.Empty
            return self.items.popleft()

    def remove(self, work):
        with self.condition:
            try:
                self.items.remove(work)
                return True
            except ValueError:
                return False

    def qsize(self):
        with self.condition:
            return len(self.items)

    def empty(self):
        return self.qsize() == 0


class Coordinator:
    def __init__(self, root, *, queue_limit=256, handler_limit=64, checkpoint=None, exporter=None,
                 linear_client_factory=None):
        self.root = Path(root).resolve(strict=True)
        self.identity = identify(self.root)
        self.ownership = Ownership(self.root)
        self.nonce, self.token = secrets.token_hex(24), secrets.token_hex(32)
        if type(queue_limit) is not int or queue_limit < 1:
            raise ValueError('queue limit must be positive')
        if type(handler_limit) is not int or handler_limit < 1:
            raise ValueError('handler limit must be positive')
        self.handler_limit = handler_limit
        self.queue = AdmissionQueue(queue_limit)
        self.pending = {}
        self.guard = threading.RLock()
        # N13 import and Git generation pinning share this publisher boundary.
        self.publication = threading.RLock()
        self.execution = threading.RLock()
        # Explicit admission gate: a lock alone lets a busy writer re-acquire
        # `execution` ahead of a waiting sync indefinitely. While `pauses` is
        # non-zero the writer admits nothing new; the in-flight command finishes.
        self.admission = threading.Condition()
        self.pauses = 0
        self.active_syncs = 0
        self.stopping, self.export_needed = threading.Event(), threading.Event()
        self.checkpoint = checkpoint or (lambda stage: None)
        self.exporter = exporter
        self.last_activity = time.monotonic()
        self.last_export_error = None
        self.server = None
        self.threads = []
        from .linear_worker import LinearWorker
        self.linear = LinearWorker(self, client_factory=linear_client_factory)

    def _connect(self, *, readonly=False):
        connection = connect(self.root, readonly=readonly)
        try:
            connection.execute('BEGIN')
            if identify_connection(self.root, connection) != self.identity:
                raise ServiceUnavailable('native authority changed; old coordinator is retiring')
            connection.rollback()
            return connection
        except (UnsupportedStoreError, ServiceUnavailable):
            self.stop()
            connection.close()
            raise
        except BaseException:
            connection.close()
            raise

    def start(self):
        if self.server is not None or self.stopping.is_set():
            raise RuntimeError('a coordinator instance starts exactly once')
        self.ownership.acquire()
        try:
            with closing(self._connect(readonly=True)):
                pass
            self.server = _Server(('127.0.0.1', 0), _Handler, handler_limit=self.handler_limit)
            self.server.coordinator = self
            for name, target in (('writer', self._writer), ('exporter', self._export), ('linear', self.linear.run),
                                 ('ipc', self.server.serve_forever)):
                thread = threading.Thread(target=target, name=f'taskmaster-{name}', daemon=True)
                thread.start()
                self.threads.append(thread)
            self.ownership.publish(self.discovery())
            self.export_needed.set()  # recover durable jobs from a prior owner
            return self
        except BaseException:
            self.close()
            raise

    def discovery(self):
        return dict(self.identity, nonce=self.nonce, token=self.token, pid=os.getpid(),
                    port=self.server.server_port)

    def submit(self, envelope):
        request, _ = contracts.validate(envelope)
        if request.get('request_id') is None:
            raise ValueError('IPC commands require a durable request_id')
        if request['store_id'] != self.identity['store_id']:
            raise contracts.Conflict('request targets a different store identity')
        work = Work(request)
        key = (request['caller_scope'], request['request_id'])
        with self.guard:
            if self.stopping.is_set():
                raise ServiceUnavailable('coordinator is stopping; retry the same request_id')
            old = self.pending.get(key)
            if old is not None:
                if old.request != request:
                    raise contracts.Conflict('request_id reused with a different queued payload')
                return old.future
            try:
                self.queue.put_nowait(work)
            except queue.Full:
                raise ServiceUnavailable('coordinator admission queue is full; retry the same request_id') from None
            self.pending[key] = work
        return work.future

    def cancel(self, caller_scope, request_id):
        with self.guard:
            work = self.pending.get((caller_scope, request_id))
            if work is None:
                return {'state': 'unknown', 'may_have_committed': True}
            if work.admitted:
                return {'state': 'admitted', 'may_have_committed': True}
            # A queued item may be a retry after its original response was lost.
            # Cancelling this queue entry cannot promise that the logical request
            # never committed. Admission and this read share the guard; an
            # unadmitted new request cannot race past the receipt check.
            with closing(self._connect(readonly=True)) as connection:
                committed = connection.execute(
                    'SELECT 1 FROM command_receipts WHERE store_id=? AND caller_scope=? AND request_id=?',
                    (self.identity['store_id'], caller_scope, request_id)).fetchone()
            if committed is not None:
                return {'state': 'already_committed', 'may_have_committed': True}
            work.cancelled.set()
            if self.queue.remove(work):
                self.pending.pop((caller_scope, request_id), None)
                work.future.set_exception(contracts.CancelledBeforeExecution('cancelled while queued'))
            return {'state': 'cancelled_before_execution', 'may_have_committed': False}

    def _writer(self):
        connection = None
        try:
            while not self.stopping.is_set() or not self.queue.empty():
                try:
                    work = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                # Gate after dequeue: a command taken during a pause waits here,
                # outside `execution`, so the pausing sync never waits for it.
                with self.admission:
                    self.admission.wait_for(lambda: not self.pauses or self.stopping.is_set())
                try:
                    self.checkpoint('dequeued')
                    with self.execution:
                        # Check after both queue wait and any generation pause;
                        # a persistent handle may still name a replaced database.
                        with closing(self._connect(readonly=True)):
                            pass
                        if connection is None:
                            connection = self._connect()
                        def admitted(stage):
                            if stage == 'admitted':
                                with self.guard:
                                    # Cancel and admission have one ordering point.
                                    if work.cancelled.is_set():
                                        raise contracts.CancelledBeforeExecution('cancelled before execution')
                                    work.admitted = True
                            self.checkpoint(stage)
                        receipt = commands.execute(connection, work.request, cancelled=work.cancelled.is_set,
                                                   checkpoint=admitted)
                        work.future.set_result(receipt)
                        self.export_needed.set()
                except BaseException as exc:
                    # A failed job must not strand the writer or its queue. The
                    # durable receipt, not this transport error, resolves retry.
                    if connection is not None:
                        connection.close()
                        connection = None
                    error = exc if isinstance(exc, Exception) else ServiceUnavailable('writer interrupted; retry the same request_id')
                    work.future.set_exception(error)
                finally:
                    with self.guard:
                        self.pending.pop((work.request['caller_scope'], work.request['request_id']), None)
        finally:
            if connection is not None:
                connection.close()

    def pause_writer(self):
        """Stop admitting new commands; pair with `resume_writer`."""
        with self.admission:
            self.pauses += 1

    def resume_writer(self):
        with self.admission:
            self.pauses -= 1
            self.admission.notify_all()

    def _drain(self, connection, through=None):
        if self.exporter is not None:
            return self.exporter(connection, self.root / '.taskmaster', through=through)
        from taskmaster.native_routing.projection import drain
        return drain(connection, self.root / '.taskmaster', session=f'coordinator-{self.nonce}',
                     through=through, progress_wait=False)

    def _export(self):
        while not self.stopping.is_set():
            if not self.export_needed.wait(0.25):
                continue
            self.export_needed.clear()
            try:
                with self.publication, closing(self._connect()) as connection:
                    notices = self._drain(connection)
                self.last_export_error = None
                if notices and not self.stopping.wait(1):
                    self.export_needed.set()
            except Exception as exc:
                self.last_export_error = str(exc)
                LOG.exception('background export failed; durable jobs retained')
                # Retry even when there are no more client writes.
                if not self.stopping.wait(1):
                    self.export_needed.set()

    def flush(self, through, *, timeout=10):
        from taskmaster.native_routing import projection, progress
        from taskmaster.native import projection as outbox
        if type(through) is not int or through < 0:
            raise ValueError('flush_through must be a non-negative sequence')
        if self.stopping.is_set():
            return {'state': 'pending', 'through': through, 'notices': ['export pending: coordinator stopping']}
        deadline = time.monotonic() + timeout
        if not self.publication.acquire(timeout=max(0, timeout)):
            return {'state': 'pending', 'through': through, 'notices': ['export pending: publisher busy']}
        try:
            if self.stopping.is_set():
                return {'state': 'pending', 'through': through, 'notices': ['export pending: coordinator stopping']}
            with closing(self._connect()) as connection:
                high = connection.execute('SELECT COALESCE(MAX(seq),0) FROM domain_events').fetchone()[0]
                if through > high:
                    raise ValueError('flush target exceeds committed sequence')
                while True:
                    notices = self._drain(connection, through)
                    # A prior background pass may already have changed a job
                    # from pending to conflict. It is then absent from this
                    # drain's claims and warnings, but remains unpublished.
                    held = set(outbox.flagged_files(connection))
                    held.update(row[0] for row in connection.execute(
                        'SELECT file FROM projection WHERE quarantined=1'))
                    notices = list(dict.fromkeys([*notices, *(
                        f'export pending: {rel} is {outbox.held_file(connection, rel)}'
                        for rel in sorted(held))]))
                    behind = projection._behind(connection, through)
                    owed = progress.owes_through(connection, through)
                    if owed and progress.NOTICE not in notices:
                        notices.append(progress.NOTICE)
                    if not behind and not owed:
                        return {'state': 'pending' if notices else 'exported', 'through': through, 'notices': notices}
                    if time.monotonic() >= deadline:
                        return {'state': 'pending', 'through': through,
                                'notices': notices or ['export pending: durable jobs remain']}
                    time.sleep(0.05)
        finally:
            self.publication.release()

    def sync(self, **arguments):
        from .sync_worker import synchronize
        return synchronize(self, **arguments)

    def dispatch(self, message):
        if not isinstance(message, dict):
            raise ValueError('IPC message must be an object')
        check_handshake(message.get('identity'), self.identity, self.nonce)
        # Recheck the authority fence and identity, including after store swaps.
        if identify(self.root) != self.identity:
            self.stop()
            raise ServiceUnavailable('native authority changed; restart the coordinator')
        self.last_activity = time.monotonic()
        method = message.get('method')
        if method == 'status':
            return dict(self.identity, nonce=self.nonce, queued=self.queue.qsize(),
                        linear_queued=len(self.linear.jobs),
                        export_error=self.last_export_error, active_syncs=self.active_syncs)
        if method == 'sync':
            return self.sync(caller_scope=message.get('caller_scope'), request_id=message.get('request_id'),
                             import_files=message.get('import_files', True), through=message.get('through', 0),
                             files=message.get('files'), take_file=message.get('take_file', False))
        if method == 'sync_status':
            from .sync_worker import operation_scope
            from taskmaster.native.sync import operation_state
            scope = operation_scope(message.get('caller_scope'), message.get('request_id'))
            with closing(self._connect(readonly=True)) as connection:
                return operation_state(connection, scope) or {'state': 'unknown'}
        if method == 'linear_bootstrap':
            from .linear_config import bootstrap
            return bootstrap(self, message.get('entry'), message.get('default_workspace'))
        if method == 'linear_retry':
            return self.linear.response(message.get('caller_scope'), message.get('request_id'), message.get('target_id', ''))
        if method == 'receipt':
            caller, key = message.get('caller_scope'), message.get('request_id')
            if not all(isinstance(value, str) and 1 <= len(value) <= 256 for value in (caller, key)):
                raise ValueError('receipt lookup requires caller_scope and request_id')
            with closing(self._connect(readonly=True)) as connection:
                row = connection.execute('SELECT outcome_json,expires_at FROM command_receipts '
                                         'WHERE store_id=? AND caller_scope=? AND request_id=?',
                                         (self.identity['store_id'], caller, key)).fetchone()
            return {'state': 'unknown'} if row is None else {'state': 'expired' if row[1] else 'committed',
                                                           'receipt': json.loads(row[0])}
        if method == 'execute':
            visibility = message.get('visibility', 'native')
            if visibility not in ('native', 'legacy'):
                raise ValueError('visibility must be native or legacy')
            receipt = self.submit(message.get('envelope')).result()
            projection = (self.flush(receipt['commit_seq']) if visibility == 'legacy' else
                          {'state': receipt['projection_state'], 'through': receipt['commit_seq'], 'notices': []})
            return {'receipt': receipt, 'projection': projection}
        if method == 'flush':
            return self.flush(message.get('through'))
        if method == 'cancel':
            return self.cancel(message.get('caller_scope'), message.get('request_id'))
        if method == 'shutdown':
            self.stop()
            return {'state': 'stopping'}
        raise ValueError('unsupported coordinator method')

    def idle_expired(self, seconds):
        with self.guard, self.linear.guard:
            return (not self.pending and not self.linear.jobs and not self.active_syncs
                    and time.monotonic() - self.last_activity >= seconds)

    def stop(self):
        # Admission's check and enqueue must finish before the writer can see
        # shutdown with an empty queue. Otherwise an accepted Future can be
        # stranded after the writer has already exited.
        with self.guard:
            self.stopping.set()
        with self.admission:
            self.admission.notify_all()

    def close(self):
        self.stop()
        if self.server is not None:
            # Keep ownership until all domain/export work has stopped.
            if any(t.name == 'taskmaster-ipc' and t.is_alive() for t in self.threads):
                self.server.shutdown()
            self.server.server_close()
        for thread in self.threads:
            thread.join()
        # An already-running compatibility barrier is an HTTP handler, not the
        # exporter thread. It must finish before a new owner can publish files.
        with self.publication:
            self.ownership.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()


_OVERLOAD_BUDGET = 0.5
_HEADER_LIMIT = 64 * 1024


def _drain(sock, deadline, *, whole_request):
    """Discard inbound bytes until one whole request (or else EOF) arrives, bounded in bytes and time."""
    head, expected, total = b'', None, 0
    while total < MAX_MESSAGE_BYTES + _HEADER_LIMIT:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(65536)
        except TimeoutError:
            return
        if not chunk:
            return
        total += len(chunk)
        if not whole_request:
            continue
        if expected is None:
            head += chunk
            lines, separator, _ = head.partition(b'\r\n\r\n')
            if not separator:
                if len(head) > _HEADER_LIMIT:
                    return
                continue
            length = 0
            for line in lines.split(b'\r\n')[1:]:
                name, _, value = line.partition(b':')
                if name.strip().lower() == b'content-length':
                    try:
                        length = int(value.strip())
                    except ValueError:
                        return
            expected = len(lines) + len(separator) + min(max(length, 0), MAX_MESSAGE_BYTES)
            head = b''
        if total >= expected:
            return


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, *args, handler_limit, **kwargs):
        self.handlers = threading.BoundedSemaphore(handler_limit)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.handlers.acquire(blocking=False):
            # Refuse before allocating another thread. A structured overload
            # response also prevents clients mistaking capacity for a dead owner
            # and repeatedly launching startup contenders.
            raw = encode({'type': 'ServiceUnavailable', 'error': 'coordinator IPC capacity reached; retry later'})
            try:
                # Closing with unread request bytes sends RST (always on
                # Windows), which can destroy the reply before the client reads
                # it. Drain the bounded request, reply, half-close, then wait
                # briefly for the client's close. This runs on the accept
                # thread, so the whole exchange shares one small deadline.
                deadline = time.monotonic() + _OVERLOAD_BUDGET
                _drain(request, deadline, whole_request=True)
                request.settimeout(max(0.01, deadline - time.monotonic()))
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\n'
                                + f'Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n'.encode('ascii') + raw)
                request.shutdown(socket.SHUT_WR)
                _drain(request, deadline, whole_request=False)
            except OSError:
                pass
            finally:
                self.close_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.handlers.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.handlers.release()


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *args):
        pass  # Never log credentials or command payloads.

    def do_POST(self):
        owner = self.server.coordinator
        try:
            if self.path != '/rpc' or not secrets.compare_digest(self.headers.get('Authorization', ''), f'Bearer {owner.token}'):
                self.answer(403, {'error': 'authentication required', 'type': 'ServiceUnavailable'})
                return
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('chunked requests are not accepted')
            size = int(self.headers.get('Content-Length', '-1'))
            if not 0 < size <= MAX_MESSAGE_BYTES:
                raise ValueError('invalid coordinator message length')
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError('incomplete coordinator message')
            value = owner.dispatch(json.loads(raw))
            self.answer(200, {'result': value})
        except (BrokenPipeError, ConnectionResetError):
            pass  # Disconnect does not undo an admitted command.
        except Exception as exc:
            self.answer(409, {'error': str(exc), 'type': type(exc).__name__})

    def answer(self, status, value):
        try:
            raw = encode(value, limit=MAX_RESPONSE_BYTES)
        except ValueError:
            status = 503
            raw = encode({'type': 'ServiceUnavailable', 'error':
                          'response too large; command may have committed; retain request_id and inspect its durable receipt'})
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--idle-seconds', type=float, default=300)
    args = parser.parse_args()
    coordinator = Coordinator(args.root)
    log = RotatingFileHandler(coordinator.ownership.directory / 'service.log', maxBytes=1024 * 1024,
                             backupCount=2, encoding='utf-8')
    LOG.addHandler(log)
    try:
        with coordinator:
            while not coordinator.stopping.wait(0.25):
                if coordinator.idle_expired(args.idle_seconds):
                    break
    except OwnershipUnavailable:
        return  # A startup peer won the kernel lock; never replace its discovery.
    finally:
        log.close()


if __name__ == '__main__':
    main()
