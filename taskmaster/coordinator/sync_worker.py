"""Finite explicit import/export coordination; managed Git pins build on this.

Lock order is publication -> writer execution -> short SQLite transactions.
Imports are submitted before taking the execution pause. Normal domain commands
never take publication, so an import cannot deadlock the writer it is awaiting.
"""
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import closing
import hashlib
import json
import sqlite3
import time

from taskmaster.native import contracts, metrics, projection, sync
from taskmaster.native.contracts import CancelledBeforeExecution
from taskmaster.native.migrate import encode
from taskmaster.native.queries import Repository
from taskmaster.projection_parse import classify
from . import sync_files
from .protocol import SYNC_TIMEOUT, validate_sync_timeout as validate_timeout
from .sync_prepare import prepare


# sync.finish runs after the caller's budget may be spent on publication; it
# gets its own short minimum so a synchronized run is not reported pending.
FINISH_TIMEOUT = 5
# A file is started only while more than this is left of the budget (at most RESERVE_SHARE
# of it, so a short budget still starts files): room for one prepare and writer command.
FILE_RESERVE = 1.0
RESERVE_SHARE = 0.1
# A sync.apply still running when the budget ends gets up to this long before its outcome is
# settled from the admission queue and its durable receipt (`_settle`). The grace and the
# sync.finish minimum share one FINISH_TIMEOUT allowance: a reply comes within budget + 5 s.
IMPORT_GRACE = FINISH_TIMEOUT
# sync.finish always gets at least this long, however much of the allowance the grace used.
FINISH_FLOOR = 0.5


def _finish_timeout(remaining, grace_used):
    """The sync.finish wait: the rest of the budget, else what the grace left of
    FINISH_TIMEOUT, never below FINISH_FLOOR (a zero wait would report a finished
    completion receipt as uncertain)."""
    return max(remaining, FINISH_TIMEOUT - grace_used, FINISH_FLOOR)
# Why a file's write is unsettled, by (observe?, outcome). An observe records the published
# bytes as the merge base and changes no task data, so it is never called an import.
_UNSETTLED = {
    (False, 'uncertain'): 'import outcome uncertain: the writer is still running it, or was interrupted, and no '
                          'receipt exists yet; inspect its receipt or retry the same sync id',
    (False, 'not_committed'): 'import not committed: it was cancelled before the writer ran it; retry the same '
                              'sync id',
    (True, 'uncertain'): 'base record (observe) outcome uncertain: the writer is still running it, or was '
                         'interrupted; it changes no task data either way; retry the same sync id',
    (True, 'not_committed'): 'base record (observe) not committed: it was cancelled before the writer ran it; it '
                             'changes no task data; retry the same sync id',
}
# The completed result is stored durably and must fit one request envelope.
SUMMARY_BYTES = 256 * 1024
_WARNINGS_KEPT = 50


def _hit(scan, rel, *, fresh=False):
    """Recorded digests for an unchanged fingerprint; None on a miss or any doubt
    (including a refused path: the full read then reports the refusal)."""
    try:
        return scan.digests(rel, fresh=fresh)
    except (OSError, ValueError):
        return None


# Parameters per `IN (...)` query (SQLite's default limit is 999 on older builds).
_IN_CHUNK = 500


def _rows_for(connection, sql, rels):
    """`sql` (with one `{}` placeholder list) run over `rels` in bounded chunks."""
    rels = list(rels)
    for start in range(0, len(rels), _IN_CHUNK):
        chunk = rels[start:start + _IN_CHUNK]
        yield from connection.execute(sql.format(','.join('?' * len(chunk))), chunk)


def batch_size():
    """Paths per full-sync batch: the unit of per-batch reading and memory (N16)."""
    return sync.MAX_FILES


