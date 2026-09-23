# User intent: N14's copy-only acceptance rehearsal - prove the incrementally maintained graph
# tables, native graph verify/repair, the indexed edit-hook neighbourhood and `depth` dependencies
# at CodeMaestro's real volume, against a legacy twin. Never runs on the live checkout.
"""Copy-only N14 rehearsal (graph consumers and compatibility completion).

    python scripts/native_n14_rehearsal.py --copy <tmn14>/cm --twin <tmn14>/cml --phase setup|graph|hook|deps|query

`--copy` becomes the native store, `--twin` stays legacy; both are prepared by hand first
(read-only on the source), exactly alike:
    git clone --no-hardlinks <live> <dir>; git -C <dir> remote remove origin
    git -C <dir> config core.hooksPath <tmn14>/hooks
    copy the live working tree's .taskmaster/ (store.db + WAL, no -shm/locks) over the clone
    touch <dir>/.benchmark-copy
Phases run in the order above, one at a time. Tool calls run in-process through the public
`backlog_*` functions (the native side through an in-process coordinator, as the twins tests
do); the edit hook runs as a subprocess like the real one. Results append to
<copy>/../results.jsonl. The pre-N14 comparison code is taken from `PRE_N14` with `git show` /
`git archive` into <tmn14>, never checked out.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import random
import sqlite3
import statistics
import subprocess
import sys
import time
import uuid

WT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(WT), str(WT / 'tests')]
LIVE = Path('C:/Users/gruku/Files/Work/CodeMaestro').resolve()
PRE_N14 = '43acef1'
PYTHON = sys.executable
RESULTS = None
TABLES = ('entity_paths', 'links', 'handover_tasks', 'related')


# ── guards and plumbing ─────────────────────────────────────────────────────
def git(root, *args, check=True):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    done = subprocess.run(['git', '-C', str(root), *args], env=env, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    if check and done.returncode:
        raise AssertionError(f'git {args} failed: {done.stderr[-2000:]}')
    return done.stdout


def guard(copy: Path) -> Path:
    copy = copy.resolve()
    assert copy != LIVE and LIVE not in copy.parents and copy not in LIVE.parents, copy
    assert (copy / '.benchmark-copy').is_file(), 'not a marked rehearsal copy'
    assert git(copy, 'remote').strip() == '', 'the copy must have no remotes'
    common = Path(git(copy, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()).resolve()
    assert copy in common.parents, f'git common dir {common} is outside the copy'
    return copy


def live_fingerprint():
    """Read-only: the live checkout's HEAD and worktree list, to show nothing touched it."""
    return {'head': git(LIVE, 'rev-parse', 'HEAD').strip(),
            'worktrees': len(git(LIVE, 'worktree', 'list', '--porcelain').split('\nworktree ')),
            'branches': len(git(LIVE, 'branch', '--list').splitlines())}


def record(phase, **values):
    line = dict(phase=phase, at=time.strftime('%H:%M:%S'), **values)
    print(json.dumps(line, default=str)[:4000], flush=True)
    with RESULTS.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(line, default=str) + '\n')


def db(root):
    connection = sqlite3.connect(root / '.taskmaster/local/store.db', timeout=30)
    connection.execute('PRAGMA busy_timeout=30000')
    return closing(connection)


def is_native(root):
    with db(root) as con:
        return con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '2'


def pct(values, q):
    values = sorted(values)
    if not values:
        return None
    return round(values[min(len(values) - 1, int(round(q * (len(values) - 1))))], 4)


def stats(values):
    return {'n': len(values), 'p50': pct(values, .5), 'p95': pct(values, .95), 'max': pct(values, 1.0),
            'mean': round(statistics.mean(values), 4) if values else None}


# ── graph tables: twin comparison and the native oracle ─────────────────────
def graph_tables(root):
    from taskmaster.native.graph_repair import COLUMNS
    with db(root) as con:
        return {t: Counter(tuple(r) for r in con.execute(f"SELECT {','.join(COLUMNS[t])} FROM {t}")) for t in TABLES}


def table_diff(legacy, native, keep=5):
    out = {}
    for table in TABLES:
        a, b = legacy[table], native[table]
        only_l, only_n = a - b, b - a
        if only_l or only_n:
            out[table] = {'legacy_only': sum(only_l.values()), 'native_only': sum(only_n.values()),
                          'legacy_examples': [list(r) for r in sorted(only_l, key=repr)[:keep]],
                          'native_examples': [list(r) for r in sorted(only_n, key=repr)[:keep]]}
    return out


def _identity(con):
    return dict(con.execute("SELECT key,value FROM native_manifest WHERE key IN "
                            "('authority','store_id','event_high_water')"))


def native_verify(root):
    """The native graph oracle over a read snapshot; changes nothing."""
    from taskmaster.native import graph_repair
    from taskmaster.native.queries import Snapshot
    with db(root) as con:
        con.isolation_level = None
        con.execute('BEGIN')
        try:
            return graph_repair.verify(Snapshot(con, _identity(con)))
        finally:
            con.rollback()


def verify_brief(report):
    return {'clean': report['clean'], 'seconds': report['seconds'], 'rows_compared': report['rows_compared'],
            'tables': {t: {k: v for k, v in f.items() if k != 'examples' or f['missing'] or f['spurious']}
                       for t, f in report['tables'].items()}}


