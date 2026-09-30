"""Linear queue transactions, without transport, file I/O or a second writer.

The coordinator owns execution. A claim is a finite batch, read before any
remote work; settlement and tracker refresh commit atomically. Queue-only
changes have receipts but do not invent entity revisions or projection jobs.
"""
from copy import deepcopy
import time

OPERATIONS = frozenset({'linear.claim', 'linear.settle'})
LEASE_SECONDS = 900
MAX_ATTEMPTS = 5
MAX_BATCH = 500
PUSH_FIELDS = frozenset({'last_pushed', 'push_hash', 'linear_issue_id', 'title', 'status'})


def validate(operation, arguments):
    from .contracts import _identifier
    if operation == 'linear.claim':
        if set(arguments) - {'owner', 'target_id', 'reset'}:
            raise ValueError('unknown linear.claim argument')
        if arguments.get('target_id'):
            _identifier(arguments['target_id'], 'target_id')
        elif arguments.get('target_id') not in (None, ''):
            raise ValueError('invalid target_id')
        if type(arguments.get('reset', False)) is not bool:
            raise ValueError('reset must be boolean')
    else:
        if set(arguments) - {'owner', 'seq', 'status', 'reason', 'tracker_id', 'updates'}:
            raise ValueError('unknown linear.settle argument')
        if type(arguments.get('seq')) is not int or arguments['seq'] < 1:
            raise ValueError('invalid queue sequence')
        if arguments.get('status') not in ('ok', 'skipped:not_found', 'skipped:no_tracker',
                                            'skipped:unchanged', 'error:transient', 'error:permanent'):
            raise ValueError('invalid Linear outcome')
        if not isinstance(arguments.get('reason', ''), str):
            raise ValueError('reason must be text')
        updates = arguments.get('updates', {})
        if (not isinstance(updates, dict) or set(updates) - PUSH_FIELDS
                or any(not isinstance(value, str) for value in updates.values())):
            raise ValueError('invalid tracker push fields')
        if updates:
            _identifier(arguments.get('tracker_id'), 'tracker_id')
            if arguments['status'] != 'ok':
                raise ValueError('only a successful push may update its tracker')
    if not isinstance(arguments.get('owner'), str) or not 1 <= len(arguments['owner']) <= 256:
        raise ValueError('invalid queue owner')


def classify(status, attempts, reason=''):
    last = attempts + 1 >= MAX_ATTEMPTS
    if status == 'ok':
        return 'ok', 'done', None
    if status == 'skipped:not_found':
        return 'skipped', 'failed' if last else 'pending', 'task not found after claim'
    if status.startswith('skipped:'):
        return 'skipped', 'done', None
    if status == 'error:permanent' or last:
        return 'permanent', 'failed', reason
    return 'transient', 'pending', reason


def apply(transaction, operation, arguments):
    validate(operation, arguments)
    transaction.result = (_claim(transaction, arguments) if operation == 'linear.claim'
                          else _settle(transaction, arguments))


def _claim(transaction, arguments):
    connection = transaction.connection
    now = time.time()
    target = arguments.get('target_id')
    scope = ' AND target_id=?' if target else ''
    params = (target,) if target else ()
    candidates = connection.execute(
        "SELECT COUNT(*) FROM linear_queue WHERE state IN ('pending','claimed','failed')" + scope, params).fetchone()[0]
    if target and not candidates:
        raise ValueError(f'no queued items for target_id {target!r}')
    # Target-scoped retry cannot recover or reset another target's row. Live
    # claims are excluded under the same transaction that admits this batch.
    if arguments.get('reset'):
        changed = connection.execute(
            "UPDATE linear_queue SET state='pending',attempts=0,last_error=NULL,claimed_by=NULL,claimed_at=NULL "
            "WHERE state IN ('pending','failed','claimed') AND "
            "(state<>'claimed' OR claimed_at IS NULL OR claimed_at<=?)" + scope,
            (now - LEASE_SECONDS, *params)).rowcount
    else:
        connection.execute(
            "UPDATE linear_queue SET state='pending',claimed_by=NULL,claimed_at=NULL WHERE state='claimed' "
            "AND (claimed_at IS NULL OR claimed_at<=?)" + scope, (now - LEASE_SECONDS, *params))
        changed = candidates
    rows = connection.execute(
        "SELECT seq,op,target_id,tracker_id,attempts FROM linear_queue WHERE state='pending'" + scope
        + ' ORDER BY seq LIMIT ?', (*params, MAX_BATCH)).fetchall()
    batch = []
    for seq, op, target_id, tracker_id, attempts in rows:
        if op == 'task_upsert':
            try:
                tracker_id = transaction.snapshot.get('task', target_id, fields=['tracker_id'])['fields'].get('tracker_id')
            except KeyError:
                pass
        # Retained pre-native queue rows may have a missing or stale tracker.
        # Bind the finite claim to current authoritative linkage, not that hint.
        connection.execute("UPDATE linear_queue SET state='claimed',claimed_by=?,claimed_at=?,tracker_id=? WHERE seq=?",
                           (arguments['owner'], now, tracker_id, seq))
        batch.append(dict(seq=seq, op=op, target_id=target_id, tracker_id=tracker_id, attempts=attempts))
    return {'items': batch, 'in_flight_skipped': candidates - changed}


def _settle(transaction, arguments):
    connection = transaction.connection
    row = connection.execute('SELECT state,claimed_by,attempts,tracker_id FROM linear_queue WHERE seq=?',
                             (arguments['seq'],)).fetchone()
    if row is None:
        raise KeyError(f"linear queue row {arguments['seq']} not found")
    if row[:2] != ('claimed', arguments['owner']):
        return {'applied': False}
    updates = arguments.get('updates') or {}
    if updates:
        if arguments['tracker_id'] != row[3]:
            raise ValueError('push outcome names a different claimed tracker')
        try:
            entity = transaction.snapshot.get('tracker', row[3], include_body=True)
        except KeyError:
            entity = None  # Remote success survives a concurrent local unlink.
        if entity is not None:
            from taskmaster.taskmaster_v3 import apply_tracker_updates
            fields = apply_tracker_updates(deepcopy(entity['fields']), **updates)
            transaction.replace('tracker', row[3], fields, entity['body'], before_entity=entity)
    bucket, state, error = classify(arguments['status'], row[2], arguments.get('reason', ''))
    connection.execute('UPDATE linear_queue SET state=?,last_error=?,attempts=attempts+?,claimed_by=NULL,claimed_at=NULL '
                       'WHERE seq=?', (state, error, int(error is not None), arguments['seq']))
    return {'applied': True, 'bucket': bucket, 'state': state}
