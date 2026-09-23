# User intent: N13's last acceptance item - a copy-only rehearsal of explicit sync, managed Git,
# linked worktrees, bypassed-Git detection and crash recovery at CodeMaestro's real data volume and
# history depth. Runs only on a marked disposable clone (no remotes); never on the live checkout.
"""Copy-only N13 rehearsal.

    python scripts/native_n13_rehearsal.py --copy C:/.../tmn13/cm --phase setup|s1|repair|s1|s2|resolve|s3|...|s7

Phases were run in that order on 2026-09-23; between s4 and s5 the copy needed hand steps
(recorded in results.jsonl and the N13 report: release/restore, a bypassed checkout -f, and
normalising a mixed-EOL handover). REHEARSAL_WORKTREE names the linked worktree dir for s5.

The copy is prepared by hand first (read-only on the source):
    git clone --no-hardlinks <live> <copy>; git -C <copy> remote remove origin
    copy the live working tree's .taskmaster/ (store.db + WAL, no -shm/locks) over the clone's
    touch <copy>/.benchmark-copy
Each phase starts its own in-process coordinator (or, for s7, one subprocess owned through its
Popen handle) and stops it before returning. Results append to <copy>/../results.jsonl.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

WT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(WT), str(WT / 'tests')]
LIVE = Path('C:/Users/gruku/Files/Work/CodeMaestro').resolve()


def guard(copy: Path) -> Path:
    copy = copy.resolve()
    assert copy != LIVE and LIVE not in copy.parents and copy not in LIVE.parents, copy
    assert (copy / '.benchmark-copy').is_file(), 'not a marked rehearsal copy'
    assert git(copy, 'remote').strip() == '', 'the copy must have no remotes'
    common = Path(git(copy, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()).resolve()
    assert copy in common.parents, f'git common dir {common} is outside the copy'
    return copy


def git(root, *args, check=True, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(env_extra or {})
    done = subprocess.run(['git', '-C', str(root), *args], env=env, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    if check and done.returncode:
        raise AssertionError(f'git {args} failed: {done.stderr[-2000:]}')
    return done.stdout


RESULTS = None


def record(phase, **values):
    line = dict(phase=phase, at=time.strftime('%H:%M:%S'), **values)
    print(json.dumps(line, default=str)[:6000], flush=True)
    with RESULTS.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(line, default=str) + '\n')


def timed(call):
    started = time.perf_counter()
    value = call()
    return value, round(time.perf_counter() - started, 3)


def brief(result, keep=12):
    """Summary of a sync/git result for the log (never authored content)."""
    if not isinstance(result, dict):
        return result
    out = {}
    for key, value in result.items():
        if key in ('imports',):
            out['imports_n'] = len(value)
            out['imports'] = [(i.get('file'), i.get('state')) for i in value[:keep]]
        elif isinstance(value, list):
            out[key + '_n'] = len(value)
            out[key] = [str(v)[:300] for v in value[:keep]]
        elif isinstance(value, dict):
            out[key] = json.loads(json.dumps(value, default=str)[:3000]) if len(json.dumps(value, default=str)) < 3000 \
                else json.dumps(value, default=str)[:3000]
        else:
            out[key] = value
    return out


@contextmanager
def owner(copy, **kwargs):
    from taskmaster.coordinator.client import Client
    from taskmaster.coordinator.service import Coordinator
    with Coordinator(copy, **kwargs) as coordinator:
        yield coordinator, Client(copy, autostart=False, timeout=900)


def db(copy):
    return closing(sqlite3.connect(copy / '.taskmaster/local/store.db'))


def state_value(copy, key):
    with db(copy) as con:
        row = con.execute('SELECT value_json FROM sync_state WHERE key=?', (key,)).fetchone()
    return None if row is None else json.loads(row[0])


def max_seq(copy):
    with db(copy) as con:
        return con.execute('SELECT COALESCE(MAX(seq),0) FROM domain_events').fetchone()[0]


def events_since(copy, seq):
    with db(copy) as con:
        return con.execute('SELECT kind, id, COUNT(*) FROM domain_events WHERE seq>? GROUP BY kind, id',
                           (seq,)).fetchall()


def projection_hash(copy, rel):
    with db(copy) as con:
        row = con.execute('SELECT content_hash FROM projection WHERE file=?', (rel,)).fetchone()
    return None if row is None else row[0]


# ── setup ──────────────────────────────────────────────────────────────────
def setup(copy):
    import pytest
    from taskmaster import backlog_server as bs, store
    from native_twins import activate_native, point_server_at
    if state_value_safe(copy, 'native'):
        record('setup', skipped='already native')
        return
    if git(copy, 'status', '--porcelain', '--', '.taskmaster').strip():
        git(copy, 'add', '-A', '--', '.taskmaster')
        git(copy, 'commit', '-q', '-m', 'rehearsal baseline: live working-tree .taskmaster at copy time')
    mp = pytest.MonkeyPatch()
    try:
        started = time.perf_counter()
        point_server_at(mp, copy)
        bs.backlog_status()
        store.reset_for_tests()
        adopted = time.perf_counter() - started
        activate_native(copy)
        store.reset_for_tests()
        activated = time.perf_counter() - started
    finally:
        mp.undo()
    with db(copy) as con:
        counts = dict(con.execute('SELECT kind, COUNT(*) FROM entity_core GROUP BY kind'))
        authority = dict(con.execute('SELECT key,value FROM native_manifest'))
    record('setup', head=git(copy, 'rev-parse', 'HEAD').strip(), commits=git(copy, 'rev-list', '--count', '--all').strip(),
           adopted_s=round(adopted, 1), activated_s=round(activated, 1), counts=counts,
           authority=authority.get('authority'), state=authority.get('state'))


def state_value_safe(copy, _):
    try:
        with db(copy) as con:
            return con.execute("SELECT value FROM native_manifest WHERE key='authority'").fetchone()[0] == 'native'
    except sqlite3.Error:
        return False


# ── 1: explicit full sync with no edits ─────────────────────────────────────
def s1(copy):
    with owner(copy) as (coordinator, client):
        with db(copy) as con:
            rows = con.execute("SELECT COUNT(*) FROM projection WHERE file NOT LIKE 'local/%'").fetchone()[0]
        seq = max_seq(copy)
        # The first syncs after activation record an observed base per file (`observe`
        # plans, one writer command each) and hit the fixed 20 s sync budget; converge.
        rounds, total, first = [], 0.0, None
        for _ in range(80):
            first, seconds = timed(lambda: client.sync())
            total += seconds
            rounds.append((seconds, first.get('observed'), len(first.get('imports', [])), first.get('state')))
            if not any(i.get('state') == 'uncertain' for i in first.get('imports', [])) and \
                    not any('time budget' in n for n in first.get('notices', [])):
                break
        record('s1', step='converge first full sync after activation', rounds=len(rounds), total_seconds=round(total, 1),
               per_round=rounds, projection_rows=rows, result=brief(first), events=events_since(copy, seq)[:20])
        status = git(copy, 'status', '--porcelain', '--', '.taskmaster')
        record('s1', step='git status after first sync', changed=len(status.splitlines()), sample=status.splitlines()[:10])
        seq = max_seq(copy)
        runs = []
        for _ in range(3):
            again, seconds = timed(lambda: client.sync())
            runs.append(seconds)
        record('s1', step='repeat full sync, no edits', seconds=runs, result=brief(again),
               new_events=len(events_since(copy, seq)))
        flushed, seconds = timed(lambda: client.flush(max_seq(copy)))
        record('s1', step='flush to max seq', seconds=seconds, result=brief(flushed))


def fingerprint(copy):
    import hashlib
    with db(copy) as con:
        rows = con.execute('SELECT entity_key, revision, deleted FROM entity_core ORDER BY entity_key').fetchall()
        seq = con.execute('SELECT COALESCE(MAX(seq),0) FROM domain_events').fetchone()[0]
    return {'seq': seq, 'entities': hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]}


def sync_until_settled(client, rounds=40, **kwargs):
    """Repeat a full sync while it only ran out of the fixed 20 s budget."""
    spent, result, n = 0.0, None, 0
    for n in range(1, rounds + 1):
        result, seconds = timed(lambda: client.sync(**kwargs))
        spent += seconds
        if not any(i.get('state') == 'uncertain' for i in result.get('imports', [])) and \
                not any('time budget' in x for x in result.get('notices', [])):
            break
    return result, round(spent, 2), n


def body_append(path: Path, text: str):
    raw = path.read_bytes()
    newline = b'\r\n' if b'\r\n' in raw else b'\n'
    if not raw.endswith(newline):
        raw += newline
    # Keep the file's own line endings: a mixed-EOL edit adopted by take_file becomes the
    # published bytes, and Git's autocrlf then never reproduces them (see report, D7).
    path.write_bytes(raw + text.encode().replace(b'\n', newline) + newline)


def pick(copy, kind, pattern, skip=()):
    """A projection file of this kind that the store publishes and Git tracks."""
    tracked = set(git(copy, 'ls-files', '--', f'.taskmaster/{pattern}').split('\n'))
    with db(copy) as con:
        for (rel,) in con.execute("SELECT file FROM projection WHERE kind=? AND quarantined=0 ORDER BY file DESC",
                                  (kind,)):
            if f'.taskmaster/{rel}' in tracked and rel not in skip and '_archive' not in rel:
                return rel
    raise AssertionError(f'no tracked {kind} file')


# ── repair: the live store's own quarantines (real data) block every full sync ──
def repair(copy):
    """B-339 is quarantined only because its body quotes a pytest `=====` rule (the
    conflict-marker check is a substring test); three archived handovers have no
    frontmatter. Repair the copy's files so sync can settle; record what it imports."""
    tm = copy / '.taskmaster'
    bug = tm / 'bugs/B-339.md'
    raw = bug.read_bytes()
    import re
    bug.write_bytes(re.sub(rb'={7,}', lambda m: b'-' * len(m.group(0)), raw))
    fixed = []
    for rel in ('handovers/_archive/2026/2026-06-12-taskmaster-notes-grounded-handover.md',
                'handovers/_archive/2026/2026-06-14-mock-grounded-playable-chain-plans-ready.md',
                'handovers/_archive/2026/2026-06-15-build-glass-shipped-slide-up-orchestrator-driven.md'):
        path = tm / rel
        raw = path.read_bytes()
        if raw.startswith(b'---'):
            continue
        ident = Path(rel).stem
        front = (f"---\nid: {ident}\ndate: '{ident[:10]}'\ncreated: '{ident[:10]}T00:00:00+00:00'\n"
                 f"tldr: rehearsal repair of a handover that had no frontmatter\nstatus: archived\n---\n")
        path.write_bytes(front.encode() + raw)
        fixed.append(rel)
    with owner(copy) as (_, client):
        seq = max_seq(copy)
        result, seconds, rounds = sync_until_settled(client)
        record('repair', seconds=seconds, rounds=rounds, repaired=['bugs/B-339.md', *fixed], result=brief(result, 20),
               events=events_since(copy, seq))


