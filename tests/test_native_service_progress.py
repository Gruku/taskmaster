"""A coordinator barrier covers earlier changelog debt, not just its target commit."""
from contextlib import closing
import json
import sqlite3

import pytest

from taskmaster.coordinator.client import Client
from taskmaster.coordinator.protocol import connect
from taskmaster.coordinator.service import Coordinator
from taskmaster.native.workflow import progress_pending_key, PROGRESS_LEGACY_KEY
from taskmaster.native_routing import progress
from test_native_service import root, request  # noqa: F401


@pytest.mark.parametrize('through', [0, 1, 10**12 - 1, 10**12])
def test_cumulative_progress_check_excludes_later_rows_but_includes_seed_zero(through):
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.execute('CREATE TABLE sync_state(key TEXT PRIMARY KEY,value_json TEXT)')
        progress._put(connection, progress.SEEDED_KEY, {'seq': 0})
        progress._put(connection, progress_pending_key(through + 1, 0), {'ts': '', 'text': 'Later'})
        assert not progress.owes_through(connection, through)
        progress._put(connection, progress_pending_key(0, 0), {'ts': '', 'text': 'Earlier seed'})
        assert progress.owes_through(connection, through)


@pytest.mark.parametrize('debt', ['completion', 'seed_row', 'legacy_list', 'unseeded'])
def test_later_barrier_cannot_hide_earlier_progress_debt(root, monkeypatch, debt):
    paragraph = '### Cumulative barrier paragraph'
    # Model retained migration state while no coordinator is running.
    with closing(connect(root)) as connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('DELETE FROM sync_state WHERE key=?', (progress.SEEDED_KEY,))
        if debt != 'unseeded':
            connection.execute('INSERT INTO sync_state VALUES(?,?)', (progress.SEEDED_KEY, '{"seq":0}'))
        if debt == 'seed_row':
            connection.execute('INSERT INTO sync_state VALUES(?,?)',
                               (progress_pending_key(0, 0), json.dumps({'ts': '', 'text': paragraph})))
        if debt == 'legacy_list':
            connection.execute('INSERT OR REPLACE INTO sync_state VALUES(?,?)',
                               (PROGRESS_LEGACY_KEY, json.dumps([{'ts': '', 'text': paragraph}])))
        connection.commit()
    take = progress._Writer._take
    monkeypatch.setattr(progress._Writer, '_take', lambda self: 'busy')
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        if debt == 'completion':
            for index, (operation, arguments) in enumerate((
                ('task.update', {'id': 'test-epic-001', 'field': 'lane', 'value': 'express'}),
                ('task.gate', {'id': 'test-epic-001', 'gate': 'review-gate', 'verdict': 'pass'}),
                ('task.pick', {'id': 'test-epic-001', 'session': 'service-tests'}),
                ('task.complete', {'id': 'test-epic-001', 'changelog': paragraph}),
            )):
                envelope = request(client, key=f'progress-prepare-{index}')
                envelope.update(operation=operation, arguments=arguments)
                client.execute(envelope)
        later = client.execute(request(client, key='later-unrelated-write'))['receipt']['commit_seq']
        # First settle ordinary entity/derived projection debt. This second
        # barrier must still observe PROGRESS even when no other file is owed.
        client.flush(later)
        barrier = owner.flush(later, timeout=0)
        assert barrier['state'] == 'pending'
        assert any('local/PROGRESS.md' in notice for notice in barrier['notices'])
        monkeypatch.setattr(progress._Writer, '_take', take)
        assert client.flush(later)['state'] == 'exported'
        with closing(connect(root, readonly=True)) as connection:
            assert not progress._owed(connection)
        if debt != 'unseeded':
            assert paragraph in (root / '.taskmaster/local/PROGRESS.md').read_text(encoding='utf-8')
