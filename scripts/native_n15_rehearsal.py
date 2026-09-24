# User intent: N15's copy-only acceptance rehearsal - prove the production legacy->native cutover
# command (dry run, cutover, crash resume/rollback, acked-write refusal, escape hatch, old-client
# refusal) at CodeMaestro's real volume and data shapes. Never touches the live checkout's store.
"""Copy-only N15 rehearsal (the production cutover command).

    python scripts/native_n15_rehearsal.py --tmn C:/Users/gruku/AppData/Local/Temp/tmn15 \
        --phase setup|dryrun|cutover|crash|acked|escape|oldclient [--points a,b,...]

Run the phases in that order, one per process. Layout under --tmn:
    cm/            full clone of the live repo (no remote, marked, hooksPath outside): the main copy
    golden/        the adopted legacy `.taskmaster/` right after setup (seed for every mini copy)
    crash/<p>-r|b  mini copies (git init + golden .taskmaster) crashed at point p, then resumed|rolled back
    acked/         crash at compare, a raw write through the fence, rollback refused, resume
    escape-*/      the projection files of the cut-over main copy adopted into a fresh legacy store
    old-*/         copies of the activated store opened by old builds
    state.json     inter-phase facts (digests, sample ids); results.jsonl: one JSON line per step
The live checkout is only `git clone`d and its `.taskmaster/` read (bytes copied). Every cutover,
resume and rollback runs through the real CLI (`python -m taskmaster.native.cutover`) in a
subprocess with the real quiesce probes. results.jsonl holds hashes, counts, ids and timings,
never authored content; mismatching answers are written to <tmn>/diag/ for local diagnosis only.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import sqlite3
import subprocess
import sys
import time

WT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(WT), str(WT / 'tests')]
LIVE = Path('C:/Users/gruku/Files/Work/CodeMaestro').resolve()
SOURCE = LIVE  # `--source` swaps in a synthetic repo for a smoke run of this script only.
TMN_PARENT = Path('C:/Users/gruku/AppData/Local/Temp').resolve()
PYTHON = sys.executable
OLD_BUILDS = {  # A 6.0.x build, read-only against a copy of the activated store.
    '602-plugin': Path.home() / '.claude/plugins/cache/gruku-tools/taskmaster/6.0.2',
    '603-plugin': Path.home() / '.claude/plugins/cache/gruku-tools/taskmaster/6.0.3',
    'master-release': WT.parent / 'master-release',
}
CRASH_POINTS = ('fence:before-commit', 'fence:after-commit', 'reconcile:after-commit', 'backup:before-commit',
                'backup:after-commit', 'backfill.entities', 'backfill:after-commit', 'compare:before-commit',
                'activate:before-commit', 'activate:after-commit', 'release:before-commit')
ACTIVATED = {'activate:after-commit', 'release:before-commit', 'release:after-commit'}
FROZEN = dt.datetime(2026, 9, 24, 12, 0, 0, tzinfo=dt.timezone.utc)
ACKED_MARK = '\n[n15 rehearsal acked write]'
TMN: Path = None
RESULTS: Path = None
BASE: Path = None  # TMN, or TMN/<run> for a rerun on fresh copies
CODE = None


# ── guards and plumbing ─────────────────────────────────────────────────────
def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_') and k != 'TASKMASTER_ROOT'}
    env.update(extra)
    return env


def git(root, *args, check=True):
    done = subprocess.run(['git', '-C', str(root), *args], env=clean_env(), capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    if check and done.returncode:
        raise AssertionError(f'git {args} failed: {done.stderr[-2000:]}')
    return done.stdout


def inside_tmn(path: Path) -> Path:
    path = path.resolve()
    assert TMN in path.parents, f'{path} is outside {TMN}'
    assert path != LIVE and LIVE not in path.parents and path not in LIVE.parents, path
    return path


def guard(copy: Path) -> Path:
    copy = inside_tmn(copy)
    assert (copy / '.benchmark-copy').is_file(), 'not a marked rehearsal copy'
    assert git(copy, 'remote').strip() == '', 'the copy must have no remotes'
    common = Path(git(copy, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()).resolve()
    assert copy in common.parents, f'git common dir {common} is outside the copy'
    hooks = Path(git(copy, 'config', '--get', 'core.hooksPath').strip()).resolve()
    assert copy not in hooks.parents and hooks != copy, 'core.hooksPath must point outside the copy'
    return copy


def live_fingerprint():
    """Read-only: the live checkout's HEAD, worktree/branch counts and store file stats."""
    local = SOURCE / '.taskmaster' / 'local'
    return {'head': git(SOURCE, 'rev-parse', 'HEAD').strip(),
            'worktrees': len(git(SOURCE, 'worktree', 'list', '--porcelain').split('\nworktree ')),
            'branches': len(git(SOURCE, 'branch', '--list').splitlines()),
            'store': {p.name: [p.stat().st_size, p.stat().st_mtime_ns] for p in sorted(local.glob('store.db*'))}}


def record(phase, **values):
    line = dict(phase=phase, at=time.strftime('%H:%M:%S'), code=CODE, run=BASE.name if BASE != TMN else None,
                **values)
    print(json.dumps(line, default=str)[:4000], flush=True)
    with RESULTS.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(line, default=str) + '\n')


def state(**updates):
    path = TMN / 'state.json'
    current = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if updates:
        current.update(updates)
        path.write_text(json.dumps(current, indent=1, default=str), encoding='utf-8')
    return current


def db_path(root):
    return root / '.taskmaster' / 'local' / 'store.db'


def ro(root):
    return closing(sqlite3.connect(f'{db_path(root).resolve().as_uri()}?mode=ro', uri=True, isolation_level=None,
                                   timeout=30))


def rw(root):
    return closing(sqlite3.connect(db_path(root), isolation_level=None, timeout=30))


def sha(text) -> str:
    return hashlib.sha256(text.encode('utf-8') if isinstance(text, str) else text).hexdigest()


def tree(root, *, local=True) -> dict:
    """rel -> sha256 of every `.taskmaster` file; a read-only open's -shm and empty -wal are skipped."""
    base, out = root / '.taskmaster', {}
    for path in sorted(base.rglob('*')):
        rel = path.relative_to(base).as_posix()
        if not path.is_file() or path.name.endswith('-shm') or (path.name.endswith('-wal') and not path.stat().st_size):
            continue
        if not local and rel.startswith('local/'):
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def tree_diff(before: dict, after: dict) -> dict:
    return {'added': sorted(set(after) - set(before))[:20], 'removed': sorted(set(before) - set(after))[:20],
            'changed': sorted(k for k in set(before) & set(after) if before[k] != after[k])[:20]}


def schema_rows(root) -> list:
    with ro(root) as con:
        return [list(r) for r in con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                                             "ORDER BY type,name")]


def domain(root) -> dict:
    """The rollback equivalence (`cutover.domain_digest`) plus authority markers, read-only."""
    from taskmaster.native import cutover
    with ro(root) as con:
        state_ = cutover.classify(con)
        return {'digest': cutover.domain_digest(con), 'authority': state_['authority'],
                'schema_version': state_['schema_version'], 'migration_state': state_['migration_state'],
                'journal': cutover._has_table(con, cutover.JOURNAL)}


def committed_index(root) -> dict:
    """(kind, id) -> (title, name, archived, sha256(body.strip()), sha256(doc)) from either authority."""
    from tests.native_twins import committed
    return {f'{k}:{i}': [d.get('title'), d.get('name'), a, sha((b or '').strip()),
                         sha(json.dumps(d, sort_keys=True, default=str))]
            for (k, i), (d, b, a) in committed(root).items()}


def index_hash(index: dict) -> str:
    return sha(json.dumps(sorted(index.items()), default=str))


def counts_by_kind(index: dict) -> dict:
    out = {}
    for key in index:
        out[key.split(':', 1)[0]] = out.get(key.split(':', 1)[0], 0) + 1
    return out


def timed(call):
    started = time.perf_counter()
    value = call()
    return value, round(time.perf_counter() - started, 3)


