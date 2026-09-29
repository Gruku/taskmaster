"""User intent: a full sync over more than sync.MAX_FILES projection files must complete by
scanning in bounded batches (N16 10x dataset, ~37,000 files) with exactly the outcomes an
unbatched sync gives: imports, deletions, conflicts, holds, Git barrier and retry contract.
"""
from contextlib import closing
import shutil
import time

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import sync_files, sync_worker
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.protocol import connect
from taskmaster.coordinator.service import Coordinator
from taskmaster.native import sync
from taskmaster.native.queries import Repository
from native_git_helpers import commit_count, git, init_repo
from native_twins import make_twins

pytestmark = pytest.mark.xdist_group('heavy_processes')
TASKS = 24
BATCH = 5


@pytest.fixture
def repo(tmp_path, monkeypatch):
    def seed():
        for index in range(TASKS):
            bs.backlog_add_task(title=f'Task {index + 1}', epic='test-epic', phase='dev')
    twins = make_twins(tmp_path, monkeypatch, seed, visibility=None)
    with twins.at(twins.native):
        init_repo(twins.native)
        yield twins.native


def rel(n):
    return f'tasks/test-epic-{n:03d}.md'


def title(root, n):
    with closing(connect(root, readonly=True)) as connection, Repository(connection).snapshot() as snapshot:
        return snapshot.get('task', f'test-epic-{n:03d}')['fields']['title']


def edit(root, n, value):
    path = root / '.taskmaster' / rel(n)
    path.write_bytes(path.read_bytes().replace(f'title: Task {n}\r'.encode(), f'title: {value}\r'.encode()))
    assert value.encode() in path.read_bytes()


def sync_applies(root, file):
    with closing(connect(root, readonly=True)) as connection:
        return connection.execute("SELECT COUNT(*) FROM domain_events WHERE op='sync.apply' "
                                  "AND json_extract(after,'$.file')=?", (file,)).fetchone()[0]


def order(root):
    """The full sync's selected order (discovery order); recorded-only paths follow."""
    return list(sync_files.discover(root / '.taskmaster').files)


def settle(client):
    result = client.sync()
    assert result['state'] == 'synchronized', result
    return result


@pytest.fixture
def small_batches(monkeypatch):
    monkeypatch.setattr(sync, 'MAX_FILES', BATCH)
    seen = []
    original = sync_worker._unchanged_rule

    def spy(owner, linked, rels=None):
        seen.append(None if rels is None else len(rels))
        return original(owner, linked, rels)
    monkeypatch.setattr(sync_worker, '_unchanged_rule', spy)
    return seen


def test_sync_over_the_limit_imports_edits_in_every_batch(repo, small_batches):
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        settle(client)
        files = order(repo)
        assert len(files) > 4 * BATCH
        edited = [1, 9, 17, 24]  # batches 1, 3, 4 and the last
        for n in edited:
            edit(repo, n, f'Batched edit {n}')
        small_batches.clear()
        result = client.sync()
        assert result['state'] == 'synchronized', result
        assert sorted(item['file'] for item in result['imports']) == sorted(rel(n) for n in edited)
        assert all(item['state'] == 'accepted' for item in result['imports'])
        assert [title(repo, n) for n in edited] == [f'Batched edit {n}' for n in edited]
        # Every batch was loaded on its own, never more than BATCH rows at once.
        assert len(small_batches) >= 5 and all(size is not None and size <= BATCH for size in small_batches)


def test_deletion_in_a_later_batch_is_detected_and_repaired(repo, small_batches):
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        settle(client)
        target = repo / '.taskmaster' / rel(TASKS)
        target.unlink()
        assert rel(TASKS) not in order(repo)  # a recorded path, selected after every discovered file
        result = client.sync()
        assert result['state'] == 'synchronized', result
        assert [(item['file'], item['state']) for item in result['imports']] == [(rel(TASKS), 'repair_pending')]
        assert target.exists() and title(repo, TASKS) == f'Task {TASKS}'