# ── 2: hand edits import exactly those files ──────────────────────────────────
def s2(copy):
    tm = copy / '.taskmaster'
    task, epic = pick(copy, 'task', 'tasks'), pick(copy, 'epic', 'epics')
    handover = pick(copy, 'handover', 'handovers')
    marker = uuid.uuid4().hex[:8]
    body_append(tm / task, f'\nRehearsal prose edit {marker}: task body line.')
    body_append(tm / handover, f'\nRehearsal prose edit {marker}: handover line.')
    raw = (tm / epic).read_bytes()
    import re
    edited = re.sub(rb'(?m)^(title: )(.*)$', lambda m: m.group(1) + m.group(2).rstrip(b'\r') + b' (rehearsal)'
                    + (b'\r' if m.group(2).endswith(b'\r') else b''), raw, count=1)
    assert edited != raw, 'epic has no title line'
    (tm / epic).write_bytes(edited)
    edits = {rel: (tm / rel).read_bytes() for rel in (task, epic, handover)}
    with owner(copy) as (_, client):
        seq = max_seq(copy)
        result, seconds, rounds = sync_until_settled(client)
        events = events_since(copy, seq)
        stable = {rel: (tm / rel).read_bytes() == content for rel, content in edits.items()}
        again, seconds2, _ = sync_until_settled(client)
        stable_after = {rel: (tm / rel).read_bytes() == content for rel, content in edits.items()}
        record('s2', edited=[task, epic, handover], seconds=seconds, rounds=rounds, result=brief(result),
               events=events, bytes_unchanged_after_import=stable, second_sync_seconds=seconds2,
               second=brief(again), second_new_events=len(events_since(copy, max_seq(copy))),
               bytes_unchanged_after_second=stable_after,
               git_status=git(copy, 'status', '--porcelain').splitlines()[:20])