# ── copies ──────────────────────────────────────────────────────────────────
IGNORE_LIVE = shutil.ignore_patterns('*-shm', '*.lock', 'coordinator')
IGNORE_MINI = shutil.ignore_patterns('*-shm', '*.lock', 'coordinator', 'backups')


def init_mini(target: Path, source_taskmaster: Path, ignore=IGNORE_MINI) -> Path:
    """A disposable project: `git init`, no remote, hooks outside, marked, a `.taskmaster/` copy."""
    target = inside_tmn(target)
    assert not target.exists(), f'{target} exists; mini copies are single-use'
    target.mkdir(parents=True)
    git(target, 'init', '-q')
    git(target, 'config', 'core.hooksPath', str(TMN / 'hooks'))
    (target / '.benchmark-copy').write_text('n15 rehearsal copy\n', encoding='utf-8')
    shutil.copytree(source_taskmaster, target / '.taskmaster', ignore=ignore)
    return guard(target)


def cli(root, *args, timed_marks=False, timeout=3600):
    """The real operator command in a subprocess; returns (exit code, report, stdout, seconds, marks)."""
    marks_file = TMN / 'diag' / f'marks-{time.time_ns()}.json'
    marks_file.parent.mkdir(exist_ok=True)
    argv = ['--root', str(root), *args]
    command = [PYTHON, '-c', TIMED_CLI, str(marks_file), *argv] if timed_marks else \
        [PYTHON, '-m', 'taskmaster.native.cutover', *argv]
    started = time.perf_counter()
    done = subprocess.run(command, cwd=WT, env=clean_env(PYTHONPATH=str(WT)), capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout)
    seconds = round(time.perf_counter() - started, 2)
    report = None
    if '--json' in args:
        try:
            report = json.loads(done.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            report = {'unparsed_stdout_tail': done.stdout[-1500:], 'stderr_tail': done.stderr[-1500:]}
    marks = json.loads(marks_file.read_text(encoding='utf-8')) if timed_marks and marks_file.exists() else None
    return done.returncode, report, done.stdout, seconds, marks


TIMED_CLI = r'''
import json, sys, time
from taskmaster.native import cutover
marks, checks = [], []
cutover.HOOKS["checkpoint"] = lambda name: marks.append((name, time.perf_counter()))
_real_integrity = getattr(cutover, "_integrity_ok", None)
if _real_integrity is not None:
    def _timed_integrity(connection):
        begin = time.perf_counter()
        try:
            return _real_integrity(connection)
        finally:
            checks.append((marks[-1][0] if marks else "start", round(time.perf_counter() - begin, 3)))
    cutover._integrity_ok = _timed_integrity
started = time.perf_counter()
code = 99
try:
    code = cutover.main(sys.argv[2:])
finally:
    with open(sys.argv[1], "w", encoding="utf-8") as stream:
        json.dump({"started": started, "ended": time.perf_counter(), "marks": marks, "integrity": checks}, stream)
sys.exit(code)
'''

CRASH_SCRIPT = r'''
import os, sys
from pathlib import Path
from taskmaster.native import cutover
def hook(name):
    if name == sys.argv[2]:
        sys.stdout.flush()
        os._exit(37)
cutover.HOOKS["checkpoint"] = hook
cutover.cutover(Path(sys.argv[1]), confirm_stopped=True)
os._exit(0)
'''


def crash(root, point):
    done = subprocess.run([PYTHON, '-c', CRASH_SCRIPT, str(root), point], cwd=WT, env=clean_env(PYTHONPATH=str(WT)),
                          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3600)
    return done.returncode, done.stderr[-1500:]


def stage_times(marks) -> dict:
    """Seconds per stage from the checkpoint hook: `<stage>:begin` to its `after-commit`."""
    if not marks:
        return {}
    at = {}
    for name, when in marks['marks']:
        at.setdefault(name, when)
    out = {'pre_fence_s': round(at.get('fence:begin', marks['ended']) - marks['started'], 3)}
    for stage in ('fence', 'reconcile', 'backup', 'backfill', 'compare', 'activate', 'release'):
        if f'{stage}:begin' in at and f'{stage}:after-commit' in at:
            out[stage] = round(at[f'{stage}:after-commit'] - at[f'{stage}:begin'], 3)
    out['total_s'] = round(marks['ended'] - marks['started'], 3)
    out['integrity_checks'] = [{'after': after, 's': seconds} for after, seconds in marks.get('integrity', [])]
    return out


def brief_report(report) -> dict:
    """A CLI report without authored content: ok, stages' non-content facts, refusals and hints."""
    if not isinstance(report, dict):
        return report
    keep = {k: report.get(k) for k in ('ok', 'mode', 'completed_stages', 'refusals', 'error', 'hint', 'fence',
                                       'restored_from', 'pre_rollback_copy', 'pre_rollback_files',
                                       'file_divergence', 'drift_absorbed', 'warnings', 'differences',
                                       'domain_digest', 'unparsed_stdout_tail', 'stderr_tail') if k in report}
    if 'token' in report:
        keep['token_present'] = bool(report['token'])
    stages = report.get('stages') or {}
    if stages:
        keep['stages'] = {
            'reconcile': (stages.get('reconcile') or {}).get('counts'),
            'backup': {k: v for k, v in (stages.get('backup') or {}).items() if k != 'carryover'},
            'backfill': stages.get('backfill'),
            'compare': {k: v for k, v in (stages.get('compare') or {}).items()},
            'activate': stages.get('activate'),
        }
    return keep


# ── in-process tool calls ───────────────────────────────────────────────────
@contextmanager
def tools(root, *, native: bool, handover_latch=True, clock=False):
    """Point the public `backlog_*` functions at `root`; a native root is served by an in-process
    coordinator through the real HTTP client (legacy visibility), as the twins tests do.
    `clock=True` freezes time for answer comparison; writes run on the real clock."""
    import pytest
    from taskmaster import store
    from tests.native_twins import install_clock, point_server_at
    from tests.native_coordinator_helpers import close_owned
    from taskmaster import backlog_server as bs
    mp = pytest.MonkeyPatch()
    try:
        if clock:
            install_clock(mp)
        point_server_at(mp, root)
        if not handover_latch:  # A fresh bridge-client process: the one-shot latch has not run.
            mp.setattr(bs, '_HANDOVER_STATUS_BACKFILL_RAN', False)
        if native:
            from taskmaster.coordinator import adapter
            from tests.native_coordinator_helpers import compatibility_client
            mp.setattr(adapter, 'Client', compatibility_client)
        yield bs
    finally:
        close_owned()
        store.reset_for_tests()
        mp.undo()


def frozen_call(bs, tool, **kwargs):
    from tests.native_twins import CLOCK
    CLOCK.update(at=FROZEN, tick=False)
    try:
        return getattr(bs, tool)(**kwargs)
    except Exception as error:  # noqa: BLE001 - an error is an answer to compare too
        return f'<error {type(error).__name__}: {error}>'


def pick_sample(root) -> dict:
    """About 50 real entities from the legacy store, chosen deterministically."""
    rng = random.Random(15)
    with ro(root) as con:
        def ids(kind, n, where=''):
            rows = [r[0] for r in con.execute(f"SELECT id FROM entities WHERE kind=? AND deleted=0 {where} ORDER BY id",
                                              (kind,))]
            return rng.sample(rows, min(n, len(rows)))
        tasks = ids('task', 10, "AND archived=0 AND status IN ('todo','in-progress','blocked','in-review')") + \
            ids('task', 10, "AND (archived=1 OR status='done')")
        epic = con.execute("SELECT json_extract(doc,'$.epic') FROM entities WHERE kind='task' AND id=?",
                           (tasks[0],)).fetchone()[0]
        return {'task': tasks, 'bug': ids('bug', 8), 'handover': ids('handover', 6), 'issue': ids('issue', 5),
                'decision': ids('decision', 5), 'idea': ids('idea', 5), 'epic': epic}


def read_calls(sample) -> list:
    calls = [('status', 'backlog_status', {}), ('list all', 'backlog_list_tasks', {'limit': 0}),
             ('list todo', 'backlog_list_tasks', {'status': 'todo'}),
             ('list epic', 'backlog_list_tasks', {'epic': sample['epic'], 'limit': 0})]
    for ident in sample['task']:
        calls += [(f'get task {ident}', 'backlog_get_task', {'task_id': ident}),
                  (f'get task verbose {ident}', 'backlog_get_task', {'task_id': ident, 'verbose': True}),
                  (f'deps {ident}', 'backlog_dependencies', {'task_id': ident}),
                  (f'context {ident}', 'backlog_context', {'focus': ident, 'scope': 'task'})]
    calls += [(f'bug {i}', 'backlog_bug_get', {'bug_id': i, 'verbose': True}) for i in sample['bug']]
    calls += [(f'handover {i}', 'backlog_handover_get', {'handover_id': i, 'verbose': True}) for i in sample['handover']]
    calls += [(f'issue {i}', 'backlog_issue_get', {'issue_id': i, 'verbose': True}) for i in sample['issue']]
    calls += [(f'decision {i}', 'backlog_decision', {'action': 'get', 'decision_id': i}) for i in sample['decision']]
    calls += [(f'idea {i}', 'backlog_idea_get', {'idea_id': i, 'verbose': True}) for i in sample['idea']]
    return calls


def capture_answers(bs, sample) -> tuple[dict, list]:
    from tests.native_twins import normalize
    answers, times = {}, []
    for label, tool, kwargs in read_calls(sample):
        started = time.perf_counter()
        answers[label] = normalize(frozen_call(bs, tool, **kwargs))
        times.append(time.perf_counter() - started)
    return answers, times


def stats(values):
    values = sorted(values)
    if not values:
        return None
    pick = lambda q: round(values[min(len(values) - 1, int(round(q * (len(values) - 1))))], 4)
    return {'n': len(values), 'p50': pick(.5), 'p95': pick(.95), 'max': pick(1.0), 'sum': round(sum(values), 2)}


def native_graph_verify(root) -> dict:
    from taskmaster.native import graph_repair
    from taskmaster.native.queries import Snapshot
    with ro(root) as con:
        con.execute('BEGIN')
        try:
            identity = dict(con.execute("SELECT key,value FROM native_manifest WHERE key IN "
                                        "('authority','store_id','event_high_water')"))
            report = graph_repair.verify(Snapshot(con, identity))
        finally:
            con.rollback()
    return {'clean': report['clean'], 'seconds': report['seconds'], 'rows_compared': report['rows_compared'],
            'tables': {t: {k: v for k, v in f.items() if k != 'examples'} for t, f in report['tables'].items()}}


def carryover_problems(root) -> list:
    """`verify_carryover` against the snapshot the cutover journaled at its backup stage."""
    from taskmaster.native import carryover, cutover
    with ro(root) as con:
        before = cutover._detail(cutover.journal(con), 'backup').get('carryover')
        if before is None:
            return ['no carry-over snapshot in the journal']
        con.execute('BEGIN')
        try:
            return list(carryover.verify_carryover(con, before))
        finally:
            con.rollback()


def export_backlog(root) -> dict:
    """Unexported work: dirty unquarantined projection rows and open native export jobs."""
    with ro(root) as con:
        def count(sql):
            try:
                return con.execute(sql).fetchone()[0]
            except sqlite3.OperationalError:
                return None
        return {'dirty': count("SELECT COUNT(*) FROM projection WHERE dirty=1 AND quarantined=0"),
                'quarantined': count("SELECT COUNT(*) FROM projection WHERE quarantined=1"),
                'jobs_open': count("SELECT COUNT(*) FROM projection_jobs WHERE state IN ('pending','claimed','conflict')")}


def assert_native_ok(root) -> str | None:
    from taskmaster.native.db import assert_native
    try:
        with ro(root) as con:
            assert_native(con)
        return None
    except Exception as error:  # noqa: BLE001
        return f'{type(error).__name__}: {error}'


# ── 1 setup ─────────────────────────────────────────────────────────────────
def setup(args):
    from taskmaster.native import cutover
    cm = TMN / 'cm'
    assert not cm.exists(), f'{cm} exists; setup builds a fresh copy'
    (TMN / 'hooks').mkdir(parents=True, exist_ok=True)
    before_live = live_fingerprint()
    record('setup', live=before_live)
    started = time.perf_counter()
    subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(SOURCE), str(cm)], env=clean_env(), check=True)
    git(cm, 'remote', 'remove', 'origin')
    git(cm, 'config', 'core.hooksPath', str(TMN / 'hooks'))
    (cm / '.benchmark-copy').write_text('n15 rehearsal copy\n', encoding='utf-8')
    clone_s = round(time.perf_counter() - started, 1)
    shutil.rmtree(inside_tmn(cm / '.taskmaster'))
    shutil.copytree(SOURCE / '.taskmaster', cm / '.taskmaster', ignore=IGNORE_LIVE)
    after_live = live_fingerprint()
    assert after_live['store'] == before_live['store'], 'the live store changed while it was copied; re-run setup'
    guard(cm)
    if git(cm, 'status', '--porcelain', '--', '.taskmaster').strip():
        git(cm, 'add', '-A', '--', '.taskmaster')
        git(cm, 'commit', '-q', '-m', 'rehearsal baseline: live working-tree .taskmaster at copy time')
    with ro(cm) as con:
        raw = {'journal_mode': con.execute('PRAGMA journal_mode').fetchone()[0],
               'handover_refusal_before_adoption': bool(cutover.handover_refusal(con)),
               'counts': cutover.reconcile_counts(con, cm)}
    record('setup', step='clone + copy', clone_s=clone_s, head=git(cm, 'rev-parse', 'HEAD').strip(), raw_store=raw)
    # Legacy adoption as a fresh bridge client would do it: open, then one handover call (the
    # one-shot handover-status latch the runbook requires before a cutover).
    with tools(cm, native=False, handover_latch=False) as bs:
        _, adopt_s = timed(lambda: bs.backlog_status())
        _, handover_s = timed(lambda: bs.backlog_handover_list())
    sample = pick_sample(cm)
    with tools(cm, native=False, clock=True) as bs:
        answers, times = capture_answers(bs, sample)
    diag = TMN / 'diag'
    diag.mkdir(exist_ok=True)
    (diag / 'legacy-answers.json').write_text(json.dumps(answers, default=str), encoding='utf-8')
    with rw(cm) as con:  # A quiet store: fold the WAL in so every copy starts from one file.
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
    with ro(cm) as con:
        reconcile = cutover.reconcile_counts(con, cm)
        handover = cutover.handover_refusal(con)
    dom, index = domain(cm), committed_index(cm)
    (diag / 'golden-index.json').write_text(json.dumps(index), encoding='utf-8')
    golden = inside_tmn(TMN / 'golden')
    assert not golden.exists()
    shutil.copytree(cm / '.taskmaster', golden / '.taskmaster', ignore=IGNORE_MINI)
    state(domain_digest=dom['digest'], committed_hash=index_hash(index), counts=counts_by_kind(index),
          sample=sample, answer_hashes={k: sha(json.dumps(v, default=str)) for k, v in answers.items()},
          projection_tree=sha(json.dumps(tree(cm, local=False))))
    record('setup', step='adopted legacy + checksum', adopt_s=adopt_s, handover_list_s=handover_s,
           reconcile=reconcile, handover_refusal_after=bool(handover), domain=dom,
           committed_hash=index_hash(index), counts=counts_by_kind(index),
           sample={k: len(v) if isinstance(v, list) else 1 for k, v in sample.items()},
           legacy_reads=len(answers), legacy_read_s=stats(times),
           answer_errors=sum(str(v).startswith('<error') for v in answers.values()),
           golden_files=len(tree(golden)), live_after=live_fingerprint(),
           verdict='pass' if not handover and not reconcile['dirty_unexported'] and not reconcile['export_intents']
           else 'fail')