# ── the twins ───────────────────────────────────────────────────────────────
@contextmanager
def twins(copy, twin):
    import pytest
    from native_twins import Twins, install_clock
    from tests.native_coordinator_helpers import close_owned
    mp = pytest.MonkeyPatch()
    install_clock(mp)  # both runs of one call read one instant (see native_twins.CLOCK)
    try:
        yield Twins(mp, twin, copy, visibility='legacy')
    finally:
        close_owned()
        mp.undo()


class Work:
    """Captures every native command's `work` counters, and relation-maintenance time."""

    def __init__(self, mp):
        from taskmaster.native import commands, relations
        self.log, self.maintain_s = [], 0.0
        execute, maintain = commands.execute, relations.maintain

        def counted_execute(connection, request, *args, **kwargs):
            started = time.perf_counter()
            receipt = execute(connection, request, *args, **kwargs)
            self.log.append({'op': request.get('operation'), 'work': (receipt or {}).get('work'),
                             'wall_s': round(time.perf_counter() - started, 4)})
            return receipt

        def timed_maintain(*args, **kwargs):
            started = time.perf_counter()
            try:
                return maintain(*args, **kwargs)
            finally:
                self.maintain_s += time.perf_counter() - started
        mp.setattr(commands, 'execute', counted_execute)
        mp.setattr(relations, 'maintain', timed_maintain)

    def take(self):
        log, spent = self.log, self.maintain_s
        self.log, self.maintain_s = [], 0.0
        total = Counter()
        for entry in log:
            total.update(entry['work'] or {})
        return {'commands': [e['op'] for e in log], 'work': dict(total), 'maintain_s': round(spent, 4),
                'command_wall_s': round(sum(e['wall_s'] for e in log), 4)}


def both(tw, tool, **kwargs):
    """One public call on the legacy twin then the native copy, timed; texts compared."""
    import datetime as dt
    from native_twins import CLOCK, normalize
    from taskmaster import backlog_server as bs
    instant = CLOCK['at'] + dt.timedelta(minutes=1)
    out = {}
    for side, root in (('legacy', tw.legacy), ('native', tw.native)):
        CLOCK.update(at=instant, tick=False)
        with tw.at(root):
            started = time.perf_counter()
            text = getattr(bs, tool)(**kwargs)
            out[side] = (text, round(time.perf_counter() - started, 3))
    CLOCK.update(at=instant, tick=False)
    same = normalize(tw._rooted(out['native'][0], tw.native)) == normalize(tw._rooted(out['legacy'][0], tw.legacy))
    return out['legacy'][0], out['native'][0], {'legacy_s': out['legacy'][1], 'native_s': out['native'][1],
                                                'same_text': same}


