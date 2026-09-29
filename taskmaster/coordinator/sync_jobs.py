# User intent: `backlog_sync` starts a sync that the coordinator itself drives to completion, so
# a client with a fixed tool timeout (Codex: 30 s) only ever waits a bounded time and polls; the
# counts are the whole sync's, one sync runs at a time, and a completed sync replays labelled.
"""Coordinator-driven sync jobs behind the `backlog_sync` MCP tool."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import re
import secrets
import threading
import time

from taskmaster.native import sync
from .protocol import SYNC_TIMEOUT

# The durable caller scope of every job's sync operation; ids are minted here, so no two
# sessions can name the same operation.
SCOPE = 'backlog-sync'
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
        self.outcomes: dict[str, str] = {}  # file -> last committed state across rounds
        self.seq = None

    def new_round(self, number):
        self.round, self.phase, self.checked = number, 'selecting', 0

    def selecting(self, count):
        self.selected, self.phase = count, 'checking'

    def reached(self, rel):
        self.checked += 1

    def committed(self, rel, state, seq):
        if state != 'observed':  # an observe records the published bytes as base: unchanged
            self.outcomes[rel] = state
        if isinstance(seq, int):
            self.seq = max(self.seq or 0, seq)

    def totals(self) -> dict:
        outcomes = dict(self.outcomes)
        groups = {label: sorted(rel for rel, state in outcomes.items() if state == state_name)
                  for label, state_name in (('imported', 'accepted'), ('repaired', 'repair_pending'),
                                            ('conflicts', 'conflict'), ('quarantined', 'quarantined'))}
        totals = {label: len(files) for label, files in groups.items()}
        totals.update({f'{label}_files': files[:NAMES] for label, files in groups.items()},
                      selected=self.selected, unchanged=max(0, self.selected - len(outcomes)),
                      rounds=self.round, seq=self.seq, finished_at=_now())
        return totals


class Job:
    def __init__(self, ident, files):
        self.id, self.files = ident, files
        self.state, self.started_at, self.finished_at = 'running', _now(), None
        self.progress, self.result, self.error = Progress(), None, None
        self.totals, self.reported = None, False
        self.done = threading.Event()

    def finish(self, state):
        self.totals = (self.result or {}).get('totals') or self.progress.totals()
        self.state, self.finished_at = state, self.totals['finished_at']
        self.done.set()


def _run(owner, job):
    try:
        previous = None
        for number in range(1, MAX_ROUNDS + 1):
            job.progress.new_round(number)
            job.result = owner.sync(caller_scope=SCOPE, request_id=job.id, files=job.files,
                                    timeout=ROUND_BUDGET, progress=job.progress)
            if job.result.get('state') == 'synchronized':
                return job.finish('complete')
            notices = list(job.result.get('notices') or [])
            retryable = any(word in notice for notice in notices for word in RETRYABLE)
            # No progress between two rounds (same reasons, same imports): stop, never spin.
            seen = (tuple(notices), len(job.progress.outcomes))
            if owner.stopping.is_set() or not retryable or seen == previous:
                break
            previous = seen
            owner.stopping.wait(RETRY_PAUSE)
        job.finish('incomplete')
    except Exception as exc:  # noqa: BLE001 -- the job reports it; the coordinator lives on
        job.error = f'{type(exc).__name__}: {exc}'[:500]
        job.finish('failed')
    finally:
        with owner.guard:
            owner.last_activity = time.monotonic()


def _start(owner, job):
    owner.sync_jobs[job.id] = job
    for ident in [key for key, old in owner.sync_jobs.items() if old.state != 'running'][:-KEPT_JOBS]:
        del owner.sync_jobs[ident]
    thread = threading.Thread(target=_run, args=(owner, job), name=f'taskmaster-sync-{job.id}', daemon=True)
    owner.threads.append(thread)
    thread.start()
    return job


def running(owner) -> bool:
    return any(job.state == 'running' for job in getattr(owner, 'sync_jobs', {}).values())


def _stored(owner, ident):
    from .sync_worker import operation_scope
    with closing(owner._connect(readonly=True)) as connection:
        return sync.operation_state(connection, operation_scope(SCOPE, ident))


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
            raise ValueError('coordinator stopping; call backlog_sync again')
        if sync_id is None:
            job = next((job for job in owner.sync_jobs.values() if job.state == 'running'), None)
            if job is not None:
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
                if stored.get('state') == 'complete':
                    result = stored['result']
                    totals = result.get('totals') or {}
                    return dict(sync_id=sync_id, state='complete', replay=True, files=stored['input']['files'],
                                finished_at=totals.get('finished_at'), totals=totals,
                                warnings=result.get('warnings') or [])
                # Begun but never finished (the coordinator restarted): resume the same operation.
                job = _start(owner, Job(sync_id, stored['input']['files']))
                answer['resumed'] = True
            if files is not None and files != job.files:
                raise ValueError(f'sync {sync_id} was started with files={job.files}; a different set needs '
                                 f'a fresh backlog_sync(files=...)')
    job.done.wait(wait_seconds)
    with owner.guard:
        replay = job.state != 'running' and job.reported
        if job.state != 'running':
            job.reported = True
    progress = job.progress
    answer.update(sync_id=job.id, state=job.state, replay=replay, files=job.files, started_at=job.started_at,
                  finished_at=job.finished_at, round=progress.round, phase=progress.phase,
                  selected=progress.selected, checked=progress.checked,
                  totals=job.totals if job.totals is not None else progress.totals(), error=job.error)
    if job.state != 'running' and job.result is not None:
        answer.update(notices=list(job.result.get('notices') or []),
                      warnings=list(job.result.get('warnings') or []))
    return answer