def resolve(copy):
    """Adopt the file side of every flagged (conflict) path with take_file, then sync."""
    with owner(copy) as (_, client):
        result, _, _ = sync_until_settled(client)
        flagged = [rel for rel in result.get('unresolved', [])]
        for rel in flagged:
            took, seconds = timed(lambda: client.sync(files=[rel], take_file=True))
            record('resolve', file=rel, seconds=seconds, result=brief(took))
        after, seconds, _ = sync_until_settled(client)
        record('resolve', step='sync after take_file', seconds=seconds, result=brief(after))


# ── 3: managed commit ───────────────────────────────────────────────────────
def s3(copy):
    note = copy / 'rehearsal-user-note.txt'
    note.write_text('user work staged outside the projection\n', encoding='utf-8')
    git(copy, 'add', '--', 'rehearsal-user-note.txt')
    head = git(copy, 'rev-parse', 'HEAD').strip()
    with owner(copy) as (_, client):
        result, seconds = timed(lambda: client.git_run(kind='commit', message='tm: rehearsal managed commit',
                                                       request_id='s3-commit'))
        replay, replay_seconds = timed(lambda: client.git_run(kind='commit', message='tm: rehearsal managed commit',
                                                              request_id='s3-commit'))
        idle, idle_seconds = timed(lambda: client.git_run(kind='commit', message='tm: nothing new',
                                                          request_id='s3-idle'))
    new_head = git(copy, 'rev-parse', 'HEAD').strip()
    files = git(copy, 'show', '--name-only', '--format=', 'HEAD').split()
    body = git(copy, 'log', '-1', '--format=%B')
    record('s3', seconds=seconds, result=brief(result), commits_added=int(git(copy, 'rev-list', '--count',
                                                                             f'{head}..HEAD').strip()),
           files_in_commit=files, only_projection=all(f.startswith('.taskmaster/') for f in files),
           trailer=[line for line in body.splitlines() if line.startswith('Taskmaster-Op')],
           still_staged=git(copy, 'diff', '--cached', '--name-only').split(), head_moved=new_head != head,
           replay_seconds=replay_seconds, replay=brief(replay), idle_seconds=idle_seconds, idle=brief(idle))


