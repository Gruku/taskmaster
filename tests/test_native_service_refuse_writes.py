# User intent: prove Coordinator.refuse_writes (team-server-014 C1): after 014's snapshot, every queued, paused and
# later write fails with the reason instead of being drained at stop, so no client is told a write succeeded that the
# bundle lacks.
from contextlib import closing

import pytest

from taskmaster.coordinator.protocol import ServiceUnavailable, connect
from taskmaster.coordinator.service import Coordinator

from test_native_service import root  # noqa: F401  (fixture)


def envelope(owner, key, title):
    return {'protocol': 2, 'store_id': owner.identity['store_id'], 'caller_scope': 'refuse-tests',
            'request_id': key, 'operation': 'task.patch', 'arguments': {'id': 'test-epic-001', 'set': {'title': title}},
            'expected_revisions': []}


def max_seq(root):
    with closing(connect(root, readonly=True)) as connection:
        return connection.execute('SELECT COALESCE(MAX(seq),0) FROM domain_events').fetchone()[0]


def test_refuse_writes_fails_queued_paused_and_later_writes(root):
    with Coordinator(root) as owner:
        before = max_seq(root)
        owner.pause_writer()
        futures = [owner.submit(envelope(owner, f'k{n}', f'Title {n}')) for n in range(3)]
        owner.refuse_writes('the backlog moved to the team server; nothing was written')
        owner.resume_writer()
        for future in futures:
            with pytest.raises(ServiceUnavailable, match='moved to the team server'):
                future.result(timeout=10)
        with pytest.raises(ServiceUnavailable, match='moved to the team server'):
            owner.submit(envelope(owner, 'late', 'Late'))
        owner.stop()
        owner.close()
    assert max_seq(root) == before


def test_a_coordinator_that_never_refuses_still_writes(root):
    with Coordinator(root) as owner:
        before = max_seq(root)
        assert owner.refusing is None
        owner.submit(envelope(owner, 'ok', 'Fine')).result(timeout=10)
        assert max_seq(root) > before


def test_refusal_leaves_reads_and_flush_working(root):
    with Coordinator(root) as owner:
        owner.refuse_writes('closed')
        with closing(owner._connect(readonly=True)) as connection:
            high = connection.execute('SELECT COALESCE(MAX(seq),0) FROM domain_events').fetchone()[0]
        owner.flush(high, timeout=30)