# ── setup ───────────────────────────────────────────────────────────────────
def activate_recorded(root):
    """`native_twins.activate_native`, step for step, with the graph drift recorded first."""
    from taskmaster import store
    from taskmaster.native import graph_repair
    from taskmaster.native.migrate import backfill, repair_graph_for_activation
    from taskmaster.native.queries import Snapshot
    from native_twins import PREFIXES
    import re
    store.reset_for_tests()
    database = root / '.taskmaster' / 'local' / 'store.db'
    reserved = {}
    reservations = database.parent / 'id-reservations.json'
    if reservations.exists():
        reserved = json.loads(reservations.read_text(encoding='utf-8'))
    facts = {}
    with closing(sqlite3.connect(database, isolation_level=None, timeout=30)) as connection:
        started = time.perf_counter()
        backfill(connection)
        facts['backfill_s'] = round(time.perf_counter() - started, 2)
        connection.execute('BEGIN IMMEDIATE')
        connection.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        connection.execute("INSERT INTO meta(key,value) VALUES('minimum_client_protocol','2') "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        connection.execute("UPDATE native_manifest SET value='native' WHERE key='authority'")
        connection.execute("UPDATE native_manifest SET value='ready' WHERE key='state'")
        connection.execute("INSERT INTO native_manifest VALUES('local_state_imported','1') "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        # Rehearsal-only: the drift the inherited legacy rows carry, before the repair fixes it.
        facts['inherited_drift'] = verify_brief(graph_repair.verify(Snapshot(connection, _identity(connection))))
        started = time.perf_counter()
        facts['activation_repair'] = repair_graph_for_activation(connection)
        facts['activation_repair_wall_s'] = round(time.perf_counter() - started, 3)
        for kind, prefix in PREFIXES.items():
            ids = [row[0] for row in connection.execute("SELECT public_id FROM entity_core WHERE kind=?", (kind,))]
            ids += list(reserved.get(kind, []))
            high = max((int(m.group(1)) for m in (re.fullmatch(re.escape(prefix) + r'(\d+)', i) for i in ids) if m),
                       default=0)
            connection.execute('INSERT INTO id_counters VALUES(?,?,?)', (kind, prefix, high))
        for kind, ids in reserved.items():
            connection.executemany('INSERT OR IGNORE INTO id_reservations VALUES(?,?)',
                                   [(kind, ident) for ident in ids])
        connection.commit()
    return facts


def setup(copy, twin):
    import pytest
    from taskmaster import backlog_server as bs, store
    from native_twins import point_server_at
    record('setup', live=live_fingerprint(), copy_head=git(copy, 'rev-parse', 'HEAD').strip(),
           twin_head=git(twin, 'rev-parse', 'HEAD').strip())
    if is_native(copy):
        record('setup', skipped='already native')
        return
    for root in (copy, twin):
        if git(root, 'status', '--porcelain', '--', '.taskmaster').strip():
            git(root, 'add', '-A', '--', '.taskmaster')
            git(root, 'commit', '-q', '-m', 'rehearsal baseline: live working-tree .taskmaster at copy time')
    mp = pytest.MonkeyPatch()
    try:
        adopted = {}
        for root in (twin, copy):
            started = time.perf_counter()
            point_server_at(mp, root)
            bs.backlog_status()
            store.reset_for_tests()
            adopted[root.name] = round(time.perf_counter() - started, 1)
        legacy_before = graph_tables(twin)
        started = time.perf_counter()
        facts = activate_recorded(copy)
        store.reset_for_tests()
        activated = round(time.perf_counter() - started, 1)
    finally:
        mp.undo()
    with db(copy) as con:
        counts = dict(con.execute('SELECT kind, COUNT(*) FROM entity_core WHERE deleted=0 GROUP BY kind'))
    record('setup', step='adopt + activate', adopted_s=adopted, activated_s=activated, counts=counts,
           backfill_s=facts['backfill_s'], activation_repair=facts['activation_repair'],
           activation_repair_wall_s=facts['activation_repair_wall_s'], inherited_drift=facts['inherited_drift'],
           graph_rows={t: sum(c.values()) for t, c in legacy_before.items()})
    after = native_verify(copy)
    native_rows = graph_tables(copy)
    record('setup', step='native verify after activation', verify=verify_brief(after),
           legacy_twin_vs_native=table_diff(legacy_before, native_rows))
    # The legacy twin still carries the inherited drift; its own full rebuild must land on
    # exactly the rows the native oracle repaired to, or no twin comparison below means anything.
    with twins(copy, twin) as tw:
        with tw.at(twin):
            started = time.perf_counter()
            bs.backlog_index_status(rebuild=True)
            rebuilt = round(time.perf_counter() - started, 2)
        with tw.at(copy):
            started = time.perf_counter()
            text = bs.backlog_index_status(verify=True)
            verify_s = round(time.perf_counter() - started, 2)
    diff = table_diff(graph_tables(twin), graph_tables(copy))
    record('setup', step='legacy twin rebuild_derived, then compare', legacy_rebuild_s=rebuilt,
           twins_identical=not diff, diff=diff, public_verify_s=verify_s,
           public_verify=[line for line in text.splitlines() if line.startswith(('Graph', 'Cost', '  '))][:12],
           verdict='pass' if after['clean'] and not diff and 'Graph check: clean' in text else 'fail',
           live=live_fingerprint())


# ── graph: the mutation script ──────────────────────────────────────────────
def pre_n14_relations(tmp: Path):
    """The pre-N14 `native/relations.py`, from `git show`, as a scratch module."""
    import importlib.util
    target = tmp / 'pre_n14_relations.py'
    if not target.exists():
        target.write_text(git(WT, 'show', f'{PRE_N14}:taskmaster/native/relations.py'), encoding='utf-8')
    spec = importlib.util.spec_from_file_location('pre_n14_relations', target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def path_cost(copy, old, kind, ident):
    """Rerun the path neighbourhood for one entity with the pre-N14 full scan and with the N14
    indexed discovery, inside a transaction that is rolled back; compare rows, work and time."""
    from taskmaster.native import relations
    with db(copy) as con:
        con.isolation_level = None
        con.execute('BEGIN IMMEDIATE')
        try:
            paths = con.execute('SELECT path,match_kind,source FROM entity_paths WHERE kind=? AND id=?',
                                (kind, ident)).fetchall()
            if not any(source in ('anchors', 'location') for _, _, source in paths):
                return {'entity': f'{kind}:{ident}', 'structural_claims': 0}
            where = "via='path' AND ((a_kind=? AND a_id=?) OR (b_kind=? AND b_id=?))"
            current = Counter(con.execute(f'SELECT * FROM related WHERE {where}', (kind, ident, kind, ident)))
            out = {'entity': f'{kind}:{ident}', 'structural_claims': sum(s != 'prose' for *_, s in paths)}
            for name, module in (('pre_n14', old), ('n14', relations)):
                started = time.perf_counter()
                count = module._path_neighborhood(con, kind, ident, paths)
                out[f'{name}_s'] = round(time.perf_counter() - started, 4)
                out[f'{name}_work'] = count
                out[f'{name}_equal_rows'] = Counter(con.execute(f'SELECT * FROM related WHERE {where}',
                                                                (kind, ident, kind, ident))) == current
            out['pairs'] = sum(current.values())
            return out
        finally:
            con.rollback()


def pick_data(twin):
    """Real ids and anchors from the (legacy) twin, chosen deterministically."""
    with db(twin) as con:
        q = lambda sql, *a: con.execute(sql, a).fetchall()
        hot_exact = q("SELECT path FROM entity_paths WHERE source='anchors' AND match_kind='exact' "
                      "GROUP BY path ORDER BY COUNT(*) DESC, path LIMIT 1")[0][0]
        hot_glob = q("SELECT path FROM entity_paths WHERE source='anchors' AND match_kind='glob' "
                     "GROUP BY path ORDER BY COUNT(*) DESC, path LIMIT 1")[0][0]
        open_task = ("kind='task' AND deleted=0 AND archived=0 AND status='todo'")
        anchored = q(f"SELECT id, doc FROM entities WHERE {open_task} AND "
                     "json_array_length(COALESCE(json_extract(doc,'$.anchors'),'[]'))>=3 ORDER BY id")
        in_handover = {r[0] for r in q('SELECT DISTINCT task_id FROM handover_tasks')}
        many = next((i, json.loads(d)) for i, d in anchored if i in in_handover)
        other = next((i, json.loads(d)) for i, d in anchored if i in in_handover and i != many[0])
        bare = q(f"SELECT id FROM entities WHERE {open_task} AND "
                 "json_array_length(COALESCE(json_extract(doc,'$.anchors'),'[]'))=0 ORDER BY id LIMIT 1")[0][0]
        bug = q("SELECT id, doc FROM entities WHERE kind='bug' AND deleted=0 AND archived=0 AND status='open' AND "
                "json_array_length(COALESCE(json_extract(doc,'$.location'),'[]'))>=1 ORDER BY id LIMIT 1")[0]
        pair = q("SELECT a.task_id, b.task_id FROM handover_tasks a JOIN handover_tasks b ON "
                 "a.handover_id=b.handover_id AND a.task_id<b.task_id JOIN entities t ON t.kind='task' AND "
                 "t.id=a.task_id AND t.deleted=0 GROUP BY 1,2 ORDER BY COUNT(*) DESC LIMIT 1")[0]
        issue_ids = [r[0] for r in q("SELECT id FROM entities WHERE kind='issue'")]
        placed = q(f"SELECT json_extract(doc,'$.epic'), json_extract(doc,'$.phase') FROM entities WHERE {open_task} "
                   "AND json_extract(doc,'$.phase') IS NOT NULL AND json_extract(doc,'$.epic') IS NOT NULL "
                   "ORDER BY id LIMIT 1")[0]
    with db(twin) as con:
        reserved = json.loads((twin / '.taskmaster/local/id-reservations.json').read_text(encoding='utf-8')) \
            if (twin / '.taskmaster/local/id-reservations.json').exists() else {}
    numbers = [int(i[4:]) for i in issue_ids + list(reserved.get('issue', [])) if i.startswith('ISS-') and i[4:].isdigit()]
    width = max(len(i) - 4 for i in issue_ids if i.startswith('ISS-'))
    return {'hot_exact': hot_exact, 'hot_glob': hot_glob, 'many': many[0], 'many_doc': many[1],
            'other': other[0], 'bare': bare, 'bug': bug[0], 'bug_doc': json.loads(bug[1]),
            'pair': list(pair), 'new_issue': f'ISS-{max(numbers) + 1:0{width}d}',
            'target_issue': f'ISS-{max(numbers) + 2:0{width}d}',
            'epic': placed[0], 'phase': placed[1]}


NEW_TASK = 'rehearsal-n14-001'


def mutations(d):
    """(label, tool, kwargs, path entity) - applied in order on both twins."""
    anchors = [str(a) for a in d['many_doc'].get('anchors') or []]
    locations = [str(a) for a in d['bug_doc'].get('location') or []]
    epic = d['epic']
    return [
        ('anchor add exact (hottest path)', 'backlog_update_task',
         dict(task_id=d['bare'], field='anchors', value=d['hot_exact']), ('task', d['bare'])),
        ('anchor add glob (hottest glob)', 'backlog_update_task',
         dict(task_id=d['bare'], field='anchors', value=f"{d['hot_exact']},{d['hot_glob']}"), ('task', d['bare'])),
        ('anchor add glob-vs-glob', 'backlog_update_task',
         dict(task_id=d['bare'], field='anchors',
              value=f"{d['hot_exact']},{d['hot_glob']},code-maestro-app-desktop/src/components/**/*.tsx"),
         ('task', d['bare'])),
        ('anchor add prefix-less glob', 'backlog_update_task',
         dict(task_id=d['bare'], field='anchors', value=f"{d['hot_exact']},**/*.py"), ('task', d['bare'])),
        ('anchor remove all', 'backlog_update_task', dict(task_id=d['bare'], field='anchors', value=''),
         ('task', d['bare'])),
        ('anchor remove one (many-anchor task)', 'backlog_update_task',
         dict(task_id=d['many'], field='anchors', value=','.join(anchors[:-1])), ('task', d['many'])),
        ('anchor restore', 'backlog_update_task', dict(task_id=d['many'], field='anchors', value=','.join(anchors)),
         ('task', d['many'])),
        ('bug location replace', 'backlog_bug_update',
         dict(bug_id=d['bug'], field='location', value=','.join([d['hot_exact'], *locations[1:]])), ('bug', d['bug'])),
        ('bug location clear', 'backlog_bug_update', dict(bug_id=d['bug'], field='location', value=''),
         ('bug', d['bug'])),
        ('bug location restore', 'backlog_bug_update', dict(bug_id=d['bug'], field='location', value=','.join(locations)),
         ('bug', d['bug'])),
        ('handover create with task_ids (co-members + a repeat)', 'backlog_handover_create',
         dict(tldr='N14 rehearsal handover sharing real tasks', body='Rehearsal only.',
              task_ids=[*d['pair'], d['many'], d['many']]), None),
        # Every live issue lacks `evidence`, so `backlog_issue_update` refuses them all (both stores):
        # the issue edits below run on an issue the rehearsal creates.
        ('create an issue with a location', 'backlog_issue_create',
         dict(title='N14 rehearsal issue', severity='P3', evidence='rehearsal', body='Rehearsal only.',
              location=[d['hot_exact']]), ('issue', d['new_issue'])),
        ('issue location add', 'backlog_issue_update',
         dict(issue_id=d['new_issue'], field='location', value=f"{d['hot_exact']},{d['hot_glob']}"),
         ('issue', d['new_issue'])),
        ('link before target: issue duplicate_of a future issue', 'backlog_issue_update',
         dict(issue_id=d['new_issue'], field='duplicate_of', value=d['target_issue']), None),
        ('create the link target issue', 'backlog_issue_create',
         dict(title='N14 rehearsal link target', severity='P3', evidence='rehearsal', body='Rehearsal only.'),
         ('issue', d['target_issue'])),
        ('link before target: issue related_tasks a future task', 'backlog_issue_update',
         dict(issue_id=d['new_issue'], field='related_tasks', value=f"{d['many']},{NEW_TASK}"), None),
        ('create the link target task (glob anchor)', 'backlog_add_task',
         dict(title='N14 rehearsal link target task', epic=epic, phase=d['phase'],
              options={'task_id': NEW_TASK, 'anchors': d['hot_glob']}), ('task', NEW_TASK)),
        # `backlog_link` recognises prefixed ids only (not CodeMaestro's task slugs): issue -> issue.
        ('typed link create issue->issue', 'backlog_link',
         dict(action='create', source=d['target_issue'], target=d['new_issue'], type='relates_to'), None),
        ('typed link remove', 'backlog_link',
         dict(action='remove', source=d['target_issue'], target=d['new_issue'], type='relates_to'), None),
        ('archive task in handovers', 'backlog_archive_task', dict(task_id=d['other'], reason='deprecated'),
         ('task', d['other'])),
        ('unarchive task (status todo)', 'backlog_update_task', dict(task_id=d['other'], field='status', value='todo'),
         ('task', d['other'])),
    ]


def settle(tw, copy, twin, d):
    """Bring both twins to the same entity state before the script: the legacy store imports
    a re-parseable quarantined file (B-339, D2) on its first write, the native one only by an
    explicit sync. Sync those files natively, then make one no-op-valued write on both."""
    from tests.native_coordinator_helpers import compatibility_client
    with db(copy) as con:
        files = [r[0] for r in con.execute('SELECT file FROM projection WHERE quarantined=1 ORDER BY file')]
    with tw.at(copy):
        started = time.perf_counter()
        synced = compatibility_client(copy).sync(files=files)
        seconds = round(time.perf_counter() - started, 2)
    with db(twin) as con:
        priority = json.loads(con.execute("SELECT doc FROM entities WHERE kind='task' AND id=?",
                                          (d['bare'],)).fetchone()[0]).get('priority', 'medium')
    both(tw, 'backlog_update_task', task_id=d['bare'], field='priority', value=priority)
    with db(copy) as con:
        left = [r[0] for r in con.execute('SELECT file FROM projection WHERE quarantined=1 ORDER BY file')]
    report = native_verify(copy)
    diff = table_diff(graph_tables(twin), graph_tables(copy))
    record('graph', step='settle quarantined files', native_sync_s=seconds, files=files, still_quarantined=left,
           sync_state=synced.get('state'), imports=[(i.get('file'), i.get('state')) for i in synced.get('imports', [])],
           native_verify_clean=report['clean'], verify=None if report['clean'] else verify_brief(report),
           twins_identical=not diff, diff=diff)


def after_mutation(tw, copy, twin, label, timing, work, old, entity, extra=None):
    diff = table_diff(graph_tables(twin), graph_tables(copy))
    report = native_verify(copy)
    cost = path_cost(copy, old, *entity) if entity else None
    ok = not diff and report['clean']
    record('graph', mutation=label, ok=ok, twins_identical=not diff, diff=diff, native_verify_clean=report['clean'],
           verify=None if report['clean'] else verify_brief(report), verify_s=report['seconds'], **timing,
           native=work, path_cost=cost, **(extra or {}))
    return ok


def graph(copy, twin):
    import pytest
    from taskmaster import backlog_server as bs
    old = pre_n14_relations(copy.parent)
    d = pick_data(twin)
    record('graph', picks={k: v for k, v in d.items() if not k.endswith('_doc')})
    failures, handover_id = [], None
    with twins(copy, twin) as tw:
        work = Work(tw.monkeypatch)
        settle(tw, copy, twin, d)
        work.take()
        for label, tool, kwargs, entity in mutations(d):
            legacy, native, timing = both(tw, tool, **kwargs)
            timing.update(legacy_text=legacy[:240], native_text=native[:240] if not timing['same_text'] else '=')
            if tool == 'backlog_handover_create':
                import re
                found = re.search(r'\d{4}-\d{2}-\d{2}-[a-z0-9-]+', native)
                handover_id = found.group(0) if found else None
                entity = ('handover', handover_id) if handover_id else None
            if not after_mutation(tw, copy, twin, label, timing, work.take(), old, entity):
                failures.append(label)
        # Handover task_ids edited in the file, then imported: legacy on its next open, native by sync.
        if handover_id:
            failures += handover_file_edit(tw, copy, twin, handover_id, d, work, old)
            # A scalar `supersedes`: the live links table held it split into characters.
            legacy, native, timing = both(tw, 'backlog_handover_create', tldr='N14 rehearsal superseding handover',
                                          body='Rehearsal only.', task_ids=[d['many']], supersedes=handover_id)
            timing.update(legacy_text=legacy[:240], native_text=native[:240] if not timing['same_text'] else '=')
            if not after_mutation(tw, copy, twin, 'handover create superseding the rehearsal handover', timing,
                                  work.take(), old, None):
                failures.append('handover supersedes')
    record('graph', verdict='pass' if not failures else 'fail', failures=failures, live=live_fingerprint())


def handover_file_edit(tw, copy, twin, handover_id, d, work, old):
    from taskmaster import backlog_server as bs
    from tests.native_coordinator_helpers import compatibility_client
    with db(copy) as con:
        rel = con.execute("SELECT file FROM projection WHERE kind='handover' AND id=?", (handover_id,)).fetchone()[0]
    new_ids = [d['pair'][0], d['other'], NEW_TASK]
    timing = {}
    for side, root in (('legacy', twin), ('native', copy)):
        path = root / '.taskmaster' / rel
        raw = path.read_bytes().decode('utf-8')
        newline = '\r\n' if '\r\n' in raw else '\n'
        lines = raw.split(newline)
        start = lines.index('task_ids:')  # the top-level block list in the frontmatter
        end = start + 1
        while end < len(lines) and lines[end].startswith(('- ', '  - ')):
            end += 1
        lines[start + 1:end] = [f'- {ident}' for ident in new_ids]
        path.write_bytes(newline.join(lines).encode('utf-8'))
        with tw.at(root):
            started = time.perf_counter()
            if side == 'legacy':
                answer = bs.backlog_handover_get(handover_id)[:200]
            else:
                answer = json.dumps(compatibility_client(root).sync(files=[rel]), default=str)[:600]
            timing[f'{side}_s'] = round(time.perf_counter() - started, 3)
            timing[f'{side}_answer'] = answer
    with db(copy) as con:
        native_members = [r[0] for r in con.execute('SELECT task_id FROM handover_tasks WHERE handover_id=?',
                                                    (handover_id,))]
    with db(twin) as con:
        legacy_members = [r[0] for r in con.execute('SELECT task_id FROM handover_tasks WHERE handover_id=?',
                                                    (handover_id,))]
    ok = after_mutation(tw, copy, twin, 'handover task_ids edited in its file, imported', timing, work.take(), old,
                        ('handover', handover_id), {'file': rel, 'wanted': new_ids, 'native_members': native_members,
                                                    'legacy_members': legacy_members})
    return [] if ok and sorted(native_members) == sorted(new_ids) else ['handover task_ids file edit']


# ── hook ────────────────────────────────────────────────────────────────────
def hook_paths(twin, n=200, globs=20):
    sys.path.insert(0, str(WT / 'hooks'))
    import edit_resurface as hook
    rng = random.Random(14)
    with db(twin) as con:
        exact = [r[0] for r in con.execute("SELECT DISTINCT path FROM entity_paths WHERE source IN ('anchors','location') "
                                           "AND match_kind='exact' ORDER BY path")]
        patterns = [r[0] for r in con.execute("SELECT path FROM entity_paths WHERE source='anchors' AND match_kind='glob' "
                                              "GROUP BY path ORDER BY COUNT(*) DESC, path")]
    sample = rng.sample(exact, min(n, len(exact)))
    tracked = git(twin, 'ls-files').splitlines()
    chosen = []
    for pattern in patterns:
        match = next((f for f in tracked if hook._glob_match(pattern, f) and f not in sample), None)
        if match and match not in chosen:
            chosen.append(match)
        if len(chosen) >= globs:
            break
    return sample, chosen


def run_hook(root, rel, session):
    payload = json.dumps({'tool_name': 'Edit', 'tool_input': {'file_path': str(root / rel)}, 'cwd': str(root),
                          'session_id': session, 'tool_response': {'success': True}})
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_') and k != 'TASKMASTER_ROOT'}
    started = time.perf_counter()
    done = subprocess.run([PYTHON, str(WT / 'hooks' / 'edit_resurface.py')], input=payload, cwd=root, env=env,
                          capture_output=True, text=True, encoding='utf-8', timeout=60)
    seconds = time.perf_counter() - started
    line = json.loads(done.stdout)['hookSpecificOutput']['additionalContext'] if done.stdout.strip() else ''
    return line, seconds, done.returncode


def resolve_all(root, rels):
    sys.path.insert(0, str(WT / 'hooks'))
    import edit_resurface as hook
    db_file = root / '.taskmaster/local/store.db'
    answers, times = {}, []
    for rel in rels:
        started = time.perf_counter()
        result = hook.resolve(db_file, rel)
        times.append(time.perf_counter() - started)
        answers[rel] = ([(e.kind, e.id, e.status) for e in result.listed], result.closed, result.prose, result.related)
    return answers, times


def hook(copy, twin):
    from taskmaster.native import neighbourhood
    exact, globbed = hook_paths(twin)
    rels = exact + globbed + ['no/such/path/n14.txt']
    record('hook', paths=len(rels), exact=len(exact), glob_matched=len(globbed), glob_examples=globbed[:5])
    results, failures = {}, []
    for name, root in (('legacy', twin), ('native', copy)):
        answers, inproc = resolve_all(root, rels)
        session = f'n14-{name}-{uuid.uuid4().hex[:8]}'
        lines, times, codes = {}, [], Counter()
        for rel in rels:
            line, seconds, code = run_hook(root, rel, session)
            lines[rel] = line.replace(str(root), '<root>')
            times.append(seconds)
            codes[code] += 1
        results[name] = (answers, lines)
        record('hook', side=name, subprocess=stats(times), in_process=stats(inproc), exit_codes=dict(codes),
               lines_printed=sum(bool(v) for v in lines.values()),
               with_related=sum(a[3] > 0 for a in answers.values()))
    (la, ll), (na, nl) = results['legacy'], results['native']
    answer_diff = [rel for rel in rels if la[rel] != na[rel]]
    line_diff = [rel for rel in rels if ll[rel] != nl[rel]]
    record('hook', step='legacy vs native', answers_identical=not answer_diff, lines_identical=not line_diff,
           answer_diff=[(r, la[r], na[r]) for r in answer_diff[:10]], line_diff=[(r, ll[r], nl[r]) for r in line_diff[:10]])
    failures += answer_diff + line_diff
    # Native without the N14 indexes (dropped, measured, then recreated as the writer would).
    with db(copy) as con:
        for name in neighbourhood._NAMES:
            con.execute(f'DROP INDEX IF EXISTS {name}')
        con.commit()
    try:
        answers, inproc = resolve_all(copy, rels)
        session = f'n14-noindex-{uuid.uuid4().hex[:8]}'
        times, lines = [], {}
        for rel in rels:
            line, seconds, _ = run_hook(copy, rel, session)
            lines[rel] = line.replace(str(copy), '<root>')
            times.append(seconds)
        same = answers == na and lines == nl
        record('hook', side='native without N14 indexes', subprocess=stats(times), in_process=stats(inproc),
               answers_identical_to_indexed=same)
        if not same:
            failures.append('no-index answers differ')
    finally:
        with db(copy) as con:
            con.isolation_level = None
            con.execute('BEGIN IMMEDIATE')
            neighbourhood.ensure_indexes(con)
            con.execute('COMMIT')
    # The indexed neighbourhood read alone, per listed entity, on native.
    with db(copy) as con:
        listed = sorted({(k, i) for a in na.values() for k, i, _ in a[0]})
        times = []
        for kind, ident in listed:
            started = time.perf_counter()
            neighbourhood.neighbours(con, kind, ident)
            times.append(time.perf_counter() - started)
    record('hook', step='native neighbourhood() per listed entity', entities=len(listed), seconds=stats(times),
           verdict='pass' if not failures else 'fail', failures=failures[:20], live=live_fingerprint())


# ── deps ────────────────────────────────────────────────────────────────────
PRE_N14_DEPS = r'''
import json, sys, time
from pathlib import Path
import pytest
root, ids, out = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()), Path(sys.argv[3])
from taskmaster import backlog_server as bs
from native_twins import point_server_at, is_native
mp = pytest.MonkeyPatch()
point_server_at(mp, root)
native = is_native(root)
if native:
    from taskmaster.coordinator import adapter
    from native_coordinator_helpers import compatibility_client, close_owned
    mp.setattr(adapter, 'Client', compatibility_client)
answers = {}
try:
    for ident in ids:
        started = time.perf_counter()
        answers[ident] = [bs.backlog_dependencies(ident), time.perf_counter() - started]
finally:
    if native:
        close_owned()
out.write_text(json.dumps(answers), encoding='utf-8')
'''


def dep_tasks(twin, n=100):
    rng = random.Random(1414)
    with db(twin) as con:
        docs = {i: json.loads(d) for i, d in con.execute("SELECT id, doc FROM entities WHERE kind='task' AND deleted=0")}
    down = Counter()
    for ident, doc in docs.items():
        deps = doc.get('depends_on') or []
        for dep in deps if isinstance(deps, list) else [deps]:
            down[str(dep)] += 1
    degree = {i: len(doc.get('depends_on') or []) + down[i] for i, doc in docs.items()}
    connected = sorted((i for i in docs if degree[i]), key=lambda i: (-degree[i], i))
    chosen = connected[:50]
    rest = [i for i in connected[50:]]
    chosen += rng.sample(rest, min(40, len(rest)))
    chosen += rng.sample(sorted(i for i in docs if not degree[i]), 10)
    return chosen[:n]


def pre_n14_code(tmp: Path) -> Path:
    target = tmp / 'pre_n14'
    if not (target / 'taskmaster').is_dir():
        target.mkdir(exist_ok=True)
        archive = tmp / 'pre_n14.tar'
        subprocess.run(['git', '-C', str(WT), 'archive', '-o', str(archive), PRE_N14, 'taskmaster', 'tests'], check=True)
        subprocess.run(['tar', '-xf', str(archive), '-C', str(target)], check=True)
    return target


def run_pre_n14_deps(tmp, root, ids):
    code = pre_n14_code(tmp)
    ids_file, out = tmp / 'deps-ids.json', tmp / f'deps-pre-n14-{root.name}.json'
    ids_file.write_text(json.dumps(ids), encoding='utf-8')
    env = dict({k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')},
               PYTHONPATH=os.pathsep.join([str(code), str(code / 'tests')]), TASKMASTER_ROOT=str(root))
    started = time.perf_counter()
    done = subprocess.run([PYTHON, '-c', PRE_N14_DEPS, str(root), str(ids_file), str(out)], cwd=tmp, env=env,
                          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=1800)
    seconds = round(time.perf_counter() - started, 1)
    if done.returncode:
        return None, seconds, done.stderr[-1500:]
    return json.loads(out.read_text(encoding='utf-8')), seconds, None


def deps(copy, twin):
    ids = dep_tasks(twin)
    failures, per_depth, worst = [], {}, []
    depth1, flagged = {}, Counter()
    with twins(copy, twin) as tw:
        for depth in (1, 3, 10):
            times = {'legacy': [], 'native': []}
            differ = []
            for ident in ids:
                legacy, native, timing = both(tw, 'backlog_dependencies', task_id=ident, depth=depth)
                times['legacy'].append(timing['legacy_s'])
                times['native'].append(timing['native_s'])
                worst.append((timing['native_s'], depth, ident, len(native)))
                for word in ('truncated', 'cycle', 'stopped after'):
                    if word in native.lower():
                        flagged[f'd{depth}:{word}'] += 1
                if not timing['same_text']:
                    differ.append(ident)
                if depth == 1:
                    depth1[ident] = (legacy, native)
            per_depth[depth] = {'legacy': stats(times['legacy']), 'native': stats(times['native']), 'differ': differ[:10],
                                'differ_n': len(differ)}
            failures += [f'd{depth}:{i}' for i in differ]
            record('deps', depth=depth, tasks=len(ids), **per_depth[depth])
    # depth=1 must be byte-equal to what the pre-N14 code answers (the frozen contract).
    for root in (twin, copy):
        answers, seconds, error = run_pre_n14_deps(copy.parent, root, ids)
        side = 'legacy' if root == twin else 'native'
        if answers is None:
            record('deps', step=f'pre-N14 depth=1 on {side}', error=error, seconds=seconds)
            failures.append(f'pre-N14 run failed on {side}')
            continue
        mismatched = [i for i in ids if answers[i][0] != depth1[i][0 if side == 'legacy' else 1]]
        record('deps', step=f'pre-N14 depth=1 vs N14 depth=1 on {side}', byte_equal=not mismatched,
               mismatched=[(i, answers[i][0][:300], depth1[i][0 if side == 'legacy' else 1][:300]) for i in mismatched[:5]],
               pre_n14=stats([a[1] for a in answers.values()]), subprocess_s=seconds)
        failures += [f'pre-N14:{side}:{i}' for i in mismatched]
    worst.sort(reverse=True)
    record('deps', step='worst native calls', worst=worst[:5], flagged=flagged,
           verdict='pass' if not failures else 'fail', failures=failures[:20], live=live_fingerprint())


# ── query ───────────────────────────────────────────────────────────────────
def query(copy, twin):
    d = pick_data(twin)
    task = d['many']
    sqls = {
        'related by via': "SELECT via, COUNT(*), SUM(weight) FROM related GROUP BY via",
        'neighbours of a task': (f"SELECT b_kind,b_id,via,weight FROM related WHERE a_kind='task' AND a_id='{task}' "
                                 f"UNION ALL SELECT a_kind,a_id,via,weight FROM related WHERE b_kind='task' AND b_id='{task}' "
                                 "ORDER BY 1,2,3"),
        'top related pairs': "SELECT a_id,b_id,via,weight FROM related ORDER BY weight DESC, a_id, b_id LIMIT 50",
        'links by type': "SELECT type, derived, dst_kind, COUNT(*) FROM links GROUP BY 1,2,3 ORDER BY 1,2,3",
        'recursive depends_on': ("WITH RECURSIVE up(id,d) AS (SELECT '" + task + "',0 UNION SELECT l.dst_id,d+1 FROM links l "
                                 "JOIN up ON l.src_kind='task' AND l.src_id=up.id AND l.type='depends_on' AND l.derived=0 "
                                 "WHERE d<10) SELECT COUNT(*), MAX(d) FROM up"),
        'busiest handover tasks': "SELECT task_id, COUNT(*) FROM handover_tasks GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 20",
        'glob claims': "SELECT kind,id,path FROM entity_paths WHERE match_kind='glob' ORDER BY 1,2,3 LIMIT 500",
    }
    failures = []
    with twins(copy, twin) as tw:
        from taskmaster import backlog_server as bs
        for label, sql in sqls.items():
            runs = []
            for _ in range(3):
                with tw.at(copy):
                    started = time.perf_counter()
                    native = bs.backlog_query(sql, limit=500)
                    runs.append(round(time.perf_counter() - started, 3))
            with tw.at(twin):
                started = time.perf_counter()
                legacy = bs.backlog_query(sql, limit=500)
                legacy_s = round(time.perf_counter() - started, 3)
            same = native == legacy
            record('query', sql=label, native_s=runs, legacy_s=legacy_s, same_as_legacy=same,
                   native_head=native[:200] if not same else None, legacy_head=legacy[:200] if not same else None)
            if not same or native.startswith('Error'):
                failures.append(label)
        for label, kwargs in (('plain', {}), ('verify', {'verify': True}), ('rebuild', {'rebuild': True})):
            runs, text = [], ''
            for _ in range(2):
                with tw.at(copy):
                    started = time.perf_counter()
                    text = bs.backlog_index_status(**kwargs)
                    runs.append(round(time.perf_counter() - started, 3))
            graph_lines = [line for line in text.splitlines() if line.startswith(('Graph', 'Cost', '  '))][:8]
            record('query', index_status=label, native_s=runs, graph=graph_lines)
            if kwargs and 'Graph check: clean' not in text:
                failures.append(f'index_status {label} not clean')
        with tw.at(twin):
            started = time.perf_counter()
            bs.backlog_index_status()
            record('query', index_status='plain (legacy twin)', legacy_s=round(time.perf_counter() - started, 3))
    record('query', verdict='pass' if not failures else 'fail', failures=failures, live=live_fingerprint())


PHASES = {'setup': setup, 'graph': graph, 'hook': hook, 'deps': deps, 'query': query}


def main():
    global RESULTS
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--copy', type=Path, required=True, help='the copy that becomes native')
    parser.add_argument('--twin', type=Path, required=True, help='the legacy twin copy')
    parser.add_argument('--phase', required=True, choices=sorted(PHASES))
    args = parser.parse_args()
    copy, twin = guard(args.copy), guard(args.twin)
    assert copy != twin
    RESULTS = copy.parent / 'results.jsonl'
    os.chdir(copy.parent)
    PHASES[args.phase](copy, twin)


if __name__ == '__main__':
    main()