# ── 4: managed checkout to an older commit and back ─────────────────────────
def s4(copy):
    older = git(copy, 'rev-parse', 'HEAD~30').strip()
    branch = git(copy, 'symbolic-ref', '--short', 'HEAD').strip()
    before = fingerprint(copy)
    with owner(copy) as (_, client):
        out, seconds = timed(lambda: client.git_run(kind='checkout', ref=older, request_id='s4-out'))
        after = fingerprint(copy)
        drift = (client.git_status().get('drift') or {}).get('files') or {}
        record('s4', step='managed checkout HEAD~30', seconds=seconds, result=brief(out), drift_count=len(drift),
               store_before=before, store_after=after, store_rolled_back=before != after)
        synced, sync_seconds, _ = sync_until_settled(client)
        record('s4', step='full sync while drift held', seconds=sync_seconds, state=synced.get('state'),
               unresolved_n=len(synced.get('unresolved', [])), imports_n=len(synced.get('imports', [])),
               store_after=fingerprint(copy))
        back, seconds = timed(lambda: client.git_run(kind='checkout', ref=branch, request_id='s4-back-1'))
        record('s4', step='managed checkout back while drift held', seconds=seconds, result=brief(back))
        # take_file on one drift path: the explicit adoption of the older bytes.
        taken = sorted(rel for rel, digest in drift.items() if digest is not None)[:1]
        if taken:
            took, seconds = timed(lambda: client.sync(files=taken, take_file=True))
            record('s4', step='take_file one drift path', file=taken, seconds=seconds, result=brief(took),
                   drift_left=len((client.git_status().get('drift') or {}).get('files') or {}))
        released, seconds = timed(lambda: client.git_recover(release_drift='take_published'))
        record('s4', step='release take_published', seconds=seconds, result=brief(released),
               drift_left=len((client.git_status().get('drift') or {}).get('files') or {}))
        back, seconds = timed(lambda: client.git_run(kind='checkout', ref=branch, request_id='s4-back-2'))
        record('s4', step='managed checkout back after release', seconds=seconds, result=brief(back),
               head=git(copy, 'rev-parse', '--abbrev-ref', 'HEAD').strip(),
               drift_left=len((client.git_status().get('drift') or {}).get('files') or {}))
        synced, sync_seconds, _ = sync_until_settled(client)
        record('s4', step='full sync after return', seconds=sync_seconds, result=brief(synced),
               store_after=fingerprint(copy), git_status=git(copy, 'status', '--porcelain').splitlines()[:20])