# ── 2 dryrun ────────────────────────────────────────────────────────────────
def dryrun(args):
    cm = guard(BASE / 'cm')
    facts = state()
    before = tree(cm)
    store_before = {p: before.get(p) for p in ('local/store.db', 'local/store.db-wal')}
    runs, failures = {}, []
    for label, flags in (('text', []), ('json', ['--json']), ('text confirmed', ['--confirm-stopped']),
                         ('json confirmed', ['--json', '--confirm-stopped'])):
        code, report, stdout, seconds, _ = cli(cm, '--dry-run', *flags)
        entry = {'exit': code, 'seconds': seconds}
        if report:
            entry.update(ok=report.get('ok'), refusals=report.get('refusals'), warnings=report.get('warnings'),
                         counts=report.get('counts'), store_state={k: report.get('store_state', {}).get(k) for k in
                                                                   ('authority', 'schema_version', 'migration_state',
                                                                    'native', 'completed_stages')},
                         quiesce={'live_owner': report.get('quiesce', {}).get('live_owner'),
                                  'open_writers': report.get('quiesce', {}).get('open_writers'),
                                  'processes': [{k: p.get(k) for k in ('pid', 'name', 'launcher', 'scope')}
                                                for p in report.get('quiesce', {}).get('processes', [])]},
                         planned=len(report.get('planned', [])), projection_files=report.get('projection_files'),
                         domain_digest_matches=report.get('domain_digest') == facts['domain_digest'],
                         carryover_keys=sorted((report.get('carryover') or {}).keys()))
        else:
            entry['lines'] = [line[:240] for line in stdout.splitlines()]
        runs[label] = entry
        record('dryrun', run=label, **entry)
    after = tree(cm)
    unchanged = after == before
    if not unchanged:
        failures.append('dry run changed .taskmaster bytes')
    confirmed = runs['json confirmed']
    if confirmed['exit'] != 0 or not confirmed.get('ok'):
        failures.append('confirmed dry run did not pass')
    if not confirmed.get('domain_digest_matches'):
        failures.append('dry-run domain digest differs from setup')
    record('dryrun', store_bytes_unchanged={p: after.get(p) == h for p, h in store_before.items()},
           tree_unchanged=unchanged, tree_diff=None if unchanged else tree_diff(before, after),
           verdict='pass' if not failures else 'fail', failures=failures, live=live_fingerprint())


