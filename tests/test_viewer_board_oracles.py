"""N10 independent invariants, including resync and moving wall-clock state."""
import json
from pathlib import Path
import re
import random

import pytest

from taskmaster import backlog_server as bs, viewer_board, viewer_dto
from native_twins import make_twins
from test_native_routing_viewer import _seed


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def read(twins, root, **options):
    with twins.at(root):
        from taskmaster.native_routing.viewer import database
        return viewer_board.response(database(), **options)


def apply(before, delta):
    if "tasks" in delta:
        return {k: delta[k] for k in ("tasks", "epics", "phases")}
    rows = {t["id"]: t for t in before["tasks"]}
    rows.update({t["id"]: t for t in delta["tasks_upsert"]})
    for ident in delta["tasks_remove"]:
        rows.pop(ident, None)
    epics = delta.get("epics", before["epics"])
    phases = delta.get("phases", before["phases"])
    ranks = {e["id"]: i for i, e in enumerate(epics)}
    tasks = sorted(rows.values(), key=lambda t: (ranks[t["epic"]], float(t.get("order") or 0), t["id"]))
    return dict(tasks=tasks, epics=epics, phases=phases)


def test_declared_fields_match_the_client():
    source = (Path(__file__).parents[1] / "viewer/js/lib/board-fields.js").read_text(encoding="utf-8")
    for name in ("BOARD_TASK_FIELDS", "BOARD_EPIC_FIELDS", "BOARD_PHASE_FIELDS"):
        literal = re.search(rf"{name}\s*=\s*\[([^]]+)\]", source).group(1)
        assert re.findall(r"'([^']+)'", literal) == list(getattr(viewer_dto, name))


def test_golden_board_b1():
    fixture = json.loads((Path(__file__).parent / 'fixtures/board_dto_b1.json').read_text())
    source = fixture['input']
    assert viewer_dto.build_board(source['tasks'], source['epics'], source['phases']) == fixture['expected']


@pytest.mark.parametrize('authority', ['legacy', 'native'])
def test_unrelated_entity_writers_preserve_revision_and_bytes(twins, authority):
    root = getattr(twins, authority)
    before = read(twins, root)[1]
    for name, arguments in (
        ('backlog_bug_create', {'title': 'unrelated bug'}),
        ('backlog_issue_create', {'title': 'unrelated issue', 'severity': 'P2', 'evidence': 'fixture'}),
        ('backlog_idea_create', {'title': 'unrelated idea'}),
        ('backlog_note', {'action': 'create', 'text': 'unrelated note'}),
        ('backlog_decision_create', {'title': 'unrelated decision', 'options': ['a', 'b']}),
        ('backlog_handover_create', {'tldr': 'unrelated handover', 'thread': 'unrelated'}),
        ('backlog_area_create', {'area_id': 'unrelated', 'name': 'Unrelated'}),
        ('backlog_project_init', {'name': 'Unrelated project'}),
    ):
        with twins.at(root):
            result = getattr(bs, name)(**arguments)
            assert not str(result).startswith('Error'), result
        after = read(twins, root)[1]
        assert json.dumps(after) == json.dumps(before), name
        assert read(twins, root, since=before['cursor'])[0] == 304, name


@pytest.mark.parametrize("authority", ["legacy", "native"])
def test_delta_equals_fresh_snapshot_over_edits(twins, authority):
    root = getattr(twins, authority)
    before = read(twins, root)[1]
    for i in range(12):
        with twins.at(root):
            ident = f"test-epic-{i % 2 + 1:03d}"
            for field, value in [('priority', ('low', 'high', 'medium')[i % 3]), ('branch', f'branch-{i % 5}')]:
                result = bs.backlog_update_task(task_id=ident, field=field, value=value)
                assert not str(result).startswith('Error'), result
        status, delta, _, _ = read(twins, root, since=before["cursor"])
        fresh = read(twins, root)[1]
        if status == 304:
            assert fresh == before
        else:
            assert apply(before, delta) == {k: fresh[k] for k in ("tasks", "epics", "phases")}
        before = fresh


@pytest.mark.parametrize('authority', ['legacy', 'native'])
@pytest.mark.parametrize('seed', [17, 981])
def test_seeded_random_sequences_preserve_304_and_delta_soundness(twins, authority, seed):
    rng = random.Random(seed)
    root = getattr(twins, authority)
    before = read(twins, root)[1]
    seen = {before['revision']: json.dumps(before)}
    for step in range(24):
        with twins.at(root):
            for _ in range(rng.randint(1, 3)):
                field, values = rng.choice([
                    ('priority', ['high', 'medium', 'low']),
                    ('branch', ['branch-a', 'branch-b', 'branch-c']),
                    ('title', ['Random a', 'Random b', 'Random c']),
                    ('notes', ['body a', 'body b']),
                ])
                result = bs.backlog_update_task(task_id=f'test-epic-{rng.randint(1, 2):03d}',
                                                field=field, value=rng.choice(values))
                assert not str(result).startswith('Error'), result
        fresh = read(twins, root)[1]
        encoded = json.dumps(fresh)
        assert seen.setdefault(fresh['revision'], encoded) == encoded
        status, delta, _, _ = read(twins, root, since=before['cursor'])
        if status == 304:
            assert fresh == before
        else:
            assert apply(before, delta) == {k: fresh[k] for k in ('tasks', 'epics', 'phases')}, (seed, step)
        before = fresh