def _unchanged_rule(owner, linked, rels=None):
    """A predicate (rel, digests) -> True when `prepare` would certainly return a plan
    with nothing to submit, decided from one bulk read instead of a snapshot, a parse
    and a file read per file. `digests` come from an unchanged fingerprint.

    `rels` bounds what is loaded to one batch's rows (every row when None); the rule
    answers False (the full path) for any path outside it.

    It is prepare's own first tests, in prepare's order:
    main - `unchanged`: a projection row whose content_hash is the file's digest, a
    retained base whose sha1 is that hash (trusted), and no quarantine, flag or drift
    hold (an untrusted base means `observe`, which needs the bytes: full path);
    linked - with no base of that checkout (blob present), `establish`: the main
    generation's content_hash is one of the file's LF/CRLF variants; with a base,
    `unchanged`: the base digest is the file's digest and the checkout holds nothing.
    Anything else (differing bytes, holds, missing files) goes through prepare."""
    from taskmaster.native import checkouts as checkout_store
    with closing(owner._connect(readonly=True)) as connection:
        connection.execute('BEGIN')
        try:
            wanted = None if rels is None else set(rels)
            if linked is None:
                sql = ('SELECT p.file,p.content_hash,p.quarantined,b.content FROM projection p '
                       'LEFT JOIN projection_base b ON b.file=p.file')
                # Row by row: one base blob is in memory at a time, never the whole store's.
                rows = (connection.execute(sql) if wanted is None
                        else _rows_for(connection, sql + ' WHERE p.file IN ({})', sorted(wanted)))
                held = set(projection.flagged_files(connection)) | set(projection.drift_files(connection))
                clean = {rel: value for rel, value, quarantined, base in rows
                         if not quarantined and rel not in held and base is not None
                         and hashlib.sha1(bytes(base)).hexdigest() == value}
                published = {}
            else:
                clean = {rel: value for rel, value in checkout_store.trusted_bases(connection, linked.id).items()
                         if wanted is None or rel in wanted}
                based = set(clean)
                for rel in checkout_store.holds(connection, linked.id):
                    clean.pop(rel, None)
                sql = 'SELECT file,content_hash FROM projection'
                rows = (connection.execute(sql) if wanted is None
                        else _rows_for(connection, sql + ' WHERE file IN ({})', sorted(wanted)))
                published = {rel: value for rel, value in rows if rel not in based}
        finally:
            connection.rollback()

    def skippable(rel, digests):
        if digests is None or (wanted is not None and rel not in wanted):
            return False
        if rel in published:
            return published[rel] in digests.variants
        return clean.get(rel) == digests.digest
    return skippable


def _receipt(owner, scope, key):
    with closing(owner._connect(readonly=True)) as connection:
        row = connection.execute('SELECT outcome_json FROM command_receipts WHERE store_id=? AND caller_scope=? '
                                 'AND request_id=?', (owner.identity['store_id'], scope, key)).fetchone()
    return None if row is None else json.loads(row[0])


def _await(future, timeout):
    """`(receipt, 'committed')`, `(None, None)` while it is still running after `timeout`,
    `(None, 'not_committed')` for a command cancelled before execution, or `(None, 'error')`
    when the writer failed without a verdict (e.g. ServiceUnavailable: interrupted). A
    refusal (Conflict and other ValueErrors: the transaction rolled back) is raised."""
    try:
        return future.result(timeout=timeout), 'committed'
    except FutureTimeout:
        return None, None
    except CancelledBeforeExecution:
        return None, 'not_committed'
    except ValueError:
        raise
    except Exception:  # noqa: BLE001 - the durable receipt, not the transport error, decides
        return None, 'error'


def _settle(owner, future, scope, key, grace):
    """`(receipt or None, outcome, grace waited)` of a submitted sync.apply that did not
    simply finish; a store error while settling (a locked database) is `uncertain`.
    """
    started = time.monotonic()
    receipt, outcome = _await(future, grace)
    waited = time.monotonic() - started
    try:
        return (*_settled(owner, future, scope, key, receipt, outcome), waited)
    except sqlite3.Error:
        return None, 'uncertain', waited


def _settled(owner, future, scope, key, receipt, outcome):
    """The rest of `_settle`, after its grace wait: `(receipt or None, outcome)`.

    - `committed`: it finished within `grace`, or its durable receipt exists (a receipt
      commits in the command's own transaction).
    - `not_committed`: it was cancelled before execution (cancel and admission share one
      ordering point, so a cancelled command can never run).
    - `uncertain`: the writer is still executing it, or failed without a verdict
      (interrupted) and no receipt exists.
    A refusal (a ValueError such as Conflict) is raised to the caller: nothing committed.
    """
    if outcome is None:
        if owner.cancel(scope, key)['state'] == 'cancelled_before_execution':
            return None, 'not_committed'
        receipt = _receipt(owner, scope, key)
        if receipt is not None:
            return receipt, 'committed'
        if not future.done():
            return None, 'uncertain'
        receipt, outcome = _await(future, 0)  # finished between the grace and the cancel
    if outcome == 'error':
        receipt = _receipt(owner, scope, key)
        return (receipt, 'committed') if receipt is not None else (None, 'uncertain')
    return receipt, outcome


