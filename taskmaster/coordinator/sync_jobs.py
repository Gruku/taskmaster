# User intent: `backlog_sync` starts a sync that the coordinator itself drives to completion, so
# a client with a fixed tool timeout (Codex: 30 s) only ever waits a bounded time and polls; the
# counts are the whole sync's, one sync runs at a time, and an ended sync replays faithfully.
"""Coordinator-driven sync jobs behind the `backlog_sync` MCP tool."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import re
import secrets
import threading
import time

from taskmaster.native import contracts, sync
from .protocol import SYNC_TIMEOUT, CoordinatorStopping

# The durable caller scope of every job's sync operation; ids are minted here, so no two
# sessions can name the same operation.
SCOPE = 'backlog-sync'
# The receipts scope of the jobs' own durable records (native `sync.job`).
RECORD_SCOPE = 'backlog-sync-job'
RECORD_TIMEOUT = 30
ID_PATTERN = re.compile(r'\d{8}T\d{6}Z-[0-9a-f]{8}')
# One round is an ordinary sync under the normal budget (it bounds the publication hold).
# A round that ends retryably is run again under the same operation id: files it already
# imported are then unchanged (their fingerprints match the new base) and cost a stat each.
ROUND_BUDGET = SYNC_TIMEOUT
MAX_ROUNDS = 20
RETRY_PAUSE = 0.5
# Notices a further round can clear; anything else (a held file, a refusal) needs a person.
RETRYABLE = ('retry', 'time budget', 'publisher busy', 'writer busy', 'durable jobs remain')
MAX_WAIT = 60
KEPT_JOBS = 32
NAMES = 20


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class Progress:
    """What a job's rounds have seen; written by the sync thread, read by pollers."""

    def __init__(self):
        self.round, self.phase, self.selected, self.checked = 0, 'starting', 0, 0
        self.reached_files: set[str] = set()   # every file a round's loop got to, across rounds
        self.pending_files: set[str] = set()   # files the latest round left unsynchronized
        self.outcomes: dict[str, str] = {}     # file -> last committed state across rounds
        self.seq = None

    def new_round(self, number):
        self.round, self.phase, self.checked = number, 'selecting', 0
        self.pending_files = set()

    def selecting(self, count):
        # Found; before the per-file loop comes the whole-set Git classification.
        self.selected, self.phase = count, 'classifying'

    def reached(self, rel):
        self.phase = 'checking'
        self.checked += 1
        self.reached_files.add(rel)

    def pending(self, rel):
        self.pending_files.add(rel)

    def committed(self, rel, state, seq):
        if state != 'observed':  # an observe records the published bytes as base: unchanged
            self.outcomes[rel] = state
        if isinstance(seq, int):
            self.seq = max(self.seq or 0, seq)

    def totals(self) -> dict:
        outcomes, reached, held = dict(self.outcomes), set(self.reached_files), set(self.pending_files)
        groups = {label: sorted(rel for rel, state in outcomes.items() if state == state_name)
                  for label, state_name in (('imported', 'accepted'), ('repaired', 'repair_pending'),
                                            ('conflicts', 'conflict'), ('quarantined', 'quarantined'))}
        totals = {label: len(files) for label, files in groups.items()}
        # Unchanged means checked and found unchanged: never a file no round reached, nor one
        # left pending (drift, held, a changed-after-parse file), nor one this job imported.
        pending = held - set(outcomes)
        unchanged = len(reached - set(outcomes) - held)
        totals.update({f'{label}_files': files[:NAMES] for label, files in groups.items()},
                      selected=self.selected, unchanged=unchanged, pending=len(pending),
                      pending_files=sorted(pending)[:NAMES],
                      not_checked=max(0, self.selected - len(reached | set(outcomes) | held)),
                      rounds=self.round, seq=self.seq, finished_at=_now())
        return totals


class Job:
    def __init__(self, ident, files):
        self.id, self.files = ident, files
        self.state, self.started_at, self.finished_at = 'running', _now(), None
        self.progress, self.result, self.error = Progress(), None, None
        self.totals, self.held, self.reported = None, None, False
        self.done = threading.Event()

    def finish(self, owner, state):
        self.totals = (self.result or {}).get('totals') or self.progress.totals()
        try:
            self.held = _held(owner)
        except Exception as exc:  # noqa: BLE001 -- advisory: the job's own outcome stands
            self.held = {'conflicts': [], 'quarantined': [], 'error': str(exc)[:200]}
        self.state, self.finished_at = state, self.totals['finished_at']

    def record(self) -> dict:
        notices = list((self.result or {}).get('notices') or []) if self.state != 'complete' else []
        return dict(state=self.state, files=self.files, started_at=self.started_at, finished_at=self.finished_at,
                    totals=self.totals, held=self.held, error=self.error, notices=notices[:50],
                    warnings=list((self.result or {}).get('warnings') or [])[:20])


def _held(owner) -> dict:
    from taskmaster.native import projection
    with closing(owner._connect(readonly=True)) as connection:
        conflicts = list(projection.flagged_files(connection))
        quarantined = [row[0] for row in connection.execute(
            "SELECT file FROM projection WHERE quarantined=1 ORDER BY file") if row[0] not in conflicts]
    return {'conflicts': conflicts[:NAMES], 'conflict_count': len(conflicts),
            'quarantined': quarantined[:NAMES], 'quarantined_count': len(quarantined)}