@pytest.mark.parametrize("authority", ["legacy", "native"])
def test_resync_boundaries_and_version(twins, authority, monkeypatch):
    root = getattr(twins, authority)
    before = read(twins, root)[1]
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-001", field="title", value="changed")
    monkeypatch.setattr(viewer_board, "DELTA_MAX", 0)
    assert read(twins, root, since=before["cursor"])[1]["resync"] == "too_many"
    token = before["cursor"].split(":")
    token[1] = "replacement-store"
    assert read(twins, root, since=":".join(token))[1]["resync"] == "store_changed"
    monkeypatch.setattr(bs, "VERSION", "next")
    assert read(twins, root, since=before["cursor"])[1]["resync"] == "version_changed"


@pytest.mark.parametrize("authority", ["legacy", "native"])
def test_board_bytes_do_not_depend_on_session(twins, authority, monkeypatch):
    root = getattr(twins, authority)
    before = read(twins, root)[1]
    monkeypatch.setattr(bs, "SESSION_ID", "another-reader")
    from datetime import timedelta
    from native_twins import CLOCK
    CLOCK['at'] += timedelta(days=365)
    after = read(twins, root)[1]
    assert json.dumps(before) == json.dumps(after)


@pytest.mark.parametrize('authority', ['legacy', 'native'])
def test_phase_epic_and_archive_deltas_match_full(twins, authority):
    root = getattr(twins, authority)
    before = read(twins, root)[1]
    for name, arguments in (
        ('backlog_add_phase', {'phase_id': 'next', 'name': 'Next'}),
        ('backlog_update_phase', {'phase_id': 'next', 'field': 'order', 'value': '0'}),
        ('backlog_add_epic', {'epic_id': 'third', 'name': 'Third', 'done_when': 'done'}),
        ('backlog_archive_task', {'task_id': 'test-epic-001', 'reason': 'duplicate'}),
    ):
        with twins.at(root):
            result = getattr(bs, name)(**arguments)
            assert not str(result).startswith('Error'), result
        status, delta, _, _ = read(twins, root, since=before['cursor'])
        assert status == 200
        fresh = read(twins, root)[1]
        assert apply(before, delta) == {k: fresh[k] for k in ('tasks', 'epics', 'phases')}
        before = fresh
    archived = next(t for t in before['tasks'] if t['id'] == 'test-epic-001')
    assert archived['status'] == 'archived'
    # The historical UI consumes archived_reason, while writers produce
    # archive_reason. Preserve that existing wire behavior; report the defect.
    assert 'archived_reason' not in archived


def test_native_batch_is_invisible_until_the_whole_commit_is_visible(twins):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import closing
    import sqlite3
    import threading
    from taskmaster.native.commands import execute
    from taskmaster.native_routing.viewer import database
    root = twins.native
    before = read(twins, root)[1]
    entered, release = threading.Event(), threading.Event()
    with twins.at(root):
        path = database()
        envelope = {'protocol': 2, 'store_id': before['revision'].split(':')[1],
                    'caller_scope': 'n10-oracle', 'request_id': 'atomic-pair',
                    'operation': 'batch', 'arguments': {'commands': [
                        {'operation': 'task.patch', 'arguments': {'id': f'test-epic-{i:03d}', 'set': {'title': f'Batch {i}'}}}
                        for i in (1, 2)]}, 'expected_revisions': []}
        def checkpoint(stage):
            if stage == 'mutated':
                entered.set()
                assert release.wait(15), 'reader did not release writer'
        def write():
            with closing(sqlite3.connect(path, isolation_level=None, timeout=10)) as conn:
                return execute(conn, envelope, checkpoint=checkpoint)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(write)
            try:
                assert entered.wait(15)
                assert viewer_board.response(path, since=before['cursor'])[0] == 304
            finally:
                release.set()
            future.result(timeout=15)
        delta = viewer_board.response(path, since=before['cursor'])[1]
        fresh = viewer_board.response(path)[1]
        assert len(delta['tasks_upsert']) == 2
        assert apply(before, delta) == {k: fresh[k] for k in ('tasks', 'epics', 'phases')}