def summarize(result):
    """A bounded completed result: receipt keys, not whole receipts.

    Every import keeps file/state/commit_seq/request_id (its receipt key under
    `receipt_scope`) until the byte budget is reached; the rest are counted in
    `imports_omitted` and stay inspectable as receipts in that scope.
    """
    summary = {key: value for key, value in result.items() if key not in ('imports', 'warnings')}
    warnings = list(result['warnings'])
    summary['warnings'] = warnings[:_WARNINGS_KEPT]
    if len(warnings) > _WARNINGS_KEPT:
        summary['warnings_omitted'] = len(warnings) - _WARNINGS_KEPT
    imports, used = [], len(encode(summary).encode()) + 64
    for item in result['imports']:
        entry = {key: item[key] for key in ('file', 'state', 'commit_seq', 'request_id') if key in item}
        size = len(encode(entry).encode()) + 1
        if used + size > SUMMARY_BYTES:
            break
        imports.append(entry)
        used += size
    summary['imports'] = imports
    if len(imports) < len(result['imports']):
        summary['imports_omitted'] = len(result['imports']) - len(imports)
    return summary


def bound(result, named=()):
    """The pending result returned to the caller, within the same byte budget.

    Unlike the stored summary it keeps each import's reason and notices, which
    say why a file was not synchronized; whatever does not fit is counted in
    `*_omitted` and stays inspectable as receipts under `receipt_scope`. Entries
    for the files the caller named come first, so a caller that asked about one
    file always learns its outcome however many other files are pending.
    """
    lists = ('unresolved', 'notices', 'imports', 'observes', 'warnings')  # the why first
    named = set(named or ())
    prefixes = tuple(f'sync pending: {rel}: ' for rel in named)

    def pinned(key, item):
        if key in ('imports', 'observes'):
            return isinstance(item, dict) and item.get('file') in named
        if key == 'unresolved':
            return item in named
        return key == 'notices' and bool(prefixes) and item.startswith(prefixes)

    bounded = {key: value for key, value in result.items() if key not in lists}
    used = len(encode(bounded).encode()) + 256
    kept = {key: set() for key in lists}
    for first in (True, False):
        for key in lists:
            for index, item in enumerate(result.get(key) or []):
                if pinned(key, item) != first:
                    continue
                size = len(encode(item).encode()) + 1
                if used + size > SUMMARY_BYTES:
                    break
                kept[key].add(index)
                used += size
    for key in lists:
        items = result.get(key) or []
        bounded[key] = [items[index] for index in sorted(kept[key])]
        omitted = len(items) - len(bounded[key])
        if omitted:
            bounded[key + '_omitted'] = omitted
    return bounded


def operation_scope(caller_scope, request_id):
    if not all(isinstance(value, str) and 1 <= len(value) <= 256 for value in (caller_scope, request_id)):
        raise ValueError('sync requires caller_scope and request_id')
    return 'sync-' + hashlib.sha256(encode([caller_scope, request_id]).encode()).hexdigest()


# The `sync` metrics record always carries these (0 when nothing was counted).
SYNC_COUNTERS = dict.fromkeys(('files_selected', 'files_stated', 'files_read', 'bytes_read', 'files_parsed',
                               'cache_hits', 'cache_misses', 'directories_listed', 'batches'), 0)


def synchronize(owner, **arguments):
    with metrics.scope('sync', caller_scope=arguments.get('caller_scope'), request_id=arguments.get('request_id'),
                       **SYNC_COUNTERS) as record:
        result = _synchronize(owner, **arguments)
        if metrics.ENABLED:
            record.update(state=result.get('state'), imports=len(result.get('imports') or ()),
                          unresolved=len(result.get('unresolved') or ()))
    # A completed result is already the bounded stored summary.
    return result if result.get('state') == 'synchronized' else bound(result, arguments.get('files') or ())