# ── 3 cutover ───────────────────────────────────────────────────────────────
def cutover_phase(args):
    cm = guard(BASE / 'cm')
    facts = state()
    failures = []
    dom = domain(cm)
    if dom['digest'] != facts['domain_digest']:
        failures.append('pre-cutover digest differs from setup')
    # The operator's first attempt, without --confirm-stopped: what does the real scan refuse?
    store_bytes = sum(p.stat().st_size for p in db_path(cm).parent.glob('store.db*'))
    code, report, _, seconds, marks = cli(cm, '--json', timed_marks=True)
    record('cutover', step='run without --confirm-stopped', exit=code, seconds=seconds,
           report=brief_report(report) if code else {'ok': True}, unchanged=code == 0 or domain(cm) == dom)
    if code == 2:  # Refused (the machine-wide scan sees other projects' servers): confirm, as the runbook says.
        code, report, _, seconds, marks = cli(cm, '--json', '--confirm-stopped', timed_marks=True)
    backup = ((report or {}).get('stages') or {}).get('backup') or {}
    repair = (((report or {}).get('stages') or {}).get('activate') or {}).get('graph_repair') or {}
    record('cutover', step='cutover', exit=code, wall_s=seconds, stage_s=stage_times(marks),
           backup_bytes=Path(backup['path']).stat().st_size if backup.get('path') else None,
           store_bytes_before=store_bytes, graph_repair_differences=repair.get('differences'),
           report=brief_report(report))
    if code != 0 or not (report or {}).get('ok'):
        failures.append(f'cutover exit {code}')
    problems = carryover_problems(cm)  # Before any client touches the native store.
    after = domain(cm)
    record('cutover', step='post-activation checks', authority=after['authority'],
           schema_version=after['schema_version'], migration_state=after['migration_state'],
           assert_native=assert_native_ok(cm), carryover_problems=problems[:10], graph=native_graph_verify(cm),
           export_backlog=export_backlog(cm))
    if problems:
        failures.append('carry-over verification not empty')
    if assert_native_ok(cm):
        failures.append('assert_native failed')
    code, report, _, _, _ = cli(cm, '--dry-run', '--json', '--confirm-stopped')
    refusal = any('already a native authority' in r for r in (report or {}).get('refusals', []))
    record('cutover', step='dry run after activation', exit=code, refuses_already_native=refusal)
    if not refusal:
        failures.append('dry run after activation did not refuse "already native"')
    # Committed domain: the native reconstruction equals the legacy rows.
    index = committed_index(cm)
    same = index_hash(index) == facts['committed_hash']
    record('cutover', step='committed entities vs pre-cutover legacy', identical=same, counts=counts_by_kind(index))
    if not same:
        failures.append('committed entities differ')
    # Native reads (in-process coordinator) against the pre-cutover legacy answers.
    legacy = json.loads((TMN / 'diag' / 'legacy-answers.json').read_text(encoding='utf-8'))
    with tools(cm, native=True, clock=True) as bs:
        _, start_s = timed(lambda: frozen_call(bs, 'backlog_status'))
        answers, times = capture_answers(bs, facts['sample'])
        text, verify_s = timed(lambda: bs.backlog_index_status(verify=True))
    differ = sorted(k for k in legacy if legacy[k] != answers.get(k))
    (BASE / 'native-answers.json').write_text(json.dumps(answers, default=str), encoding='utf-8')
    record('cutover', step='native reads vs legacy', calls=len(answers), differ_n=len(differ), differ=differ[:25],
           first_call_with_coordinator_start_s=start_s, native_read_s=stats(times),
           index_status_verify_s=verify_s, index_status_clean='Graph check: clean' in text,
           index_status_lines=[line[:160] for line in text.splitlines() if line.startswith(('Graph', 'Cost'))][:6],
           export_backlog_after_first_drain=export_backlog(cm))
    drained = export_backlog(cm)
    if drained['dirty'] or drained['jobs_open']:
        failures.append('dirty rows or open export jobs remain after the first drain')
    if differ:
        failures.append(f'{len(differ)} native answers differ')
    if 'Graph check: clean' not in text:
        failures.append('backlog_index_status(verify=True) not clean')
    # The first explicit sync after activation, at the stock budget; repeated only while a
    # round says its budget ran out (the ~350 s carry-forward is measured, not gated).
    from taskmaster.coordinator.client import Client
    from taskmaster.coordinator.service import Coordinator
    with Coordinator(cm) as coordinator:
        client = Client(cm, autostart=False)
        rounds, total = [], 0.0
        for _ in range(40):
            result, seconds = timed(lambda: client.sync())
            total += seconds
            imports = result.get('imports', []) if isinstance(result, dict) else []
            notices = result.get('notices', []) if isinstance(result, dict) else []
            rounds.append({'s': seconds, 'state': result.get('state') if isinstance(result, dict) else None,
                           'imports': len(imports),
                           'import_states': sorted({str(i.get('state')) for i in imports}),
                           'notices': len(notices), 'notice_heads': [str(n)[:120] for n in notices[:10]]})
            if not any(i.get('state') == 'uncertain' for i in imports) and \
                    not any('time budget' in str(n) for n in (result.get('notices', []) if isinstance(result, dict) else [])):
                break
        second, second_s = timed(lambda: client.sync())
    record('cutover', step='first explicit sync after activation', rounds=len(rounds), total_s=round(total, 1),
           per_round=rounds, second_sync_s=second_s, second_imports=len(second.get('imports', [])),
           git_changed=len(git(cm, 'status', '--porcelain', '--', '.taskmaster').splitlines()),
           carry_forward_reference_s=350, export_backlog_after_sync=export_backlog(cm))
    # A normal write, twice (cold coordinator start, then warm).
    task = facts['sample']['task'][0]
    from tests.native_twins import committed
    original = committed(cm)[('task', task)][0].get('priority') or 'medium'
    other = 'low' if original != 'low' else 'medium'
    with tools(cm, native=True) as bs:
        first, first_s = timed(lambda: bs.backlog_update_task(task_id=task, field='priority', value=other))
        second, second_s = timed(lambda: bs.backlog_update_task(task_id=task, field='priority', value=original))
    write_ok = not first.startswith('Error') and not second.startswith('Error')
    record('cutover', step='normal writes', task=task, first_write_s=first_s, second_write_s=second_s, ok=write_ok,
           graph_after=native_graph_verify(cm)['clean'])
    if not write_ok:
        failures.append('normal write failed')
    record('cutover', verdict='pass' if not failures else 'fail', failures=failures, live=live_fingerprint())