def test_conflict_in_a_later_batch_is_pending_and_other_batches_import(repo, small_batches):
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        settle(client)
        edit(repo, 3, 'File side of batch one')
        edit(repo, 19, 'File side of batch four')
        # The store changes the same field the file changed: an overlapping edit.
        envelope = {'protocol': 2, 'store_id': owner.identity['store_id'], 'caller_scope': 'batched-tests',
                    'request_id': 'overlap', 'operation': 'task.patch',
                    'arguments': {'id': 'test-epic-019', 'set': {'title': 'Store side'}}, 'expected_revisions': []}
        client.execute(envelope)
        result = client.sync()
        assert result['state'] == 'pending', result
        states = {item['file']: item['state'] for item in result['imports']}
        assert states == {rel(3): 'accepted', rel(19): 'conflict'}, result
        assert rel(19) in result['unresolved']
        assert title(repo, 3) == 'File side of batch one' and title(repo, 19) == 'Store side'


def test_crash_mid_batch_then_retry_under_the_same_id(repo, small_batches):
    edited = [2, 8, 13, 22]
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        settle(client)
        for n in edited:
            edit(repo, n, f'Crash edit {n}')
        committed = []

        def crash(stage):
            if stage == 'sync_import_committed':
                committed.append(stage)
                if len(committed) == 2:
                    raise RuntimeError('simulated crash in batch two')
        owner.checkpoint = crash
        with pytest.raises(RuntimeError, match='simulated crash'):
            owner.sync(caller_scope='batched', request_id='crash-retry', import_files=True, through=0,
                       files=None, take_file=False)
        status = client.sync_status('batched', 'crash-retry')
        assert (status.get('result') or {}).get('state') != 'synchronized', status
        assert title(repo, 13) == 'Task 13' and title(repo, 22) == 'Task 22'
        owner.checkpoint = lambda stage: None
        result = client.sync(caller_scope='batched', request_id='crash-retry')
        assert result['state'] == 'synchronized', result
        assert [title(repo, n) for n in edited] == [f'Crash edit {n}' for n in edited]
        # Nothing was imported twice: the retry imported only what the crash left.
        assert sorted(item['file'] for item in result['imports']) == [rel(13), rel(22)]
        assert [sync_applies(repo, rel(n)) for n in edited] == [1, 1, 1, 1]
        assert client.sync(caller_scope='batched', request_id='crash-retry') == result


def test_git_barrier_refuses_while_a_batch_is_unscanned(repo, small_batches):
    with Coordinator(repo) as owner:
        client = Client(repo, autostart=False, timeout=120)
        settle(client)
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '--allow-empty', '-m', 'published generation')
        before = commit_count(repo)
        edit(repo, 23, 'Edit the budget never reaches')
        batches = []

        def slow_after_first_batch(stage):
            if stage == 'sync_batch_scanned':
                batches.append(stage)
                if len(batches) == 1:
                    time.sleep(1.2)
        owner.checkpoint = slow_after_first_batch
        result = client.git_run(kind='commit', message='must not commit', sync_timeout=1)
        assert result['state'] == 'refused', result
        assert result['sync']['state'] == 'pending' and not result['sync']['captured']
        assert any('time budget' in notice for notice in result['sync']['notices']), result
        assert commit_count(repo) == before and title(repo, 23) == 'Task 23'
        owner.checkpoint = lambda stage: None
        assert client.sync()['state'] == 'synchronized'
        assert title(repo, 23) == 'Edit the budget never reaches'


def test_git_barrier_refuses_while_a_later_batch_is_held(repo, small_batches):
    with Coordinator(repo):
        client = Client(repo, autostart=False, timeout=120)
        settle(client)
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '--allow-empty', '-m', 'published generation')
        before = commit_count(repo)
        (repo / '.taskmaster' / rel(22)).write_bytes(b'---\nid: wrong\n---\n')
        result = client.git_run(kind='commit', message='must not commit')
        assert result['state'] == 'refused', result
        assert rel(22) in result['sync']['unresolved']
        assert commit_count(repo) == before
        # Still held on the next sync: the quarantine is a whole-set hold, not a batch's.
        again = client.sync()
        assert again['state'] == 'pending' and rel(22) in again['unresolved']