# ── 5: linked worktree ──────────────────────────────────────────────────────
def s5(copy):
    wt = copy.parent / os.environ.get('REHEARSAL_WORKTREE', 'cm-wt')
    assert LIVE not in wt.resolve().parents
    if not wt.exists():
        git(copy, 'worktree', 'add', '-q', '-b', f'rehearsal-{wt.name}', str(wt), 'HEAD')
    with owner(copy) as (_, client):
        first, seconds, rounds = sync_until_settled(client, worktree=wt)
        record('s5', step='first sync(worktree=W)', seconds=seconds, rounds=rounds, result=brief(first))
        again, seconds, _ = sync_until_settled(client, worktree=wt)
        record('s5', step='repeat sync(worktree=W), no edits', seconds=seconds, result=brief(again))
        task = pick(copy, 'task', 'tasks')
        body_append(wt / '.taskmaster' / task, f'\nRehearsal linked-worktree edit {uuid.uuid4().hex[:8]}.')
        edited = (wt / '.taskmaster' / task).read_bytes()
        seq = max_seq(copy)
        head = git(wt, 'rev-parse', 'HEAD').strip()
        result, seconds = timed(lambda: client.git_run(kind='commit', message='tm: rehearsal linked commit',
                                                       request_id='s5-commit', worktree=str(wt)))
        files = git(wt, 'show', '--name-only', '--format=', 'HEAD').split()
        record('s5', step='managed commit in W', seconds=seconds, result=brief(result), file=task,
               events=events_since(copy, seq), commits_added=int(git(wt, 'rev-list', '--count',
                                                                       f'{head}..HEAD').strip()),
               files_in_commit=files[:20], files_in_commit_n=len(files),
               main_has_edit=(copy / '.taskmaster' / task).read_bytes() == edited)
        before = fingerprint(copy)
        git(wt, 'reset', '-q', '--hard', 'HEAD~1')
        after_reset, seconds, _ = sync_until_settled(client, worktree=wt)
        status = client.git_status()
        holds = {ident: rec.get('holds') for ident, rec in status['checkouts'].items() if rec.get('linked')}
        record('s5', step='bypassed reset --hard HEAD~1 in W, then sync(worktree=W)', seconds=seconds,
               result=brief(after_reset), holds=holds, store_before=before, store_after=fingerprint(copy),
               main_still_has_edit=(copy / '.taskmaster' / task).read_bytes() == edited,
               w_has_edit=(wt / '.taskmaster' / task).read_bytes() == edited)
        released, seconds = timed(lambda: client.git_recover(release_drift='take_published', worktree=str(wt)))
        settle, seconds2, _ = sync_until_settled(client, worktree=wt)
        record('s5', step='release take_published in W + sync', seconds=seconds + seconds2, result=brief(released),
               sync=brief(settle), w_has_edit=(wt / '.taskmaster' / task).read_bytes() == edited)