# ── 4 crash ─────────────────────────────────────────────────────────────────
def crash_phase(args):
    facts = state()
    points = args.points.split(',') if args.points else list(CRASH_POINTS)
    failures = []
    for point in points:
        slug = point.replace(':', '-').replace('.', '-')
        outcome = {'point': point}
        for twin in args.twins:
            root = init_mini(BASE / 'crash' / f'{slug}-{twin}', TMN / 'golden' / '.taskmaster')
            pre = domain(root)
            pre['schema'] = schema_rows(root)
            pre_projection = tree(root, local=False)
            assert pre['digest'] == facts['domain_digest'], 'mini copy does not match the golden digest'
            code, err = crash(root, point)
            crashed = domain(root)
            outcome[f'{twin}_crash'] = {'exit': code, 'authority': crashed['authority'],
                                        'migration_state': crashed['migration_state'], 'journal': crashed['journal']}
            if code != 37:
                failures.append(f'{point}/{twin}: crash exit {code}')
                outcome[f'{twin}_crash']['stderr'] = err[-600:]
                continue
            if twin == 'r':
                outcome['resume'] = resume_check(root, point, facts, failures)
            else:
                outcome['rollback'] = rollback_check(root, point, pre, pre_projection, failures)
        record('crash', **outcome)
    record('crash', points=len(points), verdict='pass' if not failures else 'fail', failures=failures,
           live=live_fingerprint())


def resume_check(root, point, facts, failures) -> dict:
    out = {}
    code, report, _, seconds, _ = cli(root, '--resume', '--confirm-stopped', '--json')
    out.update(exit=code, seconds=seconds, report=brief_report(report))
    if point == 'fence:before-commit':
        if code != 2 or 'no cutover journal' not in json.dumps(report):
            failures.append(f'{point}: resume did not refuse with "no cutover journal"')
        code, report, _, seconds, _ = cli(root, '--confirm-stopped', '--json')
        out['fresh_run'] = {'exit': code, 'seconds': seconds, 'completed': (report or {}).get('completed_stages')}
    completed = (report or {}).get('completed_stages') or []
    if code != 0 or not completed or completed[-1] != 'release':
        failures.append(f'{point}: resume/run did not complete (exit {code})')
    out['assert_native'] = assert_native_ok(root)
    same = index_hash(committed_index(root)) == facts['committed_hash']
    out['committed_identical'] = same
    out['carryover_problems'] = carryover_problems(root)[:5]
    out['graph_clean'] = native_graph_verify(root)['clean']
    with ro(root) as con:
        out['takeovers'] = con.execute("SELECT COUNT(*) FROM native_cutover_journal WHERE stage='takeover'").fetchone()[0]
        out['backfills'] = con.execute("SELECT COUNT(*) FROM native_cutover_journal WHERE stage='backfill' "
                                       "AND status='done'").fetchone()[0]
    if out['assert_native'] or not same or out['carryover_problems'] or not out['graph_clean']:
        failures.append(f'{point}: resumed store is not an exact native store')
    return out


def rollback_check(root, point, pre, pre_projection, failures) -> dict:
    code, report, _, seconds, _ = cli(root, '--rollback', '--confirm-stopped', '--json')
    after = domain(root)
    out = {'exit': code, 'seconds': seconds, 'report': brief_report(report), 'authority': after['authority'],
           'journal': after['journal'], 'migration_state': after['migration_state']}
    text = json.dumps(report or {})
    if point in ACTIVATED:
        if code != 2 or 'escape hatch' not in text.lower() or after['authority'] != 'native':
            failures.append(f'{point}: rollback after activation was not refused')
        return out
    if point == 'fence:before-commit':
        if code != 2 or 'no cutover fence' not in text:
            failures.append(f'{point}: rollback did not refuse with "no cutover fence"')
    elif code != 0 or not (report or {}).get('ok'):
        failures.append(f'{point}: rollback failed (exit {code})')
    out['digest_exact'] = after['digest'] == pre['digest']
    schema_after = schema_rows(root)
    out['sqlite_master_identical'] = schema_after == pre['schema']
    if not out['sqlite_master_identical']:
        before_names = {(r[0], r[1]): r[3] for r in pre['schema']}
        after_names = {(r[0], r[1]): r[3] for r in schema_after}
        out['sqlite_master_diff'] = {
            'added': sorted(map(list, set(after_names) - set(before_names)))[:20],
            'removed': sorted(map(list, set(before_names) - set(after_names)))[:20],
            'sql_changed': sorted(list(k) for k in set(before_names) & set(after_names)
                                  if before_names[k] != after_names[k])[:20]}
    out['projection_files_unchanged'] = tree(root, local=False) == pre_projection
    from taskmaster.admission import assert_compatible
    try:
        with ro(root) as con:
            assert_compatible(con)
        out['bridge_admits'] = True
    except Exception as error:  # noqa: BLE001
        out['bridge_admits'] = f'{type(error).__name__}: {error}'
    if not out['digest_exact'] or not out['sqlite_master_identical'] or after['journal'] or after['authority'] not in ('legacy', None) \
            or out['bridge_admits'] is not True or not out['projection_files_unchanged']:
        failures.append(f'{point}: rolled-back store is not the exact pre-cutover legacy store')
    return out


# ── 5 acked write ───────────────────────────────────────────────────────────
def runbook_restore_script() -> str:
    """The runbook's manual-restore block, verbatim (the operator saves it as restore_backup.py)."""
    text = (WT / 'docs' / 'runbooks' / 'native-cutover.md').read_text(encoding='utf-8')
    fence = '```python manual-restore\n'
    begin = text.index(fence) + len(fence)
    return text[begin:text.index('\n```', begin) + 1]


def operator_cli(root, *args):
    """A runbook CLI line as written; the real scan refuses this machine's other-project plugin
    servers, so it is re-run with --confirm-stopped exactly as runbook section 2 prescribes."""
    code, report, _, seconds, _ = cli(root, *args, '--json')
    literal = {'exit': code, 'refusals': (report or {}).get('refusals')}
    if code == 2 and all('processes from the launcher inventory' in r for r in (report or {}).get('refusals', [])):
        code, report, _, seconds, _ = cli(root, *args, '--json', '--confirm-stopped')
    return code, report, seconds, literal