def _synchronize(owner, *, caller_scope, request_id, import_files=True, through=0,
                 files=None, take_file=False, timeout=SYNC_TIMEOUT, worktree=None, progress=None):
    """`progress` (in-process only, never over IPC) is a sync job's `sync_jobs.Progress`:
    it is told what this round selects, reaches and commits, so a job's counts span rounds."""
    options = dict(import_files=import_files, through=through, files=files, take_file=take_file)
    sync.validate_input(options)
    validate_timeout(timeout)
    if worktree is not None and (not isinstance(worktree, str) or not worktree):
        raise ValueError('worktree must be an absolute checkout path')
    scope = operation_scope(caller_scope, request_id)
    # `imports` are domain writes; `observed` counts committed observes (the published bytes
    # recorded as merge base) and `observes` lists any whose outcome the budget left unsettled.
    result = dict(state='pending', through=through, captured=False, selected=0, imports=[], observed=0, observes=[],
                  unresolved=[], notices=[], warnings=[], caller_scope=caller_scope,
                  request_id=request_id, receipt_scope=scope, import_files=import_files)
    deadline = time.monotonic() + max(0, timeout)
    reserve = min(FILE_RESERVE, max(0, timeout) * RESERVE_SHARE)
    grace_used = 0.0  # past the budget, taken from the FINISH_TIMEOUT allowance
    acquired = False
    with owner.guard:
        owner.active_syncs += 1

    def remaining():
        return max(0, deadline - time.monotonic())

    # Membership sets beside the ordered lists: a sync may name tens of thousands of paths.
    unresolved_seen, notices_seen = set(), set()

    def pending(rel, reason):
        if rel and rel not in unresolved_seen:
            unresolved_seen.add(rel)
            result['unresolved'].append(rel)
            if progress is not None:
                progress.pending(rel)
        notice = f'sync pending: {rel}: {reason}' if rel else f'sync pending: {reason}'
        if notice not in notices_seen:
            notices_seen.add(notice)
            result['notices'].append(notice)

    def submit(operation, key, arguments):
        envelope = dict(protocol=2, store_id=owner.identity['store_id'], caller_scope=scope,
                        request_id=key, operation=operation, arguments=arguments, expected_revisions=[])
        # Validate before enqueue, including the fully encoded 1 MiB limit.
        contracts.validate(envelope)
        return owner.submit(envelope)

    def execute(operation, key, arguments, timeout=None):
        return submit(operation, key, arguments).result(timeout=remaining() if timeout is None else timeout)

    backlog = owner.root / '.taskmaster'
    # Only a full ordinary sync takes the stat fast path; a named resync or take_file
    # always reads the bytes (the explicit escape from any fingerprint doubt).
    fast = files is None and not take_file
    scan = None
    scanned_all = False  # every selected path went through the per-file loop

    def current(plan):
        if plan.observation is not None:
            return sync_files.unchanged(backlog, plan.observation)
        return sync_files.observe(backlog, plan.file) is None

    try:
        if owner.stopping.is_set():
            pending(None, 'coordinator stopping')
            return result
        acquired = owner.publication.acquire(timeout=remaining())
        if not acquired:
            pending(None, 'publisher busy')
            return result
        refusal = owner.publication_refusal()
        if refusal:
            pending(None, refusal)
            return result
        from . import checkouts
        from .git import GitRefused
        linked = None
        if worktree is not None:
            try:
                named = checkouts.resolve(owner.root, worktree)
            except GitRefused as exc:
                raise ValueError(str(exc)) from None
            if named.linked:
                linked = named
                backlog = linked.backlog
                options['checkout'] = linked.id
                result['checkout'] = linked.public()
                result['warnings'].extend(checkouts.register(owner, linked))
        # Git detection runs on the checkout this sync imports from; a root that is
        # not a Git top level has no HEAD to observe.
        observed_checkout = linked or checkouts.optional_main(owner.root)
        # Refuse an impossible target before anything, including imports, commits.
        with closing(owner._connect(readonly=True)) as connection:
            if through > connection.execute('SELECT COALESCE(MAX(seq),0) FROM domain_events').fetchone()[0]:
                raise ValueError('flush target exceeds committed sequence')
        try:
            execute('sync.begin', 'begin', {'input': options})
        except FutureTimeout:
            pending(None, 'begin outcome uncertain; inspect receipt_scope/begin or retry the same sync id')
            return result
        with closing(owner._connect(readonly=True)) as connection:
            state = sync.operation_state(connection, scope)
        if state['state'] == 'complete':
            return state['result']
        if linked is not None:
            for notice in checkouts.recover_intent(owner, linked):
                if notice.startswith('sync pending: '):
                    rel, _, reason = notice.removeprefix('sync pending: ').partition(': ')
                    pending(rel, reason)
                else:
                    result['warnings'].append(notice)
        selected = []
        observation, seen, unverified, moved = None, {}, None, False
        if import_files:
            scan = sync_files.open_scan(owner.root, backlog, fast=fast)
            if files is not None:
                selected = list(files)
            else:
                inventory = sync_files.discover(backlog, scan)
                selected = list(inventory.files)
                result['warnings'].extend(f'duplicate import path skipped: {rel}; canonical {canonical}'
                                          for rel, canonical in inventory.duplicates.items())
                for rel, reason in inventory.refused.items():
                    pending(rel, reason)
                chosen = set(selected)
                with closing(owner._connect(readonly=True)) as connection:
                    # Include missing known authored paths so absence requests
                    # repair. Derived indexes/local files are not import inputs.
                    for (rel,) in connection.execute('SELECT file FROM projection ORDER BY file'):
                        try:
                            classify(rel)
                        except ValueError:
                            continue
                        if rel not in inventory.duplicates and rel not in chosen:
                            chosen.add(rel)
                            selected.append(rel)
            # How many paths this sync judges: the caller's denominator for "unchanged".
            result['selected'] = len(selected)
            if progress is not None:
                progress.selecting(len(selected))
            if metrics.ENABLED:
                metrics.add('files_selected', len(selected))
            # No size ceiling (N16): every selected path is enumerated here, and the per-file
            # loop below reads, parses and holds one batch at a time. The whole-set judgements
            # (Git classification, holds, completion) still see every path; see
            # docs/plans/2026-09-29-n16-batched-sync.md.
            from . import git as managed_git
            # Paths a managed checkout left differing from the published generation are
            # drift: restored ones resolve here; the rest are neither imported nor repaired.
            drift = (managed_git.prune_drift(owner) if linked is None
                     else checkouts.prune_drift(owner, linked))
            if observed_checkout is not None and not take_file:
                # Step 10: Git operations that bypassed the coordinator put bytes here
                # that are drift (an older or foreign generation), never import authority.
                # Named files (MCP resync) are classified too; take_file is the explicit adopt.
                try:
                    observation, found, warnings, seen = checkouts.detect(owner, observed_checkout, selected, drift,
                                                                          scan=scan, deadline=deadline - reserve)
                except checkouts.DetectInterrupted as exc:
                    # Nothing is imported unclassified; the scan keeps what this pass read.
                    pending(None, f'Git classification stopped ({exc}); nothing imported; retry the same sync id')
                    return result
                except (GitRefused, OSError) as exc:
                    # Fail closed: nothing is imported while Git state is unknown.
                    unverified = f'Git state could not be inspected ({exc}); not imported, retry the sync'[:500]
                    found, warnings = {}, [unverified]
                result['warnings'].extend(warnings)
                drift |= set(found)
            owner.checkpoint('sync_files_selected')
            size = batch_size()
            for first in range(0, len(selected), size):
                batch = selected[first:first + size]
                if metrics.ENABLED:
                    metrics.add('batches')
                # Only this batch's rows (and base blobs) are loaded for the no-op rule.
                skippable = _unchanged_rule(owner, linked, batch) if fast else None
                for rel in batch:
                    # Start a file only with room left to finish it: a write submitted as the budget
                    # ends is what used to be reported "uncertain" although it committed.
                    if remaining() <= reserve or owner.stopping.is_set():
                        pending(rel, 'time budget exhausted or coordinator stopping; retry the same sync id')
                        return result
                    if progress is not None:
                        progress.reached(rel)
                    if rel in drift and not take_file:
                        pending(rel, managed_git.DRIFT_GUIDANCE if linked is None else checkouts.LINKED_DRIFT)
                        continue
                    if unverified:
                        pending(rel, unverified)
                        continue
                    if skippable is not None and skippable(rel, _hit(scan, rel)):
                        continue  # exactly prepare's no-op outcome; see _unchanged_rule
                    try:
                        with closing(owner._connect(readonly=True)) as connection, Repository(connection).snapshot() as snapshot:
                            plan = prepare(snapshot, backlog, rel, take_file=take_file,
                                           checkout=None if linked is None else linked.id, scan=scan)
                        if plan.arguments is None:
                            if plan.state not in ('unchanged', 'establish'):
                                pending(rel, plan.reason)
                            continue
                        owner.checkpoint('sync_prepared')
                        if not current(plan):
                            pending(rel, 'file changed after parse; not imported')
                            continue
                        if rel in seen and seen[rel] != (None if plan.observation is None else plan.observation.digest):
                            # Only the classified bytes were judged authored (a Git restore may land between).
                            pending(rel, 'file changed after Git classification; not imported, retry the sync')
                            continue
                        # The HEAD guard keeps bytes a bypassed Git operation put here from being
                        # imported as authored edits. An `observe` imports nothing: sync.apply accepts
                        # it only for bytes equal to the published generation's recorded digest (or,
                        # D7, to its trusted base up to line endings) under the manifest token, and it
                        # changes no domain row. Those bytes are the published generation whatever put
                        # them on disk, so a HEAD move cannot change what an observe records; and the
                        # completion check still re-reads HEAD before the observation may advance.
                        # Every other mode (apply, conflict, quarantine, repair) keeps the check.
                        guarded = plan.arguments['mode'] != 'observe'
                        if (guarded and observation is not None and not moved
                                and checkouts.observe(observed_checkout) != observation):
                            moved = True
                        if moved and guarded:
                            pending(rel, 'HEAD moved during the sync; not imported, retry the sync')
                            continue
                        key = hashlib.sha256(encode(plan.arguments).encode()).hexdigest()
                        future = submit('sync.apply', key, plan.arguments)
                        receipt, outcome = _await(future, remaining())
                        if outcome != 'committed':
                            receipt, outcome, waited = _settle(
                                owner, future, scope, key, max(0.0, IMPORT_GRACE - grace_used) if outcome is None else 0)
                            grace_used += waited  # only the grace wait, not the cancel/receipt reads
                        if receipt is None:
                            observing = plan.state == 'observe'
                            result['observes' if observing else 'imports'].append(dict(
                                file=rel, state=outcome, caller_scope=scope, request_id=key,
                                may_have_committed=outcome == 'uncertain'))
                            pending(rel, _UNSETTLED[observing, outcome])
                            continue  # the loop's budget check ends the sync once the time is gone
                        if progress is not None:
                            progress.committed(rel, 'observed' if plan.state == 'observe'
                                               else receipt['result'].get('state'), receipt.get('commit_seq'))
                        if plan.state == 'observe':
                            result['observed'] += 1
                        else:
                            result['imports'].append(dict(receipt['result'], commit_seq=receipt['commit_seq'],
                                                          caller_scope=scope, request_id=key))
                            if rel in drift and receipt['result'].get('state') == 'accepted' and linked is None:
                                managed_git.drop_drift(owner, [rel])  # explicitly taken
                                drift.discard(rel)
                        owner.checkpoint('sync_import_committed')
                        if not current(plan):
                            pending(rel, 'file changed after import commit; newer bytes retained')
                        if plan.state in {'conflict', 'quarantine'}:
                            pending(rel, plan.reason)
                    except (ValueError, KeyError, OSError) as exc:
                        pending(rel, str(exc))
                if scan is not None and fast:
                    # Keep what this batch learned: a crash or an exhausted budget then
                    # restarts the next attempt on stat calls for the scanned batches.
                    sync_files.save_scan(owner.root, scan, complete=False)
                owner.checkpoint('sync_batch_scanned')
            scanned_all = True

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
                # Imports may have committed: report, never raise past them.
                pending(None, 'flush target exceeds committed sequence')
                return result
            result.update(through=max(through, captured), captured=True)
            owner.checkpoint('sync_pinned')
            if progress is not None:
                progress.phase = 'publishing'
            try:
                publication = owner.flush(result['through'], timeout=remaining())
            except Exception as exc:
                # Imports have committed; an export failure is a pending barrier.
                pending(None, f'projection publication failed: {exc}')
                return result
            owner.checkpoint('sync_published')
            for notice in publication['notices']:
                if notice not in notices_seen:
                    notices_seen.add(notice)
                    result['notices'].append(notice)
            if publication['state'] != 'exported':
                pending(None, 'projection publication incomplete')
            if linked is not None:
                # A participating linked checkout: the published generation is copied in,
                # compare-and-swap against its own bases, while publication is held.
                for notice in checkouts.publish(owner, linked, result['through'], scan=scan if fast else None):
                    rel, _, reason = notice.removeprefix('sync pending: ').partition(': ')
                    pending(rel, reason)
                selected = []
            completion_pending = False
            with closing(owner._connect(readonly=True)) as connection:
                held = set(projection.flagged_files(connection)) | set(projection.drift_files(connection))
                held.update(row[0] for row in connection.execute('SELECT file FROM projection WHERE quarantined=1'))
                for rel in sorted(held if linked is None else ()):
                    reason = projection.held_file(connection, rel) or 'held projection'
                    pending(rel, f'{reason}; {checkouts.DERIVED_GUIDANCE}' if checkouts.derived(rel) else reason)
                if linked is None and import_files:
                    # A derived index is never import input, so nothing above looks at it;
                    # managed Git would refuse its differing bytes, and so must sync (D5).
                    for rel, value in connection.execute("SELECT file,content_hash FROM projection WHERE "
                                                         "file NOT LIKE 'local/%' AND content_hash!=''").fetchall():
                        if rel in held or not checkouts.derived(rel):
                            continue
                        actual = checkouts.observed_read(backlog, rel, scan)
                        if actual in (None, checkouts.UNREADABLE) or value not in checkouts._variants(actual):
                            pending(rel, 'derived index differs from the published generation; '
                                    + checkouts.DERIVED_GUIDANCE)
                for rel in selected:
                    try:
                        # A fresh lstat: publication may have rewritten the file since.
                        hit = _hit(scan, rel, fresh=True) if fast else None
                        if hit is not None:
                            exists, variants = True, hit.variants
                        else:
                            actual = scan.observe(rel) if scan is not None else sync_files.observe(backlog, rel)
                            exists = actual is not None
                            variants = set() if actual is None else sync_files.Digests.of(actual.content).variants
                        expected = connection.execute('SELECT content_hash FROM projection WHERE file=?', (rel,)).fetchone()
                        if expected is None and not exists:
                            continue  # a recorded move/tombstone requires absence
                        if expected is None or expected[0] not in variants:
                            completion_pending = True
                            pending(rel, 'file differs from the published generation at completion')
                    except (OSError, ValueError) as exc:
                        completion_pending = True
                        pending(rel, str(exc))
            if observation is not None and files is None and not moved and not completion_pending:
                # Only a full sync advances the observation, only while HEAD is still where
                # classification saw it (an older observation re-detects, never skips), and
                # never past a file the completion check could not confirm: the next sync
                # must judge it against the movement that may have put it there.
                try:
                    if checkouts.observe(observed_checkout) == observation:
                        checkouts.remember(owner, observed_checkout, observation)
                except (GitRefused, OSError) as exc:
                    result['warnings'].append(f'checkout observation not recorded: {exc}'[:500])
        finally:
            owner.execution.release()
            owner.resume_writer()
        if not result['notices'] and not result['unresolved']:
            # The caller receives exactly what is stored, so a lost-response
            # replay returns the same result.
            if progress is not None:
                # The whole job's counts, stored with the result so a replay reports them too.
                result['totals'] = progress.totals()
            completed = summarize(dict(result, state='synchronized'))
            try:
                execute('sync.finish', 'finish', {'result': completed},
                        timeout=_finish_timeout(remaining(), grace_used))
            except FutureTimeout:
                pending(None, 'completion receipt uncertain; retry the same sync id')
            except Exception as exc:
                pending(None, f'completion receipt not recorded: {exc}; retry the same sync id')
            else:
                return completed
        return result
    finally:
        if scan is not None and fast:
            # A sync that stopped before its last batch keeps the unscanned batches' entries.
            sync_files.save_scan(owner.root, scan, complete=scanned_all)
        if acquired:
            owner.publication.release()
        with owner.guard:
            owner.active_syncs -= 1
            owner.last_activity = time.monotonic()