# ── 6: bypassed operations on the main copy ─────────────────────────────────
def s6(copy):
    from taskmaster.coordinator import checkouts
    tm = copy / '.taskmaster'
    with owner(copy) as (_, client):
        clean, seconds, _ = sync_until_settled(client)
        record('s6', step='baseline sync', seconds=seconds, state=clean.get('state'))
        # (a) plain commit of an edited projection: authored.
        task = pick(copy, 'task', 'tasks', skip=())
        body_append(tm / task, f'\nRehearsal plain-commit edit {uuid.uuid4().hex[:8]}.')
        git(copy, 'commit', '-q', '-m', 'user: plain commit of a projection edit', '--', f'.taskmaster/{task}')
        seq = max_seq(copy)
        a, seconds, _ = sync_until_settled(client)
        record('s6', step='(a) plain git commit of edited projection, then sync', file=task, seconds=seconds,
               result=brief(a), events=events_since(copy, seq))
        # (b) restore the older committed bytes of that path: held.
        before = fingerprint(copy)
        git(copy, 'checkout', 'HEAD~1', '--', f'.taskmaster/{task}')
        b, seconds, _ = sync_until_settled(client)
        drift = (client.git_status().get('drift') or {}).get('files') or {}
        record('s6', step='(b) git checkout HEAD~1 -- file, then sync', seconds=seconds, result=brief(b),
               held=task in drift, store_before=before, store_after=fingerprint(copy))
        released = client.git_recover(release_drift='take_published')
        settled, _, _ = sync_until_settled(client)
        record('s6', step='(b) release take_published', result=brief(released), sync_state=settled.get('state'))
        git(copy, 'reset', '-q', '--', f'.taskmaster/{task}')  # the restore also staged the old bytes
        # (c) revert --no-commit of the plain commit, then commit: held.
        target = git(copy, 'log', '-1', '--format=%H', '--', f'.taskmaster/{task}').strip()
        git(copy, 'revert', '--no-commit', target)
        mid, seconds_mid, _ = sync_until_settled(client)
        git(copy, 'commit', '-q', '-m', 'user: revert of the plain commit')
        before = fingerprint(copy)
        c, seconds, _ = sync_until_settled(client)
        drift = (client.git_status().get('drift') or {}).get('files') or {}
        record('s6', step='(c) revert --no-commit (+sync mid-operation) then commit, then sync',
               mid_state=mid.get('state'), mid_unresolved=mid.get('unresolved', [])[:5], mid_seconds=seconds_mid,
               seconds=seconds, result=brief(c), held=task in drift, store_before=before,
               store_after=fingerprint(copy))
        released = client.git_recover(release_drift='take_published')
        settled, _, _ = sync_until_settled(client)
        record('s6', step='(c) release take_published', result=brief(released), sync_state=settled.get('state'))
        # Cost of the drift-detection history walk at real depth.
        main = checkouts.main(copy)
        with db(copy) as con:
            rels = [r[0] for r in con.execute("SELECT file FROM projection WHERE file LIKE 'tasks/%' ORDER BY file")]
        walks = {}
        for n in (1, 100, 500):
            (blobs, complete), seconds = timed(lambda: checkouts.history_blobs(main, rels[:n]))
            walks[n] = {'seconds': seconds, 'blobs': len(blobs), 'complete': complete}
        record('s6', step='history walk cost', commits=git(copy, 'rev-list', '--count', '--all', '--reflog').strip(),
               walks=walks)