def _write(owner, job, record):
    """The job's durable record (native `sync.job`): replayable after a coordinator restart."""
    envelope = dict(protocol=2, store_id=owner.identity['store_id'], caller_scope=RECORD_SCOPE,
                    request_id=f"{job.id}:{record['state']}", operation='sync.job',
                    arguments={'id': job.id, 'record': record}, expected_revisions=[])
    contracts.validate(envelope)
    owner.submit(envelope).result(timeout=RECORD_TIMEOUT)


def _run(owner, job):
    try:
        try:
            _write(owner, job, dict(state='running', files=job.files, started_at=job.started_at))
            previous = None
            for number in range(1, MAX_ROUNDS + 1):
                job.progress.new_round(number)
                job.result = owner.sync(caller_scope=SCOPE, request_id=job.id, files=job.files,
                                        timeout=ROUND_BUDGET, progress=job.progress)
                if job.result.get('state') == 'synchronized':
                    break
                notices = list(job.result.get('notices') or [])
                retryable = any(word in notice for notice in notices for word in RETRYABLE)
                # No progress between two rounds (same reasons, same imports): stop, never spin.
                seen = (tuple(notices), len(job.progress.outcomes))
                if owner.stopping.is_set() or not retryable or seen == previous:
                    break
                previous = seen
                owner.stopping.wait(RETRY_PAUSE)
            job.finish(owner, 'complete' if job.result.get('state') == 'synchronized' else 'incomplete')
        except Exception as exc:  # noqa: BLE001 -- the job reports it; the coordinator lives on
            job.error = f'{type(exc).__name__}: {exc}'[:500]
            job.finish(owner, 'failed')
        try:
            _write(owner, job, job.record())
        except Exception as exc:  # noqa: BLE001 -- the in-memory answer stands; say it was not kept
            job.warnings = [f'this result was not stored ({type(exc).__name__}); it is lost if the coordinator stops']
    finally:
        # Activity first: a waiter woken by `done` must not find an owner already idle-expired.
        with owner.guard:
            owner.last_activity = time.monotonic()
        job.done.set()


def _start(owner, job):
    owner.sync_jobs[job.id] = job
    for ident in [key for key, old in owner.sync_jobs.items() if old.state != 'running'][:-KEPT_JOBS]:
        del owner.sync_jobs[ident]
    thread = threading.Thread(target=_run, args=(owner, job), name=f'taskmaster-sync-{job.id}', daemon=True)
    owner.threads.append(thread)
    thread.start()
    return job


def running(owner):
    """The running `backlog_sync` job, or None. Anything that stops or retires the coordinator
    while idle must treat a running job as busy: it lives between rounds too."""
    with owner.guard:
        return next((job for job in getattr(owner, 'sync_jobs', {}).values() if not job.done.is_set()), None)


def _stored(owner, ident):
    with closing(owner._connect(readonly=True)) as connection:
        return sync.job_record(connection, ident)


def request(owner, *, sync_id=None, files=None, wait_seconds=0):
    """Start a job (or attach to the one running), or look one up by id; wait up to
    `wait_seconds` for it to end, then describe it."""
    if type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= MAX_WAIT:
        raise ValueError(f'sync wait must be 0..{MAX_WAIT} seconds')
    if files is not None:
        sync.validate_input(dict(import_files=True, through=0, files=files, take_file=False))
    if sync_id is not None and (not isinstance(sync_id, str) or not ID_PATTERN.fullmatch(sync_id)):
        raise ValueError(f'{sync_id!r} is not a sync id backlog_sync issued; start a sync with backlog_sync()')
    answer = {}
    with owner.guard:
        if owner.stopping.is_set():
            raise CoordinatorStopping('coordinator is stopping; no sync was started; call backlog_sync again')
        active = running(owner)
        if sync_id is None:
            if active is not None:
                job = active
                answer['attached'] = True
                if files != job.files:
                    answer['not_started'] = files
            else:
                ident = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4)
                job = _start(owner, Job(ident, files))
        else:
            job = owner.sync_jobs.get(sync_id)
            if job is None:
                stored = _stored(owner, sync_id)
                if stored is None:
                    raise ValueError(f'unknown sync id {sync_id}; start a fresh backlog_sync()')
                if files is not None and files != stored.get('files'):
                    raise ValueError(f'sync {sync_id} was started with files={stored.get("files")}; a different '
                                     f'set needs a fresh backlog_sync(files=...)')
                if stored['state'] == 'running':
                    # Begun under an earlier coordinator that stopped: never re-run it silently.
                    return dict(sync_id=sync_id, state='interrupted', started_at=stored.get('started_at'),
                                running=None if active is None else active.id)
                return dict(stored, sync_id=sync_id, replay=True)
            if files is not None and files != job.files:
                raise ValueError(f'sync {sync_id} was started with files={job.files}; a different set needs '
                                 f'a fresh backlog_sync(files=...)')
    job.done.wait(wait_seconds)
    with owner.guard:
        finished = job.done.is_set()
        replay = finished and job.reported
        if finished:
            job.reported = True
    if finished:
        answer.update(job.record(), sync_id=job.id, replay=replay)
        answer['warnings'] = answer['warnings'] + list(getattr(job, 'warnings', []))
        return answer
    progress = job.progress
    answer.update(sync_id=job.id, state='running', started_at=job.started_at, files=job.files,
                  round=progress.round, phase=progress.phase, selected=progress.selected,
                  checked=progress.checked, totals=progress.totals())
    return answer