def acked_manual_restore(facts) -> list:
    """Crash at compare and leak a write (row and its projection file). `--rollback` refuses;
    the runbook's manual restore, run literally, must end at the exact pre-cutover digest and
    byte-identical projection files with the leaked write kept aside, and a fresh cutover on
    that copy must then succeed."""
    failures = []
    root = init_mini(BASE / 'acked-restore', TMN / 'golden' / '.taskmaster')
    pre, pre_files = domain(root), tree(root, local=False)
    sidecar = db_path(root).parent / 'id-reservations.json'
    pre_sidecar = sha(sidecar.read_bytes()) if sidecar.exists() else None
    code, _ = crash(root, 'compare:before-commit')
    if code != 37:
        failures.append(f'restore: crash exit {code}')
    with rw(root) as con:
        bug = con.execute("SELECT id FROM entities WHERE kind='bug' AND deleted=0 ORDER BY id LIMIT 1").fetchone()[0]
        con.execute('UPDATE entities SET body=COALESCE(body,\'\')||? WHERE kind=\'bug\' AND id=?', (ACKED_MARK, bug))
        rel = con.execute("SELECT file FROM projection WHERE kind='bug' AND id=?", (bug,)).fetchone()[0]
    path = root / '.taskmaster' / rel
    path.write_bytes(path.read_bytes() + ACKED_MARK.encode('utf-8'))
    leaked_files = tree_diff(pre_files, tree(root, local=False))
    code, report, _, seconds, _ = cli(root, '--rollback', '--confirm-stopped', '--json')
    text = ' '.join((report or {}).get('refusals', []))
    refused = code == 2 and f'"{bug}"' in text
    record('acked', step='restore: plain rollback after a leaked row + file write', bug=bug, exit=code,
           refused_naming_entity=refused, also_names_file=rel in text, leaked_files=leaked_files,
           refusal_head=text[:600].replace(ACKED_MARK, '<mark>'))
    if not refused:
        failures.append('restore: plain rollback did not refuse naming the entity')
    # Runbook step 1: save the block as restore_backup.py, run `python restore_backup.py <project>`.
    script = BASE / 'restore_backup.py'
    script.write_text(runbook_restore_script(), encoding='utf-8')
    started = time.perf_counter()
    done = subprocess.run([PYTHON, str(script), str(root)], cwd=root, env=clean_env(), capture_output=True,
                          text=True, encoding='utf-8', errors='replace', timeout=1800)
    step1 = {'exit': done.returncode, 'seconds': round(time.perf_counter() - started, 2),
             'stdout_tail': done.stdout.strip()[-300:], 'stderr_tail': done.stderr.strip()[-600:]}
    # Step 2: clear the fence the restored store carries.
    code2, report2, seconds2, literal2 = operator_cli(root, '--rollback', '--clear-orphan-fence')
    # Step 3: verify with the dry run.
    code3, report3, _, literal3 = operator_cli(root, '--dry-run')
    state3 = (report3 or {}).get('store_state') or {}
    after, after_files = domain(root), tree(root, local=False)
    exact, files_same = after['digest'] == pre['digest'], after_files == pre_files
    sidecar_same = (sha(sidecar.read_bytes()) if sidecar.exists() else None) == pre_sidecar
    asides = sorted((db_path(root).parent / 'backups').glob('aside-*'))
    kept_row = kept_file = False
    if asides:
        aside = asides[-1]
        with closing(sqlite3.connect(f'{(aside / "store.db").resolve().as_uri()}?mode=ro', uri=True)) as con:
            row = con.execute("SELECT body FROM entities WHERE kind='bug' AND id=?", (bug,)).fetchone()
        kept_row = bool(row and (row[0] or '').endswith(ACKED_MARK))
        saved = aside / 'files' / rel
        kept_file = saved.exists() and saved.read_bytes().endswith(ACKED_MARK.encode('utf-8'))
    record('acked', step='restore: runbook manual restore (steps 1-3, literal)', script_step=step1,
           clear_fence={'exit': code2, 'seconds': seconds2, 'literal': literal2, 'ok': (report2 or {}).get('ok'),
                        'warnings': (report2 or {}).get('warnings'), 'error': (report2 or {}).get('error'),
                        'refusals': (report2 or {}).get('refusals')},
           verify_dry_run={'exit': code3, 'literal': literal3, 'authority': state3.get('authority'),
                           'migration_state': state3.get('migration_state'),
                           'refusals': (report3 or {}).get('refusals')},
           digest_exact=exact, projection_files_byte_identical=files_same, sidecar_identical=sidecar_same,
           files_diff=None if files_same else tree_diff(pre_files, after_files), journal=after['journal'],
           authority=after['authority'], migration_state=after['migration_state'],
           aside=asides[-1].name if asides else None, leaked_row_kept_aside=kept_row, leaked_file_kept_aside=kept_file)
    if step1['exit'] != 0 or code2 != 0 or code3 != 0 or state3.get('authority') != 'legacy' \
            or state3.get('migration_state') != 'ready' or not exact or not files_same or not sidecar_same \
            or after['journal'] or not kept_row or not kept_file:
        failures.append('restore: the manual restore did not return the exact pre-cutover store and files '
                        'with the leaked write kept aside')
    code, report, _, seconds, _ = cli(root, '--confirm-stopped', '--json')
    completed = (report or {}).get('completed_stages') or []
    same = index_hash(committed_index(root)) == facts['committed_hash']
    record('acked', step='restore: fresh cutover on the restored copy', exit=code, seconds=seconds,
           completed=completed, assert_native=assert_native_ok(root), committed_identical_to_golden=same,
           carryover_problems=carryover_problems(root)[:5], graph_clean=native_graph_verify(root)['clean'],
           warnings=(report or {}).get('warnings'), error=(report or {}).get('error'),
           refusals=(report or {}).get('refusals'))
    if code != 0 or not completed or completed[-1] != 'release' or assert_native_ok(root) or not same:
        failures.append('restore: a fresh cutover after the manual restore did not succeed')
    return failures


def acked_dirty(facts) -> list:
    """Crash at compare, then an old client's committed-but-unexported write: the row changes
    and its projection row is left dirty. `--resume` must carry the write into the native store
    and export it (reconcile flushes pending legacy exports itself)."""
    failures = []
    root = init_mini(BASE / 'acked-dirty', TMN / 'golden' / '.taskmaster')
    code, _ = crash(root, 'compare:before-commit')
    if code != 37:
        failures.append(f'dirty: crash exit {code}')
    with rw(root) as con:
        bug = con.execute("SELECT id FROM entities WHERE kind='bug' AND deleted=0 ORDER BY id DESC LIMIT 1"
                          ).fetchone()[0]
        con.execute('UPDATE entities SET body=COALESCE(body,\'\')||? WHERE kind=\'bug\' AND id=?', (ACKED_MARK, bug))
        rel = con.execute("SELECT file FROM projection WHERE kind='bug' AND id=?", (bug,)).fetchone()[0]
        con.execute('UPDATE projection SET dirty=1 WHERE file=?', (rel,))
    path = root / '.taskmaster' / rel
    file_before = path.read_bytes()
    code, report, _, seconds, _ = cli(root, '--resume', '--confirm-stopped', '--json')
    from tests.native_twins import committed
    entry = committed(root).get(('bug', bug))
    carried = bool(entry and (entry[1] or '').endswith(ACKED_MARK))
    exported = ACKED_MARK.strip().encode('utf-8') in path.read_bytes()
    with ro(root) as con:
        journal_rows = [(r[0], r[1], json.loads(r[2] or '{}')) for r in con.execute(
            "SELECT stage,status,detail_json FROM native_cutover_journal ORDER BY seq")]
        pending = con.execute("SELECT COUNT(*) FROM projection_jobs WHERE state IN ('pending','claimed','conflict')"
                              ).fetchone()[0]
    reconcile = [d for st, status, d in journal_rows if st == 'reconcile' and status == 'done']
    record('acked', step='dirty: resume after a leaked write whose projection is still dirty', bug=bug, file=rel,
           exit=code, seconds=seconds, write_in_native_store=carried, exported_to_file=exported,
           file_changed=path.read_bytes() != file_before, pending_projection_jobs=pending,
           drift_rows=sum(st == 'drift' for st, _, _ in journal_rows),
           reconcile_flushed=[{k: d.get(k) for k in ('counts_before', 'flushed')} for d in reconcile],
           assert_native=assert_native_ok(root), carryover_problems=carryover_problems(root)[:5],
           graph_clean=native_graph_verify(root)['clean'], warnings=(report or {}).get('warnings'),
           error=(report or {}).get('error'))
    backlog_after_resume = export_backlog(root)
    with tools(root, native=True) as bs:  # The first native drain.
        bs.backlog_status()
    drained = export_backlog(root)
    exported_after_drain = ACKED_MARK.strip().encode('utf-8') in path.read_bytes()
    record('acked', step='dirty: first native drain', export_backlog_after_resume=backlog_after_resume,
           export_backlog_after_drain=drained, exported_after_drain=exported_after_drain)
    if code != 0 or not carried or not exported_after_drain or assert_native_ok(root) or drained['dirty'] \
            or drained['jobs_open']:
        failures.append('dirty: the dirty leaked write was not carried and exported by the first drain')
    return failures