# ── 7: crash mid managed commit ─────────────────────────────────────────────
CRASH_SCRIPT = """
import sys, time
from pathlib import Path
from taskmaster.coordinator import sync_worker
from taskmaster.coordinator.service import Coordinator
sync_worker._synchronize.__kwdefaults__['timeout'] = int(sys.argv[2])
with Coordinator(Path(sys.argv[1])):
    while True:
        time.sleep(0.1)
"""


def s7(copy):
    from taskmaster.coordinator import git as managed, job as jobs
    from taskmaster.coordinator.client import Client
    from taskmaster.coordinator.protocol import ServiceUnavailable
    from taskmaster.coordinator.service import Coordinator
    hooks = copy.parent / 'hooks'
    hooks.mkdir(exist_ok=True)
    assert git(copy, 'config', 'core.hooksPath').strip().replace('\\', '/').lower() == hooks.as_posix().lower()
    entered, release = copy.parent / 'hook-entered', copy.parent / 'hook-release'
    for path in (entered, release):
        if path.exists():
            path.unlink()
    hook = hooks / 'pre-commit'
    hook.write_text(f'#!/bin/sh\ntouch "{entered.as_posix()}"\nwhile [ ! -f "{release.as_posix()}" ]; '
                    f'do sleep 0.05; done\nexit 0\n', encoding='utf-8', newline='\n')
    task = pick(copy, 'task', 'tasks')
    body_append(copy / '.taskmaster' / task, f'\nRehearsal crash-window edit {uuid.uuid4().hex[:8]}.')
    racer = pick(copy, 'task', 'tasks', skip=(task,))
    racer_id = Path(racer).stem
    head, count = git(copy, 'rev-parse', 'HEAD').strip(), int(git(copy, 'rev-list', '--count', 'HEAD').strip())
    environment = dict(os.environ, PYTHONPATH=str(WT), TASKMASTER_ROOT=str(copy))
    first = subprocess.Popen([sys.executable, '-c', CRASH_SCRIPT, str(copy), str(SYNC_BUDGET)], cwd=WT, env=environment,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    replacement, marker, seen = None, None, {}
    try:
        client = Client(copy, autostart=False, timeout=900)
        deadline = time.monotonic() + 60
        while True:
            try:
                client.status()
                break
            except ServiceUnavailable:
                assert time.monotonic() < deadline, 'rehearsal coordinator did not start'
                time.sleep(0.1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            started = time.perf_counter()
            running = pool.submit(Client(copy, autostart=False, timeout=900).git_run, kind='commit',
                                  message='tm: rehearsal crash window', request_id='s7-crash')
            while not entered.exists():
                assert time.monotonic() < deadline + 600 and not running.done(), \
                    f'hook never entered: {running.result() if running.done() else "timeout"}'
                time.sleep(0.05)
            to_hook = time.perf_counter() - started
            envelope = {'protocol': 2, 'store_id': client.identity['store_id'], 'caller_scope': 'rehearsal',
                        'request_id': 's7-racing', 'operation': 'task.patch',
                        'arguments': {'id': racer_id, 'set': {'title': 'Rehearsal racing title'}},
                        'expected_revisions': []}
            racing = client.execute(envelope)['receipt']
            first.kill()  # only the rehearsal-owned coordinator, through its own Popen handle
            first.wait(timeout=30)
            try:
                running.result(timeout=120)
                lost = 'returned'
            except ServiceUnavailable as exc:
                lost = f'ServiceUnavailable: {str(exc)[:160]}'
        marker = state_value(copy, managed.MARKER_KEY)
        probe = jobs.Job.open(marker['job'])
        alive = None
        if probe is not None:
            with probe:
                alive = probe.active_processes()
        racer_rel = copy / '.taskmaster' / racer

        def checkpoint(name):
            if name == 'git_recovery_retiring':
                seen['pin_before'] = replacement.git_pin is not None
                seen['published_before'] = b'Rehearsal racing title' in racer_rel.read_bytes()
                seen['flush_before'] = replacement.flush(racing['commit_seq'], timeout=0).get('state')
            elif name == 'git_recovery_quiesced':
                observer = jobs.Job.open(marker['job'])
                if observer is not None:
                    with observer:
                        seen['active_after'] = observer.active_processes()
                seen['published_at_quiesce'] = b'Rehearsal racing title' in racer_rel.read_bytes()
        replacement = Coordinator(copy, checkpoint=checkpoint)
        started = time.perf_counter()
        replacement.start()
        deadline = time.monotonic() + 120
        while replacement.git_pin is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        recovery_seconds = time.perf_counter() - started
        last = managed.read_state(replacement, managed.LAST_KEY)
        flushed = replacement.flush(racing['commit_seq'])
        record('s7', seconds_to_hook=round(to_hook, 2), in_flight_call=lost, marker_phase=marker.get('phase'),
               job_active_after_kill=alive, seen=seen, recovery_seconds=round(recovery_seconds, 2),
               pin_after=replacement.git_pin, last_state=last.get('state'), last_recovered=last.get('recovered'),
               head_unchanged=git(copy, 'rev-parse', 'HEAD').strip() == head,
               commits_added=int(git(copy, 'rev-list', '--count', 'HEAD').strip()) - count,
               marker_cleared=managed.read_state(replacement, managed.MARKER_KEY) is None,
               racing_flush=flushed.get('state'),
               racing_published=b'Rehearsal racing title' in racer_rel.read_bytes(),
               retry_same_request=brief(managed.run(replacement, kind='commit', caller_scope='explicit-git',
                                                    request_id='s7-crash', message='tm: rehearsal crash window')))
        hook.unlink()
        result, seconds = timed(lambda: managed.run(replacement, kind='commit', caller_scope='explicit-git',
                                                    request_id='s7-after', message='tm: rehearsal after crash'))
        record('s7', step='fresh managed commit after recovery (hook removed)', seconds=seconds, result=brief(result))
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=30)
        release.touch()
        if replacement is not None and replacement.server is not None:
            replacement.close()
        if marker and marker.get('job'):
            leftover = jobs.Job.open(marker['job'])
            if leftover is not None:
                with leftover:
                    leftover.retire(10)
        if hook.exists():
            hook.unlink()


SYNC_BUDGET = 900


def widen_sync_budget():
    """Rehearsal-only: the coordinator's full sync has a fixed 20 s budget that IPC callers
    cannot change, and at CodeMaestro scale a no-edit sync needs ~60-90 s, so it never
    reaches `synchronized` and managed Git always refuses (defect D1 in the N13 report).
    Widening the default in this process (and in the s7 subprocess) lets the remaining
    behaviour be exercised; product code is unchanged."""
    from taskmaster.coordinator import sync_worker
    sync_worker._synchronize.__kwdefaults__['timeout'] = SYNC_BUDGET


PHASES = {'setup': setup, 's1': s1, 'repair': repair, 's2': s2, 'resolve': resolve, 's3': s3, 's4': s4, 's5': s5, 's6': s6, 's7': s7}


def main():
    global RESULTS
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--copy', type=Path, required=True)
    parser.add_argument('--phase', required=True, choices=sorted(PHASES))
    args = parser.parse_args()
    copy = guard(args.copy)
    widen_sync_budget()
    RESULTS = copy.parent / 'results.jsonl'
    os.chdir(copy.parent)
    PHASES[args.phase](copy)


if __name__ == '__main__':
    main()
