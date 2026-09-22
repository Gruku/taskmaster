"""Finite explicit import/export coordination; managed Git pins build on this.

Lock order is publication -> writer execution -> short SQLite transactions.
Imports are submitted before taking the execution pause. Normal domain commands
never take publication, so an import cannot deadlock the writer it is awaiting.
"""
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import closing
import hashlib
import time

from taskmaster.native import contracts, projection, sync
from taskmaster.native.migrate import encode
from taskmaster.native.queries import Repository
from taskmaster.projection_parse import classify
from . import sync_files
from .sync_prepare import prepare


def operation_scope(caller_scope, request_id):
    if not all(isinstance(value, str) and 1 <= len(value) <= 256 for value in (caller_scope, request_id)):
        raise ValueError('sync requires caller_scope and request_id')
    return 'sync-' + hashlib.sha256(encode([caller_scope, request_id]).encode()).hexdigest()


def synchronize(owner, *, caller_scope, request_id, import_files=True, through=0,
                files=None, take_file=False, timeout=20):
    options = dict(import_files=import_files, through=through, files=files, take_file=take_file)
    sync.validate_input(options)
    scope = operation_scope(caller_scope, request_id)
    result = dict(state='pending', through=through, captured=False, imports=[], observed=0,
                  unresolved=[], notices=[], warnings=[], caller_scope=caller_scope,
                  request_id=request_id, receipt_scope=scope, import_files=import_files)
    deadline = time.monotonic() + max(0, timeout)
    acquired = False
    with owner.guard:
        owner.active_syncs += 1

    def remaining():
        return max(0, deadline - time.monotonic())

    def pending(rel, reason):
        if rel and rel not in result['unresolved']:
            result['unresolved'].append(rel)
        notice = f'sync pending: {rel}: {reason}' if rel else f'sync pending: {reason}'
        if notice not in result['notices']:
            result['notices'].append(notice)

    def execute(operation, key, arguments):
        envelope = dict(protocol=2, store_id=owner.identity['store_id'], caller_scope=scope,
                        request_id=key, operation=operation, arguments=arguments, expected_revisions=[])
        # Validate before enqueue, including the fully encoded 1 MiB limit.
        contracts.validate(envelope)
        return owner.submit(envelope).result(timeout=remaining())

    def current(plan):
        if plan.observation is not None:
            return sync_files.unchanged(owner.root / '.taskmaster', plan.observation)
        return sync_files.observe(owner.root / '.taskmaster', plan.file) is None

    try:
        if owner.stopping.is_set():
            pending(None, 'coordinator stopping')
            return result
        acquired = owner.publication.acquire(timeout=remaining())
        if not acquired:
            pending(None, 'publisher busy')
            return result
        try:
            execute('sync.begin', 'begin', {'input': options})
        except FutureTimeout:
            pending(None, 'begin outcome uncertain; inspect receipt_scope/begin or retry the same sync id')
            return result
        with closing(owner._connect(readonly=True)) as connection:
            state = sync.operation_state(connection, scope)
        if state['state'] == 'complete':
            return state['result']
        selected = []
        if import_files:
            if files is not None:
                selected = list(files)
            else:
                inventory = sync_files.discover(owner.root / '.taskmaster')
                selected = list(inventory.files)
                result['warnings'].extend(f'duplicate import path skipped: {rel}; canonical {canonical}'
                                          for rel, canonical in inventory.duplicates.items())
                for rel, reason in inventory.refused.items():
                    pending(rel, reason)
                with closing(owner._connect(readonly=True)) as connection:
                    # Include missing known authored paths so absence requests
                    # repair. Derived indexes/local files are not import inputs.
                    for (rel,) in connection.execute('SELECT file FROM projection ORDER BY file'):
                        try:
                            classify(rel)
                        except ValueError:
                            continue
                        if rel not in inventory.duplicates and rel not in selected:
                            selected.append(rel)
            if len(selected) > sync.MAX_FILES:
                pending(None, f'more than {sync.MAX_FILES} projection files; bounded scan refused')
                return result
            for rel in selected:
                if not remaining() or owner.stopping.is_set():
                    pending(rel, 'time budget exhausted or coordinator stopping; retry the same sync id')
                    return result
                try:
                    with closing(owner._connect(readonly=True)) as connection, Repository(connection).snapshot() as snapshot:
                        plan = prepare(snapshot, owner.root / '.taskmaster', rel, take_file=take_file)
                    if plan.arguments is None:
                        if plan.state != 'unchanged':
                            pending(rel, plan.reason)
                        continue
                    owner.checkpoint('sync_prepared')
                    if not current(plan):
                        pending(rel, 'file changed after parse; not imported')
                        continue
                    key = hashlib.sha256(encode(plan.arguments).encode()).hexdigest()
                    try:
                        receipt = execute('sync.apply', key, plan.arguments)
                    except FutureTimeout:
                        result['imports'].append(dict(file=rel, state='uncertain', caller_scope=scope, request_id=key,
                                                      may_have_committed=True))
                        pending(rel, 'import outcome uncertain; inspect its receipt or retry the same sync id')
                        return result
                    if plan.state == 'observe':
                        result['observed'] += 1
                    else:
                        result['imports'].append(dict(receipt['result'], commit_seq=receipt['commit_seq'],
                                                      caller_scope=scope, request_id=key))
                    owner.checkpoint('sync_import_committed')
                    if not current(plan):
                        pending(rel, 'file changed after import commit; newer bytes retained')
                    if plan.state in {'conflict', 'quarantine'}:
                        pending(rel, plan.reason)
                except (ValueError, KeyError, OSError) as exc:
                    pending(rel, str(exc))

        # No import is awaiting the sole writer when this pause is acquired.
        # Writers queued after this point remain durable intent for the next
        # generation rather than extending this finite barrier indefinitely.
        # The admission gate stops new dequeues first, so waiting here covers
        # at most the one command already in flight.
        owner.pause_writer()
        paused = False
        try:
            paused =owner.execution.acquire(timeout=remaining())
        finally:
            if not paused:
                owner.resume_writer()
        if not paused:
            pending(None, 'writer busy')
            return result
        try:
            if owner.stopping.is_set():
                pending(None, 'coordinator stopping')
                return result
            with closing(owner._connect(readonly=True)) as connection:
                captured = connection.execute('SELECT COALESCE(MAX(seq),0) FROM domain_events').fetchone()[0]
            if through > captured:
                raise ValueError('flush target exceeds committed sequence')
            result.update(through=max(through, captured), captured=True)
            owner.checkpoint('sync_pinned')
            try:
                publication = owner.flush(result['through'], timeout=remaining())
            except (ValueError, OSError) as exc:
                pending(None, str(exc))
                return result
            owner.checkpoint('sync_published')
            result['notices'].extend(notice for notice in publication['notices'] if notice not in result['notices'])
            if publication['state'] != 'exported':
                pending(None, 'projection publication incomplete')
            with closing(owner._connect(readonly=True)) as connection:
                held = set(projection.flagged_files(connection))
                held.update(row[0] for row in connection.execute('SELECT file FROM projection WHERE quarantined=1'))
                for rel in sorted(held):
                    pending(rel, projection.held_file(connection, rel) or 'held projection')
                for rel in selected:
                    try:
                        actual = sync_files.observe(owner.root / '.taskmaster', rel)
                        expected = connection.execute('SELECT content_hash FROM projection WHERE file=?', (rel,)).fetchone()
                        if expected is None and actual is None:
                            continue  # a recorded move/tombstone requires absence
                        variants = set() if actual is None else {
                            projection._digest(actual.content), projection._digest(projection._lf(actual.content)),
                            projection._digest(projection._crlf(actual.content))}
                        if expected is None or expected[0] not in variants:
                            pending(rel, 'file differs from the published generation at completion')
                    except (OSError, ValueError) as exc:
                        pending(rel, str(exc))
        finally:
            owner.execution.release()
            owner.resume_writer()
        if not result['notices'] and not result['unresolved']:
            result['state'] = 'synchronized'
            try:
                execute('sync.finish', 'finish', {'result': result})
            except FutureTimeout:
                result['state'] = 'pending'
                pending(None, 'completion receipt uncertain; retry the same sync id')
        return result
    finally:
        if acquired:
            owner.publication.release()
        with owner.guard:
            owner.active_syncs -= 1
            owner.last_activity = time.monotonic()