def acked(args):
    facts = state()
    failures = []
    if args.only == 'restore':
        failures += acked_manual_restore(facts)
        record('acked', only='restore', verdict='pass' if not failures else 'fail', failures=failures,
               live=live_fingerprint())
        return
    root = init_mini(BASE / 'acked', TMN / 'golden' / '.taskmaster')
    code, _ = crash(root, 'compare:before-commit')
    if code != 37:
        failures.append(f'crash exit {code}')
    with rw(root) as con:  # An unstopped pre-bridge client ignores the fence and writes.
        bug = con.execute("SELECT id FROM entities WHERE kind='bug' AND deleted=0 ORDER BY id LIMIT 1").fetchone()[0]
        con.execute('UPDATE entities SET body=COALESCE(body,\'\')||? WHERE kind=\'bug\' AND id=?', (ACKED_MARK, bug))
    written = domain(root)
    code, report, _, seconds, _ = cli(root, '--rollback', '--confirm-stopped', '--json')
    text = ' '.join((report or {}).get('refusals', []))
    named = 'entities: 0 added, 0 removed, 1 changed' in text and f'"{bug}"' in text
    unchanged = domain(root) == written
    record('acked', step='rollback after a write through the fence', bug=bug, exit=code, seconds=seconds,
           refused=code == 2, names_the_change=named, store_unchanged_by_refusal=unchanged,
           refusal_head=text[:400].replace(ACKED_MARK, '<mark>'))
    if code != 2 or not named or not unchanged:
        failures.append('rollback did not refuse naming the change')
    code, report, _, seconds, _ = cli(root, '--resume', '--confirm-stopped', '--json')
    from tests.native_twins import committed
    entry = committed(root).get(('bug', bug))
    carried = bool(entry and (entry[1] or '').endswith(ACKED_MARK))
    with ro(root) as con:
        backfills = con.execute("SELECT COUNT(*) FROM native_cutover_journal WHERE stage='backfill' "
                                "AND status='done'").fetchone()[0]
        drifts = [json.loads(r[0] or '{}').get('reasons') for r in con.execute(
            "SELECT detail_json FROM native_cutover_journal WHERE stage='drift' ORDER BY seq")]
    index = committed_index(root)
    golden = json.loads((TMN / 'diag' / 'golden-index.json').read_text(encoding='utf-8'))
    others_same = {k: v for k, v in index.items() if k != f'bug:{bug}'} == \
        {k: v for k, v in golden.items() if k != f'bug:{bug}'}
    drift_names_bug = any(bug in json.dumps(r) for r in drifts)
    record('acked', step='resume carries the write', exit=code, seconds=seconds, report=brief_report(report),
           write_in_native_store=carried, backfill_runs=backfills, drift_rows=len(drifts),
           drift_names_the_entity=drift_names_bug,
           drift_reasons=[str(r).replace(ACKED_MARK, '<mark>')[:300] for r in drifts],
           assert_native=assert_native_ok(root),
           carryover_problems=carryover_problems(root)[:5], graph_clean=native_graph_verify(root)['clean'],
           other_entities_identical=others_same)
    if code != 0 or not carried or assert_native_ok(root) or not others_same:
        failures.append('resume did not carry the acknowledged write')
    if not drifts or not drift_names_bug:
        failures.append('resume recorded no drift row naming the write')
    failures += acked_manual_restore(facts)
    failures += acked_dirty(facts)
    record('acked', verdict='pass' if not failures else 'fail', failures=failures, live=live_fingerprint())


# ── 6 escape hatch ──────────────────────────────────────────────────────────
def escape(args):
    cm = guard(BASE / 'cm')
    facts = state()
    failures = []
    assert domain(cm)['authority'] == 'native', 'run the cutover phase first'
    sample = facts['sample']
    from tests.native_twins import committed
    docs = committed(cm)
    task_doc = next(d for (k, i), (d, _, _) in sorted(docs.items(), key=lambda kv: kv[0])
                    if k == 'task' and d.get('epic') and d.get('phase') and d.get('status') == 'todo')
    writes = {}
    with tools(cm, native=True) as bs:
        for label, tool, kwargs in (
                ('add task', 'backlog_add_task', {'title': 'N15 rehearsal escape-hatch task', 'epic': task_doc['epic'],
                                                  'phase': task_doc['phase']}),
                ('create bug', 'backlog_bug_create', {'title': 'N15 rehearsal escape-hatch bug'}),
                ('update task', 'backlog_update_task', {'task_id': sample['task'][1], 'field': 'priority',
                                                        'value': 'low'}),
                ('create idea', 'backlog_idea_create', {'title': 'N15 rehearsal escape-hatch idea'})):
            answer, seconds = timed(lambda: getattr(bs, tool)(**kwargs))
            writes[label] = {'s': seconds, 'error': str(answer).startswith(('Error', '<error'))}
        # Step 1: drain every export while the native client is still up.
        pending, waited = None, 0.0
        for _ in range(120):
            with ro(cm) as con:
                pending = con.execute("SELECT COUNT(*) FROM projection_jobs WHERE state IN "
                                      "('pending','claimed','conflict')").fetchone()[0]
            if not pending:
                break
            bs.backlog_store_status()
            time.sleep(1)
            waited += 1
    record('escape', step='native writes + drain', writes=writes, pending_after=pending, waited_s=waited,
           git_changed=len(git(cm, 'status', '--porcelain', '--', '.taskmaster').splitlines()))
    if pending or any(w['error'] for w in writes.values()):
        failures.append('native writes failed or exports did not drain')
    native_index = committed_index(cm)
    authored = {k: v for k, v in native_index.items() if k.split(':', 1)[0] not in ('backlog', 'project')}
    # Steps 4-5: the files, without the store, adopted by a fresh legacy store.
    ignore = shutil.ignore_patterns('store.db', 'store.db-wal', 'store.db-shm', 'backups', 'coordinator', '*.lock')
    builds = [('current', None)] + [(name, path) for name, path in OLD_BUILDS.items()
                                    if name == 'master-release' and (path / 'taskmaster').is_dir()]
    for name, build in builds:
        fresh = init_mini(BASE / f'escape-{name}', cm / '.taskmaster', ignore=ignore)
        assert not db_path(fresh).exists()
        if build is None:
            with tools(fresh, native=False, handover_latch=False) as bs:
                _, adopt_s = timed(lambda: bs.backlog_status())
            detail = None
        else:
            detail, adopt_s = timed(lambda: old_build_call(build, fresh, ['backlog_status']))
        adopted = committed_index(fresh)
        missing = sorted(set(authored) - set(adopted))
        extra = sorted(k for k in set(adopted) - set(authored) if k.split(':', 1)[0] not in ('backlog', 'project'))
        fields = {'title': 0, 'name': 1, 'archived': 2, 'body': 3}
        mismatch = {f: sorted(k for k in set(authored) & set(adopted) if authored[k][i] != adopted[k][i])
                    for f, i in fields.items()}
        with ro(fresh) as con:
            schema = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        with ro(fresh) as con:
            quarantined = [r[0] for r in con.execute('SELECT file FROM projection WHERE quarantined=1 ORDER BY file')]
        native_quarantined = export_backlog(cm)['quarantined']
        record('escape', build=name, adopt_s=adopt_s, schema_version=schema[0] if schema else None,
               quarantined_after_adoption=quarantined, native_quarantined=native_quarantined,
               b339_present='bug:B-339' in adopted, b339_in_native='bug:B-339' in authored,
               authored=len(authored), adopted=len(adopted), missing_n=len(missing), missing=missing[:20],
               extra_n=len(extra), extra=extra[:10],
               mismatched={f: {'n': len(v), 'keys': v[:10]} for f, v in mismatch.items()},
               old_build=None if detail is None else {
                   'package': detail.get('package'), 'exit': detail.get('exit'),
                   'import_error': detail.get('import_error'),
                   'calls': {n: {k: v for k, v in c.items() if k != 'head'}
                             for n, c in detail.get('calls', {}).items()}})
        if missing or any(mismatch.values()):
            failures.append(f'{name}: authored documents lost or changed')
    record('escape', verdict='pass' if not failures else 'fail', failures=failures, live=live_fingerprint())