def _scenario(root, client, owner):
    """Edits in several batches, a deletion, an invalid file and an overlapping edit."""
    settle(client)
    edit(root, 1, 'Equivalent one')
    edit(root, 12, 'Equivalent twelve')
    edit(root, 19, 'File side')
    (root / '.taskmaster' / rel(24)).unlink()
    (root / '.taskmaster' / rel(7)).write_bytes(b'---\nid: wrong\n---\n')
    client.execute({'protocol': 2, 'store_id': owner.identity['store_id'], 'caller_scope': 'batched-tests',
                    'request_id': 'overlap', 'operation': 'task.patch',
                    'arguments': {'id': 'test-epic-019', 'set': {'title': 'Store side'}}, 'expected_revisions': []})
    first = client.sync(caller_scope='equivalence', request_id='one')
    second = client.sync(caller_scope='equivalence', request_id='two')
    with closing(connect(root, readonly=True)) as connection:
        rows = connection.execute('SELECT file,content_hash,dirty,quarantined FROM projection ORDER BY file').fetchall()
    files = {path.relative_to(root / '.taskmaster').as_posix(): path.read_bytes()
             for path in sorted((root / '.taskmaster').rglob('*.md')) if 'local' not in path.parts}
    return comparable(first), comparable(second), [title(root, n) for n in range(1, TASKS + 1)], rows, files


def comparable(result):
    """A result without its import request keys. A key hashes the prepared arguments, whose
    manifest precondition includes a flagged file's `flagged_at` time, so the conflicted
    file's key differs between two copies whatever the batching."""
    return dict(result, imports=[{key: value for key, value in item.items() if key != 'request_id'}
                                 for item in result.get('imports') or []])


def test_batched_sync_equals_the_unbatched_sync(repo, tmp_path, monkeypatch):
    twin = tmp_path / 'batched-copy'
    shutil.copytree(repo, twin)
    with Coordinator(repo) as owner:
        unbatched = _scenario(repo, Client(repo, autostart=False, timeout=120), owner)
    monkeypatch.setattr(sync, 'MAX_FILES', BATCH)
    with Coordinator(twin) as owner:
        batched = _scenario(twin, Client(twin, autostart=False, timeout=120), owner)
    assert unbatched[0]['state'] == 'pending' and unbatched[0]['imports'], unbatched[0]
    for left, right in zip(unbatched, batched):
        if isinstance(left, dict):
            for a, b in zip(left.get('imports') or [], right.get('imports') or []):
                assert {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)} == {}
            assert {key: (left.get(key), right.get(key)) for key in set(left) | set(right)
                    if left.get(key) != right.get(key)} == {}
        assert left == right


@pytest.fixture
def plain(tmp_path, monkeypatch):
    """No Git: nothing but the batches themselves confirms fingerprints."""
    def seed():
        for index in range(TASKS):
            bs.backlog_add_task(title=f'Task {index + 1}', epic='test-epic', phase='dev')
    twins = make_twins(tmp_path, monkeypatch, seed, visibility=None)
    with twins.at(twins.native):
        yield twins.native


def test_an_interrupted_batched_sync_keeps_the_fingerprints_of_unscanned_batches(plain, small_batches, monkeypatch):
    from test_native_sync_perf import age_projection
    # At 37,000 files the bound's 1,024-entry slack is negligible next to a 10,000-file batch.
    monkeypatch.setattr(sync_files, 'CACHE_SLACK', 0)
    backlog = plain / '.taskmaster'
    with Coordinator(plain) as owner:
        client = Client(plain, autostart=False, timeout=120)
        settle(client)
        age_projection(plain)
        settle(client)  # records every file's fingerprint
        known = sync_files._load_cache(plain)[sync_files._cache_key(backlog)]['entries']
        assert rel(TASKS) in known and rel(1) in known
        batches = []

        def slow_after_first_batch(stage):
            if stage == 'sync_batch_scanned':
                batches.append(stage)
                if len(batches) == 1:
                    time.sleep(1.2)
        owner.checkpoint = slow_after_first_batch
        result = client.sync(timeout=1)
        assert result['state'] == 'pending' and len(batches) == 1, result
        kept = sync_files._load_cache(plain)[sync_files._cache_key(backlog)]['entries']
        # The retry resumes on stat calls for every batch, scanned or not.
        assert set(known) <= set(kept)
