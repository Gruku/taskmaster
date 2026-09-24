"""N13 imports under publication ownership, then captures one finite writer target."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import threading
import time

import pytest

from taskmaster.coordinator.client import Client
from taskmaster.coordinator.protocol import connect
from taskmaster.coordinator.service import Coordinator
from taskmaster.native.queries import Repository
from test_native_service import root, request  # noqa: F401

REL = 'tasks/test-epic-001.md'


def title(root):
    with closing(connect(root, readonly=True)) as connection, Repository(connection).snapshot() as snapshot:
        return snapshot.get('task', 'test-epic-001')['fields']['title']


def edit(root, value):
    path = root / '.taskmaster' / REL
    text = path.read_text(encoding='utf-8').replace('Service task', value)
    path.write_text(text, encoding='utf-8')
    return path.read_bytes()


def test_explicit_sync_imports_and_exports_through_post_import_target(root):
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        assert client.sync(files=[REL])['state'] == 'synchronized'
        edit(root, 'Authored via file')
        result = client.sync(files=[REL])
        assert result['state'] == 'synchronized', result
        assert title(root) == 'Authored via file'
        assert result['imports'][0]['state'] == 'accepted'
        assert result['through'] >= result['imports'][0]['commit_seq']
        receipt = client.receipt(result["receipt_scope"], result['imports'][0]['request_id'])
        assert receipt['state'] == 'committed'
        assert not owner.active_syncs


# The checkpoint stands in for an external editor, but it runs on the coordinator's
# stack, so the bypass guard would attribute its write to service.py.
@pytest.mark.allow_projection_bypass
def test_file_changed_after_parse_is_not_imported(root):
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        original = edit(root, 'First edit')
        newer = original.replace(b'First edit', b'Later edit')
        path = root / '.taskmaster' / REL
        owner.checkpoint = lambda stage: path.write_bytes(newer) if stage == 'sync_prepared' else None
        result = client.sync(files=[REL])
        assert result['state'] == 'pending' and REL in result['unresolved']
        assert title(root) == 'Service task'
        assert path.read_bytes() == newer


@pytest.mark.allow_projection_bypass
def test_file_changed_after_commit_survives_export_and_reports_pending(root):
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        original = edit(root, 'Imported edit')
        newer = original.replace(b'Imported edit', b'Newer external edit')
        path = root / '.taskmaster' / REL
        owner.checkpoint = lambda stage: path.write_bytes(newer) if stage == 'sync_import_committed' else None
        result = client.sync(files=[REL])
        assert result['state'] == 'pending' and REL in result['unresolved']
        assert title(root) == 'Imported edit'
        assert path.read_bytes() == newer


def test_database_changed_after_parse_rejects_stale_import(root):
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        external = edit(root, 'External')
        changed = []
        def checkpoint(stage):
            if stage == 'sync_prepared' and not changed:
                changed.append(True)
                owner.submit(request(client, 'interleaved', 'Concurrent database')).result(timeout=10)
        owner.checkpoint = checkpoint
        result = client.sync(files=[REL])
        assert result['state'] == 'pending' and REL in result['unresolved']
        assert title(root) == 'Concurrent database'
        assert (root / '.taskmaster' / REL).read_bytes() == external


def test_generation_pause_is_finite_and_later_admitted_writer_cannot_interleave(root):
    pinned, resume = threading.Event(), threading.Event()
    seen = {}
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        def checkpoint(stage):
            if stage == 'sync_pinned':
                pinned.set()
                assert resume.wait(10)
            elif stage == 'sync_published':
                seen['file'] = (root / '.taskmaster' / REL).read_text(encoding='utf-8')
                seen['later_done'] = later.done()
        owner.checkpoint = checkpoint
        with ThreadPoolExecutor(max_workers=2) as pool:
            barrier = pool.submit(client.sync, import_files=False)
            try:
                assert pinned.wait(10)
                later = owner.submit(request(client, 'after-pin', 'Later intent'))
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    assert not later.done(), 'a writer admitted after the pin committed inside the barrier'
                    time.sleep(0.02)
                assert not owner.idle_expired(0), 'active synchronization must hold service ownership'
            finally:
                resume.set()
            result = barrier.result(timeout=15)
            receipt = later.result(timeout=10)
        assert result['state'] == 'synchronized', result
        assert receipt['commit_seq'] > result['through']
        assert not seen['later_done'] and 'Later intent' not in seen['file'], seen
        assert owner.flush(receipt['commit_seq'])['state'] == 'exported'
        assert 'Later intent' in (root / '.taskmaster' / REL).read_text(encoding='utf-8')


def test_sync_reaches_a_finite_target_despite_continuous_unrelated_writes(root):
    stop = threading.Event()
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        # Each command holds the writer's execution lock for most of its life, as
        # a loaded store does; the gap between two commands is microseconds.
        owner.checkpoint = lambda stage: time.sleep(0.005) if stage == 'admitted' else None
        def writes(lane):
            count = 0
            while not stop.is_set():
                count += 1
                owner.submit(request(client, f'stream-{lane}-{count}', f'Stream {lane} {count}')).result(timeout=10)
            return count
        with ThreadPoolExecutor(max_workers=4) as pool:
            streams = [pool.submit(writes, lane) for lane in range(4)]
            try:
                time.sleep(0.2)
                result = owner.sync(caller_scope='stream', request_id='finite', import_files=False, timeout=5)
            finally:
                stop.set()
            counts = [stream.result(timeout=15) for stream in streams]
        assert result['state'] == 'synchronized', result
        assert result['captured'] and min(counts) > 1


def test_invalid_file_and_missing_file_have_distinct_durable_outcomes(root):
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        path = root / '.taskmaster' / REL
        path.write_bytes(b'---\nid: wrong\n---\n')
        result = client.sync(files=[REL])
        assert result['state'] == 'pending' and result['imports'][0]['state'] == 'quarantined'
        path.unlink()
        result = client.sync(files=[REL])
        assert result['state'] == 'synchronized', result
        assert title(root) == 'Service task' and path.exists()


def test_completed_sync_replays_its_original_result_without_importing_later_edits(root):
    with Coordinator(root):
        client = Client(root, autostart=False)
        result = client.sync(files=[REL], caller_scope='retry-test', request_id='fixed')
        assert result['state'] == 'synchronized', result
        assert client.sync_status('retry-test', 'fixed')['result'] == result
        edited = edit(root, 'A later authored edit')
        assert client.sync(files=[REL], caller_scope='retry-test', request_id='fixed') == result
        assert title(root) == 'Service task'
        assert (root / '.taskmaster' / REL).read_bytes() == edited
        newer = client.sync(files=[REL])
        assert newer['through'] > result['through']
        assert title(root) == 'A later authored edit'
        with pytest.raises(ValueError, match='different|payload'):
            client.sync(import_files=False, caller_scope='retry-test', request_id='fixed')


def test_lost_success_response_recovers_completed_sync_from_same_operation_id(root, monkeypatch):
    from taskmaster.coordinator import service
    from taskmaster.coordinator.protocol import encode
    original = service._Handler.answer
    lost = []
    def answer(handler, status, value):
        if status == 200 and value.get('result', {}).get('state') == 'synchronized' and not lost:
            lost.append(True)
            raw = encode(value)
            handler.send_response(200)
            handler.send_header('Content-Length', str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw[:20])
            handler.wfile.flush()
            handler.close_connection = True
            return
        return original(handler, status, value)
    monkeypatch.setattr(service._Handler, 'answer', answer)
    with Coordinator(root):
        client = Client(root, autostart=False)
        result = client.sync(files=[REL], caller_scope='lost-response', request_id='same-id')
        assert result['state'] == 'synchronized' and lost
        assert client.sync_status('lost-response', 'same-id')['result'] == result


def test_sync_refuses_linked_projection_without_touching_its_target(root, tmp_path):
    path = root / '.taskmaster' / REL
    outside = tmp_path / 'outside.md'
    outside.write_bytes(b'private target must stay unchanged')
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip('test account cannot create symlinks')
    with Coordinator(root):
        client = Client(root, autostart=False)
        result = client.sync(files=[REL])
        assert result['state'] == 'pending' and REL in result['unresolved']
        assert outside.read_bytes() == b'private target must stay unchanged'
        assert path.is_symlink()


def junction(target, link):
    _winapi = pytest.importorskip('_winapi')
    target.mkdir(parents=True, exist_ok=True)
    _winapi.CreateJunction(str(target), str(link))


def test_junctioned_sample_directory_does_not_block_unrelated_publication(root, tmp_path):
    junction(tmp_path / 'outside-bugs', root / '.taskmaster' / 'bugs')
    with Coordinator(root) as owner:
        client = Client(root, autostart=False, visibility='legacy')
        outcome = client.execute(request(client, 'beside-junction', 'Published beside junction'))
        assert outcome['projection']['state'] == 'exported', outcome
        assert 'Published beside junction' in (root / '.taskmaster' / REL).read_text(encoding='utf-8')
        assert owner.last_export_error is None
    assert not any((tmp_path / 'outside-bugs').iterdir())


def test_junctioned_job_file_is_a_per_job_refusal_not_a_drain_failure(root, tmp_path):
    tasks = root / '.taskmaster' / 'tasks'
    outside = tmp_path / 'outside-tasks'
    outside.mkdir()
    for path in tasks.iterdir():
        (outside / path.name).write_bytes(path.read_bytes())
        path.unlink()
    tasks.rmdir()
    junction(outside, tasks)
    before = (outside / 'test-epic-001.md').read_bytes()
    with Coordinator(root) as owner:
        client = Client(root, autostart=False, visibility='legacy')
        outcome = client.execute(request(client, 'into-junction', 'Refused target'))
        assert outcome['receipt']['commit_seq']
        assert outcome['projection']['state'] == 'pending'
        assert any(REL in notice and 'refused' in notice for notice in outcome['projection']['notices']), outcome
        assert (outside / 'test-epic-001.md').read_bytes() == before


def test_admission_gate_holds_queued_commands_while_paused(root):
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        owner.pause_writer()
        try:
            queued = owner.submit(request(client, 'gated', 'Gated intent'))
            deadline = time.monotonic() + 0.3
            while time.monotonic() < deadline:
                assert not queued.done(), 'the writer admitted a command through a pause'
                time.sleep(0.02)
        finally:
            owner.resume_writer()
        assert queued.result(timeout=10)['commit_seq']


def test_writer_busy_timeout_reports_pending_and_releases_the_pause(root):
    admitted, release = threading.Event(), threading.Event()
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        def checkpoint(stage):
            if stage == 'admitted' and slow and not admitted.is_set():
                admitted.set()
                assert release.wait(10)
        slow, pause = [], owner.pause_writer
        owner.checkpoint = checkpoint
        def pause_with_command_in_flight():
            # The command is admitted after sync.begin and before the pause.
            slow.append(owner.submit(request(client, 'slow', 'Slow command')))
            assert admitted.wait(10)
            pause()
        owner.pause_writer = pause_with_command_in_flight
        try:
            result = owner.sync(caller_scope='busy', request_id='one', import_files=False, timeout=2)
        finally:
            release.set()
        assert slow, result
        slow = slow[0]
        assert result['state'] == 'pending' and any('writer busy' in n for n in result['notices']), result
        assert slow.result(timeout=10)['commit_seq']
        assert owner.submit(request(client, 'after-busy', 'After busy')).result(timeout=10)['commit_seq']
        assert owner.pauses == 0


@pytest.mark.allow_projection_bypass  # the checkpoint edits the file on the coordinator's stack
def test_external_edit_after_publication_is_reported_pending(root):
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        path = root / '.taskmaster' / REL
        def checkpoint(stage):
            if stage == 'sync_published':
                path.write_bytes(path.read_bytes().replace(b'Service task', b'Edited after flush'))
        owner.checkpoint = checkpoint
        result = client.sync(files=[REL], caller_scope='recheck', request_id='one')
        assert result['state'] == 'pending' and REL in result['unresolved'], result
        assert client.sync_status('recheck', 'one')['state'] != 'complete'
        assert b'Edited after flush' in path.read_bytes()


@pytest.mark.parametrize('failure', ['pending', 'oserror', 'runtime'])
def test_incomplete_export_never_records_a_completed_sync(root, failure):
    def exporter(connection, backlog_dir, through=None):
        if failure == 'oserror':
            raise OSError('disk refused')
        if failure == 'runtime':
            raise RuntimeError('exporter bug')
        return ['export pending: stub']
    with Coordinator(root, exporter=exporter) as owner:
        client = Client(root, autostart=False)
        owner.submit(request(client, 'owed', 'Owed export')).result(timeout=10)
        result = owner.sync(caller_scope='stub', request_id=failure, import_files=False, timeout=1)
        assert result['state'] == 'pending' and result['notices'], result
        assert client.sync_status('stub', failure)['state'] != 'complete'


def test_flush_target_beyond_committed_sequence_is_refused_before_any_import(root):
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        edit(root, 'Must not import')
        with pytest.raises(ValueError, match='exceeds committed'):
            owner.sync(caller_scope='beyond', request_id='one', files=[REL], through=10**9)
        assert title(root) == 'Service task'


def test_begin_outcome_uncertain_is_pending(root):
    from taskmaster.native_routing.projection import drain
    admitted, release, settled = threading.Event(), threading.Event(), threading.Event()
    passes = []

    def exporter(connection, backlog_dir, through=None):
        notices = drain(connection, backlog_dir, session='begin-uncertain', through=through, progress_wait=False)
        passes.append(notices)
        settled.set()
        return notices

    with Coordinator(root, exporter=exporter) as owner:
        client = Client(root, autostart=False)
        # The startup export pass holds publication and claims jobs in a write
        # transaction; overlapping the blocked writer it would stall there and
        # sync would stop at "publisher busy" before ever submitting begin.
        assert settled.wait(30) and passes == [[]], passes
        with owner.publication:
            pass
        def checkpoint(stage):
            if stage == 'admitted' and not admitted.is_set():
                admitted.set()
                assert release.wait(10)
        owner.checkpoint = checkpoint
        blocker = owner.submit(request(client, 'blocker', 'Blocking command'))
        assert admitted.wait(10)
        try:
            result = owner.sync(caller_scope='uncertain', request_id='begin', import_files=False, timeout=2)
        finally:
            release.set()
        assert result['state'] == 'pending' and any('begin outcome uncertain' in n for n in result['notices']), result
        blocker.result(timeout=10)


def test_import_outcome_uncertain_is_pending_and_names_the_receipt(root):
    release = threading.Event()
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        edit(root, 'Slow import')
        def checkpoint(stage):
            if stage == 'sync_prepared':
                owner.checkpoint = lambda inner: release.wait(10) if inner == 'admitted' else None
        owner.checkpoint = checkpoint
        try:
            result = owner.sync(caller_scope='uncertain', request_id='import', files=[REL], timeout=1.5)
        finally:
            release.set()
        assert result['state'] == 'pending' and REL in result['unresolved'], result
        assert result['imports'][0]['state'] == 'uncertain' and result['imports'][0]['may_have_committed']


def test_completion_receipt_gets_its_own_budget_after_a_slow_publication(root):
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        owner.checkpoint = lambda stage: time.sleep(1.2) if stage == 'sync_published' else None
        result = owner.sync(caller_scope='budget', request_id='one', import_files=False, timeout=1)
        assert result['state'] == 'synchronized', result
        assert client.sync_status('budget', 'one')['result'] == result


@pytest.mark.parametrize('failure', ['timeout', 'rejected'])
def test_completion_receipt_failure_is_pending(root, monkeypatch, failure):
    from concurrent.futures import Future
    from taskmaster.coordinator import sync_worker
    monkeypatch.setattr(sync_worker, 'FINISH_TIMEOUT', 0.3)
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        original = owner.submit
        def submit(envelope):
            if envelope['operation'] != 'sync.finish':
                return original(envelope)
            if failure == 'rejected':
                raise ValueError('encoded request exceeds limit')
            return Future()
        owner.submit = submit
        result = owner.sync(caller_scope='finish', request_id=failure, import_files=False, timeout=5)
        assert result['state'] == 'pending' and result['notices'], result
        assert client.sync_status('finish', failure)['state'] != 'complete'


# The byte budget is read at call time, so the bounding tests shrink it instead of
# building 80 MB payloads; the real budget's fit inside one message is checked apart.
SMALL_SUMMARY_BYTES = 16 * 1024


def test_the_summary_budget_fits_well_inside_one_protocol_message():
    from taskmaster.coordinator import sync_worker
    from taskmaster.coordinator.protocol import MAX_MESSAGE_BYTES
    assert sync_worker.SUMMARY_BYTES < MAX_MESSAGE_BYTES // 2


def test_completed_result_is_bounded_and_counts_omitted_receipts(monkeypatch):
    from taskmaster.coordinator import sync_worker
    from taskmaster.native.migrate import encode
    monkeypatch.setattr(sync_worker, 'SUMMARY_BYTES', SMALL_SUMMARY_BYTES)
    result = dict(state='synchronized', through=5, captured=True, observed=0, unresolved=[], notices=[],
                  warnings=[f'duplicate import path skipped: tasks/x-{n}.md' for n in range(200)],
                  caller_scope='c', request_id='r', receipt_scope='sync-' + 'a' * 64, import_files=True,
                  imports=[dict(file=f'tasks/t-{n:05}.md', state='accepted', reason='r' * 512, commit_seq=n,
                                caller_scope='sync-' + 'a' * 64, request_id='b' * 64) for n in range(400)])
    assert len(encode({'result': result}).encode()) > 8 * SMALL_SUMMARY_BYTES
    summary = sync_worker.summarize(result)
    assert len(encode({'result': summary}).encode()) <= SMALL_SUMMARY_BYTES
    assert summary['state'] == 'synchronized' and summary['through'] == 5
    assert summary['imports'] == [{'file': item['file'], 'state': 'accepted', 'commit_seq': item['commit_seq'],
                                   'request_id': item['request_id']} for item in result['imports']][:len(summary['imports'])]
    assert summary['imports'] and summary['imports_omitted'] == 400 - len(summary['imports'])
    assert summary['warnings_omitted'] == 200 - len(summary['warnings'])


def test_pending_result_returned_to_the_caller_is_bounded_and_counts_omissions(monkeypatch):
    from taskmaster.coordinator import sync_worker
    from taskmaster.native.migrate import encode
    monkeypatch.setattr(sync_worker, 'SUMMARY_BYTES', SMALL_SUMMARY_BYTES)
    files = [f'tasks/t-{n:05}.md' for n in range(400)]
    result = dict(state='pending', through=5, captured=False, observed=0, unresolved=list(files),
                  notices=[f'sync pending: {rel}: ' + 'why ' * 50 for rel in files],
                  warnings=[f'duplicate import path skipped: {rel}' for rel in files],
                  caller_scope='c', request_id='r', receipt_scope='sync-' + 'a' * 64, import_files=True,
                  imports=[dict(file=rel, state='conflict', reason='r' * 512, commit_seq=n,
                                caller_scope='sync-' + 'a' * 64, request_id='b' * 64) for n, rel in enumerate(files)])
    assert len(encode({'result': result}).encode()) > 8 * SMALL_SUMMARY_BYTES
    bounded = sync_worker.bound(result)
    assert len(encode({'result': bounded}).encode()) <= SMALL_SUMMARY_BYTES
    assert bounded['state'] == 'pending' and bounded['receipt_scope'] == result['receipt_scope']
    for key in ('unresolved', 'notices', 'imports', 'warnings'):
        assert bounded[key] == result[key][:len(bounded[key])]
        assert len(bounded[key]) + bounded.get(key + '_omitted', 0) == len(result[key])
    # The why survives first: every unresolved path, and notices before receipts.
    assert bounded['unresolved'] == files and bounded['notices']
    assert bounded['imports_omitted'] > 0


def test_synchronize_bounds_a_pending_result(monkeypatch):
    from taskmaster.coordinator import sync_worker
    big = dict(state='pending', unresolved=[], notices=['n' * 4096] * 1000, imports=[], warnings=[])
    monkeypatch.setattr(sync_worker, '_synchronize', lambda owner, **arguments: big)
    bounded = sync_worker.synchronize(None, caller_scope='c', request_id='r')
    assert bounded['notices_omitted'] > 0


@pytest.mark.parametrize('stubs', [
    [{'id': 'test-epic-001', 'title': 'Stub'}, {'id': 'test-epic-099', 'title': 'Invented'}],
    [{'id': 'test-epic-099', 'title': 'Invented'}]], ids=['divergent', 'new-only'])
def test_ordinary_sync_never_imports_task_rows_from_backlog_yaml(root, stubs):
    import yaml
    path = root / '.taskmaster' / 'backlog.yaml'
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=['backlog.yaml'])
        with closing(connect(root, readonly=True)) as connection, Repository(connection).snapshot() as snapshot:
            before = snapshot.get('task', 'test-epic-001', include_body=True)
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
        data['epics'][0]['tasks'] = stubs
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')
        result = client.sync(files=['backlog.yaml'])
        assert result['state'] == 'pending' and 'backlog.yaml' in result['unresolved'], result
        assert any('task' in notice for notice in result['notices']), result
        with closing(connect(root, readonly=True)) as connection, Repository(connection).snapshot() as snapshot:
            assert snapshot.get('task', 'test-epic-001', include_body=True) == before
            with pytest.raises(KeyError):
                snapshot.get('task', 'test-epic-099', include_deleted=True)


def test_sync_apply_refuses_task_rows_from_a_backlog_import():
    from taskmaster.native import sync
    arguments = {'file': 'backlog.yaml', 'mode': 'apply', 'reason': 'r', 'observed_base64': 'eA==',
                 'observed_hash': '11f6ad8ec52a2984abaafd7c3b516503785c2072', 'expected_manifest': '0' * 64,
                 'rows': [{'kind': 'task', 'id': 'test-epic-001', 'revision': 1,
                           'fields': {'id': 'test-epic-001'}, 'body': None}]}
    with pytest.raises(ValueError, match='task'):
        sync.validate('sync.apply', arguments)