OLD_CALL = r'''
import json, sys, traceback
out = {"calls": {}}
try:
    import taskmaster
    out["package"] = taskmaster.__file__
    from taskmaster import backlog_server as bs
    for spec in json.loads(sys.argv[1]):
        name, kwargs = spec if isinstance(spec, list) else (spec, {})
        try:
            text = str(getattr(bs, name)(**kwargs))
            out["calls"][name] = {"ok": True, "len": len(text), "head": text[:200]}
        except Exception as error:
            out["calls"][name] = {"ok": False, "error": f"{type(error).__name__}: {str(error)[:400]}"}
except Exception as error:
    out["import_error"] = f"{type(error).__name__}: {error}"
    out["trace"] = traceback.format_exc()[-1500:]
print(json.dumps(out))
'''


def old_build_call(build: Path, root: Path, calls: list) -> dict:
    """A 6.0.x build in its own process against `root` only (no bytecode written into the build)."""
    env = clean_env(PYTHONPATH=str(build), TASKMASTER_ROOT=str(root), PYTHONDONTWRITEBYTECODE='1')
    done = subprocess.run([PYTHON, '-c', OLD_CALL, json.dumps(calls)], cwd=root, env=env, capture_output=True,
                          text=True, encoding='utf-8', errors='replace', timeout=1800)
    try:
        out = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = {'unparsed': done.stdout[-800:], 'stderr': done.stderr[-800:]}
    out['exit'] = done.returncode
    return out


# ── 7 old client ────────────────────────────────────────────────────────────
def oldclient(args):
    cm = guard(BASE / 'cm')
    failures = []
    assert domain(cm)['authority'] == 'native', 'run the cutover phase first'
    from taskmaster import admission, store
    from taskmaster.admission import UnsupportedStoreError
    # The current build's legacy (bridge) admission and Store against the activated store.
    local = init_mini(BASE / 'old-current', cm / '.taskmaster', ignore=shutil.ignore_patterns(
        '*-shm', '*.lock', 'coordinator', 'backups'))
    before = tree(local)
    refusals = {}
    try:
        with ro(local) as con:
            admission.assert_compatible(con)
        refusals['assert_compatible'] = None
    except UnsupportedStoreError as error:
        refusals['assert_compatible'] = str(error)[:300]
    store.reset_for_tests()
    task = state()['sample']['task'][0]
    try:
        store.open_store(root=local).get('task', task)
        refusals['store.get'] = None
    except UnsupportedStoreError as error:
        refusals['store.get'] = str(error)[:300]
    finally:
        store.reset_for_tests()
    unchanged = tree(local) == before
    record('oldclient', build='current bridge admission', protocol=(admission.LEGACY_SCHEMA_VERSION,
                                                                   admission.CLIENT_PROTOCOL),
           refusals=refusals, unchanged=unchanged)
    if not all(refusals.values()) or not unchanged:
        failures.append('current bridge admission did not refuse, or wrote')
    for name, build in OLD_BUILDS.items():
        if not (build / 'taskmaster').is_dir():
            record('oldclient', build=name, skipped=f'{build} not present')
            continue
        copy = init_mini(BASE / f'old-{name}', cm / '.taskmaster', ignore=shutil.ignore_patterns(
            '*-shm', '*.lock', 'coordinator', 'backups'))
        before = tree(copy)
        result = old_build_call(build, copy, ['backlog_status', ['backlog_get_task', {'task_id': task}],
                                              ['backlog_list_tasks', {'limit': 5}]])
        after = tree(copy)
        calls = result.get('calls', {})
        refused = bool(calls) and all(not c['ok'] or 'schema_version=2' in c.get('head', '') or
                                      'Unsupported' in c.get('head', '') for c in calls.values())
        answered = {n: {'ok': c['ok'], 'error': c.get('error'), 'len': c.get('len'),
                        'refusal_text': 'schema_version=2' in (c.get('head', '') + (c.get('error') or ''))}
                    for n, c in calls.items()}
        record('oldclient', build=name, package=result.get('package'), exit=result.get('exit'),
               import_error=result.get('import_error'), calls=answered, refused=refused,
               unchanged=after == before, tree_diff=None if after == before else tree_diff(before, after),
               authority_after=domain(copy)['authority'])
        if not refused or after != before:
            failures.append(f'{name}: did not refuse, or changed the copy')
    record('oldclient', verdict='pass' if not failures else 'fail', failures=failures, live=live_fingerprint())


def reseed(args):
    """A fresh main copy for a rerun: clone the first clone (its baseline commit) and put the
    golden adopted-legacy `.taskmaster/` in place; it must match the setup checksums."""
    facts = state()
    cm = inside_tmn(BASE / 'cm')
    assert BASE != TMN and not cm.exists(), 'reseed needs --run and a fresh run folder'
    BASE.mkdir(exist_ok=True)
    subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(TMN / 'cm'), str(cm)], env=clean_env(), check=True)
    git(cm, 'remote', 'remove', 'origin')
    git(cm, 'config', 'core.hooksPath', str(TMN / 'hooks'))
    (cm / '.benchmark-copy').write_text('n15 rehearsal copy\n', encoding='utf-8')
    shutil.rmtree(inside_tmn(cm / '.taskmaster'))
    shutil.copytree(TMN / 'golden' / '.taskmaster', cm / '.taskmaster')
    guard(cm)
    dom, index = domain(cm), committed_index(cm)
    ok = dom['digest'] == facts['domain_digest'] and index_hash(index) == facts['committed_hash']
    record('reseed', head=git(cm, 'rev-parse', 'HEAD').strip(), domain=dom, committed_identical=ok,
           verdict='pass' if ok else 'fail', live=live_fingerprint())


PHASES = {'setup': setup, 'reseed': reseed, 'dryrun': dryrun, 'cutover': cutover_phase, 'crash': crash_phase, 'acked': acked,
          'escape': escape, 'oldclient': oldclient}


def main():
    global TMN, RESULTS, SOURCE, BASE, CODE
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tmn', type=Path, required=True, help='the rehearsal folder (tmn15)')
    parser.add_argument('--phase', required=True, choices=list(PHASES))
    parser.add_argument('--source', type=Path, default=LIVE, help='setup: the repo to clone (default: live)')
    parser.add_argument('--run', help='rerun namespace: copies go under <tmn>/<run> (fresh from the golden seed)')
    parser.add_argument('--twins', default='rb', help="crash: which twins to run, 'r' resume, 'b' rollback")
    parser.add_argument('--only', choices=['restore'], help='acked: run only the manual-restore check')
    parser.add_argument('--points', help='crash: comma-separated checkpoint names (default: the representative set)')
    args = parser.parse_args()
    TMN = args.tmn.resolve()
    assert TMN.parent == TMN_PARENT and TMN.name.startswith('tmn15'), f'{TMN} is not a tmn15 folder under {TMN_PARENT}'
    TMN.mkdir(exist_ok=True)
    if args.phase == 'setup':
        SOURCE = args.source.resolve()
        state(source=str(SOURCE))
    else:
        SOURCE = Path(state().get('source', str(LIVE))).resolve()
    RESULTS = TMN / 'results.jsonl'
    BASE = inside_tmn(TMN / args.run) if args.run else TMN
    CODE = git(WT, 'rev-parse', '--short', 'HEAD').strip()
    os.chdir(TMN)
    PHASES[args.phase](args)


if __name__ == '__main__':
    main()
