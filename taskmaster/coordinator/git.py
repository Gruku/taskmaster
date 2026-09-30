"""User intent: managed Git captures exactly one coherent projection generation, and a
crash mid-operation can never let a replacement publisher race the Git child.

The coordinator holds `publication` for the whole operation. A durable marker is
written before any child exists; only a proven-empty job boundary plus
reconciliation of HEAD/index/generation clears it. Commit and checkout are never
replayed; uncertainty intentionally keeps publication pinned.
"""
from __future__ import annotations

import base64
from contextlib import closing
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import time
import uuid

from taskmaster.bounded_run import run_bounded
from taskmaster.native import metrics
from taskmaster.projection_paths import UnsafePath, check_component, safe_path
from . import job as jobs
from .contained import ManagedChild

LOG = logging.getLogger(__name__)

MARKER_KEY = 'git.managed'
LAST_KEY = 'git.last'
DRIFT_KEY = 'git.drift'  # == native.projection.DRIFT_KEY
RECEIPTS_KEY = 'git.receipts'
# Set-aside dirs whose files could not all be settled: [{op_id, dir, backlog, files: {rel: sha1|null}}].
# Kept durably after the marker clears; retried by the startup sweep and shown by git_status.
ASIDE_KEY = 'git.aside'
RECEIPTS_KEPT = 32
TRAILER = 'Taskmaster-Op'
DRIFT_GUIDANCE = ('managed checkout drift: the checked-out file differs from the published generation and is '
                  'not imported or overwritten; restore the published file (e.g. check the previous branch out '
                  'again), adopt it with sync take_file, or run git recover --release-drift import (sync '
                  'imports it) or take-published (it receives the published file; its bytes are retained)')
TOKEN_ENV = 'TASKMASTER_MANAGED_GIT'
PUBLICATION_TIMEOUT = 10
GIT_TIMEOUT = 600
PROBE_TIMEOUT = 60
RETIRE_TIMEOUT = 15
PROBE_LIMIT = 16 * 1024 * 1024
MAX_PROJECTION_BYTES = 64 * 1024 * 1024
MAX_MESSAGE = 64 * 1024
_KEPT = 20


class GitRefused(ValueError):
    """Refused before any marker or child: nothing changed."""


# ── Git probes (read-only, argv only, hidden, bounded) ─────────────────────

def environment(extra=None):
    """Inherited GIT_* variables (e.g. from a hook context) must not redirect Git."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    env['GIT_TERMINAL_PROMPT'] = '0'
    env.pop(TOKEN_ENV, None)
    env.update(extra or {})
    return env


def executable():
    found = shutil.which('git')
    if not found:
        raise GitRefused('git executable not found on PATH')
    return str(Path(found).resolve())


def probe(root, *args, ok=(0,), stdin=None, limit=PROBE_LIMIT, timeout=PROBE_TIMEOUT):
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    env = environment({'GIT_OPTIONAL_LOCKS': '0'})
    try:
        completed = run_bounded([executable(), '--no-optional-locks', *args], cwd=root, env=env,
                                input=stdin, timeout=timeout, **flags)
    except subprocess.TimeoutExpired:
        raise GitRefused(f'git {args[0]} timed out') from None
    if completed.returncode not in ok:
        raise GitRefused(f'git {args[0]} failed: {completed.stderr.decode("utf-8", "replace").strip()[:500]}')
    if len(completed.stdout) > limit:
        raise GitRefused(f'git {args[0]} output exceeds {limit} bytes')
    return completed.returncode, completed.stdout


def _text(root, *args, ok=(0,)):
    code, out = probe(root, *args, ok=ok)
    return code, out.decode('utf-8', 'replace').strip()


def repository(root):
    """`root` must be the Git top level of the checkout operated on (main or linked)."""
    _, top = _text(root, 'rev-parse', '--show-toplevel')
    if os.path.normcase(str(Path(top).resolve())) != os.path.normcase(str(Path(root).resolve())):
        raise GitRefused('checkout root is not the Git top level')
    _, git_dir = _text(root, 'rev-parse', '--absolute-git-dir')
    _, index = _text(root, 'rev-parse', '--git-path', 'index')
    _, common = _text(root, 'rev-parse', '--git-common-dir')
    index_path = Path(index) if Path(index).is_absolute() else Path(root) / index
    common_path = Path(common) if Path(common).is_absolute() else Path(root) / common
    return {'git_dir': str(Path(git_dir)), 'index': str(index_path), 'common_dir': str(common_path.resolve())}


def snapshot(root, repo):
    code, head = _text(root, 'rev-parse', '--verify', '-q', 'HEAD^{commit}', ok=(0, 1))
    head = head if code == 0 else None
    code, ref = _text(root, 'symbolic-ref', '-q', 'HEAD', ok=(0, 1))
    ref = ref if code == 0 else None
    try:
        digest = hashlib.sha256(Path(repo['index']).read_bytes()).hexdigest()
    except FileNotFoundError:
        digest = None
    git_dir = Path(repo['git_dir'])
    # A linked worktree keeps its own index/HEAD; branch refs live in the common dir.
    common = Path(repo.get('common_dir') or git_dir)
    candidates = [(git_dir, 'index.lock'), (git_dir, 'HEAD.lock')] + ([(common, ref + '.lock')] if ref else [])
    locks = [name for base, name in candidates if (base / name).exists()]
    # Worktree fingerprint: a checkout killed mid-unpack may have rewritten files
    # (and created new ones) before it ever updated the index or HEAD.
    _, status_raw = probe(root, 'status', '--porcelain=v1', '-z', '--untracked-files=normal')
    worktree = hashlib.sha256(status_raw).hexdigest()
    return {'head': head, 'ref': ref, 'index': digest, 'locks': locks, 'worktree': worktree}


def _blob_id(content):
    return hashlib.sha1(b'blob %d\0' % len(content) + content).hexdigest()


# ── Projection generation ──────────────────────────────────────────────────

def _read_projection(backlog, rel):
    path = safe_path(backlog, rel)
    try:
        info = check_component(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROJECTION_BYTES:
        raise UnsafePath(f'projection is not a bounded regular file: {rel}')
    content = path.read_bytes()
    if metrics.ENABLED:
        metrics.add('files_read')
        metrics.add('bytes_read', len(content))
    return content


def _variants(content):
    from taskmaster.native import projection
    return {projection._digest(content), projection._digest(projection._lf(content)),
            projection._digest(projection._crlf(content))}


def _same_text_digests(owner, backlog, rel):
    """D8: the disk file's digests when its text equals the trusted published bytes up to
    line endings (D7: Git's eol conversion writes a mixed-EOL generation in a uniform
    form, which sync accepts), else None. Read only for files no digest variant matches."""
    from . import checkouts, sync_files
    content = _read_projection(backlog, rel)
    if content is None:
        return None
    with closing(owner._connect(readonly=True)) as connection:
        base = checkouts.main_base(connection, rel)
    return sync_files.Digests.of(content) if checkouts.same_text(base, content) else None


def generation(owner, through, backlog=None):
    """The published generation and whether every file on disk still carries it.

    `through` is the barrier's synchronized target: a domain write committed after
    the writer pause was released is not part of this generation. `backlog` is the
    participating checkout's projection directory (a linked worktree carries the
    published bytes after its own synchronization).

    Every file is read in full (never judged by a cached fingerprint): the generation
    is what a commit records, and it is the last check before Git may overwrite a file.
    A write through a memory mapping moves no timestamp at all (measured on NTFS, the
    real ChangeTime included), so a fingerprint hit could let a commit record, or a
    checkout overwrite, bytes the store never published; `_verify_tree` would only catch
    the first after the commit. N16 measured the cost (3.1-6.2 s for 3,708 files) and kept
    it. The verified reads refresh the sync fingerprint cache."""
    from . import sync_files
    backlog = owner.root / '.taskmaster' if backlog is None else backlog
    scan = sync_files.open_scan(owner.root, backlog, fast=False)
    with closing(owner._connect(readonly=True)) as connection:
        connection.execute('BEGIN')
        rows = connection.execute("SELECT file,content_hash FROM projection WHERE file NOT LIKE 'local/%' "
                                  'ORDER BY file').fetchall()
        connection.rollback()
    mismatched, blobs = [], {}
    for rel, digest in rows:
        try:
            known = scan.digests(rel)
            if known is None:
                observed = scan.observe(rel, authored=False, limit=MAX_PROJECTION_BYTES)
                known = None if observed is None else sync_files.Digests.of(observed.content)
            if known is not None and digest not in known.variants:
                known = _same_text_digests(owner, backlog, rel)
        except (OSError, ValueError) as exc:
            mismatched.append(f'{rel}: {exc}')
            continue
        if known is None:
            mismatched.append(rel)
            continue
        # The Git blobs this generation may be committed as: the disk bytes exactly, or
        # LF-normalised. Both carry the generation's text (up to line endings), so
        # whatever eol conversion Git applies when staging, a foreign blob is caught.
        blobs[rel] = sorted({known.blob, known.blob_lf})
    sync_files.save_scan(owner.root, scan)
    value = hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()
    return ({'digest': value, 'files': len(rows), 'through': through, 'blobs': blobs},
            [rel for rel, _ in rows], mismatched)


def _head_tree(root):
    _, raw = probe(root, 'ls-tree', '-r', '-z', '--full-tree', 'HEAD', '--', '.taskmaster')
    tree = {}
    for entry in raw.split(b'\0'):
        if not entry:
            continue
        meta, _, path = entry.partition(b'\t')
        tree[path.decode('utf-8', 'surrogateescape')] = meta.split()[2].decode()
    return tree


def _verify_tree(owner, marker, root=None):
    """Committed .taskmaster blobs must be the recorded generation's blobs, never
    whatever is on disk at reconcile time. None when nothing was recorded."""
    blobs = (marker.get('generation') or {}).get('blobs')
    if blobs is None:
        return None
    tree = _head_tree(owner.root if root is None else root)
    return [rel for rel, ids in sorted(blobs.items()) if tree.get(f'.taskmaster/{rel}') not in ids]


def _head_records_generation(marker, root):
    """HEAD already holds what the commit would stage: every generation file as one of
    its blobs, and no tracked path the commit would stage as a deletion (the same rule
    as `_commands`: tracked, not in the generation, not local, gone from disk)."""
    generation = marker.get('generation') or {}
    blobs = generation.get('blobs')
    if blobs is None or len(blobs) != generation.get('files'):
        return False  # unrecorded, or a file whose blobs were not captured
    tree = _head_tree(root)
    if not all(tree.get(f'.taskmaster/{rel}') in ids for rel, ids in blobs.items()):
        return False
    wanted = {f'.taskmaster/{rel}' for rel in blobs}
    return not any(path not in wanted and not path.startswith('.taskmaster/local/') and not (Path(root) / path).exists()
                   for path in tree)


def _trailer_ops(root, commit):
    _, text = _text(root, 'log', '-1', f'--format=%(trailers:key={TRAILER},valueonly)', commit)
    return {line.strip() for line in text.splitlines() if line.strip()}


# ── Durable marker ─────────────────────────────────────────────────────────

def read_state(owner, key):
    with closing(owner._connect(readonly=True)) as connection:
        row = connection.execute('SELECT value_json FROM sync_state WHERE key=?', (key,)).fetchone()
    return None if row is None else json.loads(row[0])


def write_state(owner, **values):
    """Durable (synchronous=FULL) upsert/delete of `git.*` rows; the caller holds publication."""
    with closing(owner._connect()) as connection:
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('BEGIN IMMEDIATE')
        try:
            for name, value in values.items():
                key = {'marker': MARKER_KEY, 'last': LAST_KEY, 'drift': DRIFT_KEY, 'receipts': RECEIPTS_KEY,
                       'aside': ASIDE_KEY}[name]
                if value is None:
                    connection.execute('DELETE FROM sync_state WHERE key=?', (key,))
                else:
                    connection.execute('INSERT INTO sync_state(key,value_json) VALUES(?,?) ON CONFLICT(key) '
                                       'DO UPDATE SET value_json=excluded.value_json',
                                       (key, json.dumps(value, separators=(',', ':'))))
            connection.execute('COMMIT')
        except BaseException:
            connection.execute('ROLLBACK')
            raise


def _public(marker):
    if marker is None:
        return None
    public = {key: value for key, value in marker.items() if key not in ('token_hash',)}
    if isinstance(public.get('generation'), dict):
        public['generation'] = {key: value for key, value in public['generation'].items() if key != 'blobs'}
    return public


def _receipt(owner, request):
    """The settled result for this request, if any (bounded per-request receipts)."""
    last = read_state(owner, LAST_KEY)
    if last is not None and last.get('request') == request:
        return last
    for item in read_state(owner, RECEIPTS_KEY) or []:
        if item.get('request') == request:
            return item
    return None


def _settled(owner, last):
    """The rows that record a settled result: `git.last` plus the keyed receipt."""
    kept = [item for item in (read_state(owner, RECEIPTS_KEY) or []) if item.get('request') != last.get('request')]
    return {'last': last, 'receipts': (kept + [last])[-RECEIPTS_KEPT:]}


def status(owner):
    from taskmaster.native import checkouts as store
    from . import checkouts
    with closing(owner._connect(readonly=True)) as connection:
        known = {ident: {key: value for key, value in record.items() if key in ('path', 'linked', 'observed',
                                                                               'generation', 'intent')}
                 for ident, record in store.records(connection).items()}
        for ident, record in known.items():
            record['holds'] = {rel: reason for rel, (reason, _) in store.holds(connection, ident).items()}
        discarded = checkouts.discarded(connection)
    return {'pin': owner.git_pin, 'active': _public(read_state(owner, MARKER_KEY)),
            'last': read_state(owner, LAST_KEY), 'drift': read_state(owner, DRIFT_KEY),
            'checkouts': known, 'discarded': discarded, 'contained': jobs.supported(),
            'aside': read_state(owner, ASIDE_KEY)}


def _checkout_of(owner, marker):
    """The checkout a marker operated on; a replaced linked worktree is refused."""
    from . import checkouts
    recorded = marker.get('checkout') or {}
    if not recorded.get('linked'):
        return None
    checkout = checkouts.resolve(owner.root, recorded['path'])
    if checkout.id != recorded['id']:
        raise GitRefused(f"the linked checkout {recorded['path']} was replaced since the operation started")
    return checkout


# ── Checkout drift (step 9 adds per-checkout bases; until then drift is held) ──

def _observed_digest(backlog, rel):
    try:
        content = _read_projection(backlog, rel)
    except (OSError, UnsafePath):
        return 'unreadable'
    return None if content is None else hashlib.sha1(content).hexdigest()


def checkout_drift(owner):
    """{rel: observed sha1 | None (missing)} for every projection the checkout left
    differing from the published generation, including files only the checkout has."""
    from . import sync_files
    from . import checkouts
    backlog = owner.root / '.taskmaster'
    with closing(owner._connect(readonly=True)) as connection:
        published = dict(connection.execute(
            "SELECT file,content_hash FROM projection WHERE file NOT LIKE 'local/%'").fetchall())
        drift = {}
        for rel, digest in published.items():
            try:
                content = _read_projection(backlog, rel)
            except (OSError, UnsafePath):
                drift[rel] = 'unreadable'
                continue
            if content is None or (digest not in _variants(content)
                                   and not checkouts.same_text(checkouts.main_base(connection, rel), content)):
                drift[rel] = None if content is None else hashlib.sha1(content).hexdigest()
    for rel in sync_files.discover(backlog).files:
        if rel not in published and not rel.startswith('local/'):
            drift[rel] = _observed_digest(backlog, rel)
    return drift


def prune_drift(owner):
    """Drop drift entries whose file again carries the published bytes (or whose
    published record and file are both absent). Caller holds publication."""
    state = read_state(owner, DRIFT_KEY)
    if not state:
        return set()
    from . import checkouts
    files = dict(state.get('files') or {})
    backlog = owner.root / '.taskmaster'
    with closing(owner._connect(readonly=True)) as connection:
        for rel in list(files):
            row = connection.execute('SELECT content_hash FROM projection WHERE file=?', (rel,)).fetchone()
            try:
                content = _read_projection(backlog, rel)
            except (OSError, UnsafePath):
                continue
            if (row is None and content is None) or (row is not None and content is not None and (
                    row[0] in _variants(content) or checkouts.same_text(checkouts.main_base(connection, rel), content))):
                del files[rel]
    if files != state.get('files'):
        write_state(owner, drift=dict(state, files=files) if files else None)
        owner.export_needed.set()
    return set(files)


def drop_drift(owner, rels):
    state = read_state(owner, DRIFT_KEY)
    if not state:
        return
    files = {rel: value for rel, value in (state.get('files') or {}).items() if rel not in set(rels)}
    write_state(owner, drift=dict(state, files=files) if files else None)
    owner.export_needed.set()


# ── Managed checkout round trip (D6) ───────────────────────────────────────

ASIDE_DIR = 'taskmaster-aside'


def _held_drift(owner, checkout):
    """Paths held as checkout drift in the checkout operated on."""
    if not checkout.linked:
        return set((read_state(owner, DRIFT_KEY) or {}).get('files') or {})
    from taskmaster.native import checkouts as store
    with closing(owner._connect(readonly=True)) as connection:
        return {rel for rel, (reason, _) in store.holds(connection, checkout.id).items() if reason == 'drift'}


def _only_drift(synced, drift):
    """Whether a pre-sync is pending for held drift alone (the barrier itself completed)."""
    if not synced.get('captured') or synced.get('unresolved_omitted') or synced.get('notices_omitted'):
        return False
    if not set(synced.get('unresolved') or ()) <= drift:
        return False
    for notice in synced.get('notices') or ():
        if notice == 'sync pending: projection publication incomplete':
            continue  # every cause of it is an `export pending:` notice, each checked here
        if notice.startswith('sync pending: '):
            rel = notice.removeprefix('sync pending: ').partition(': ')[0]
        elif notice.startswith('export pending: '):
            rel = notice.removeprefix('export pending: ').partition(' ')[0]
        else:
            return False
        if rel not in drift:
            return False
    return True


def _status_entries(root):
    """{path: XY} of `.taskmaster` entries Git reports as changed or untracked."""
    _, raw = probe(root, '-c', 'status.renames=false', 'status', '--porcelain=v1', '-z', '--untracked-files=all',
                   '--', '.taskmaster')
    found = {}
    for entry in raw.split(b'\0'):
        if len(entry) > 3:
            found[entry[3:].decode('utf-8', 'surrogateescape')] = entry[:2].decode('ascii', 'replace')
    return found


def _aside_plan(owner, checkout, target_commit, op_id):
    """The store's own published files Git would refuse to replace when checking out
    `target_commit`: untracked files the target tracks, and worktree-only modifications
    of paths the target changes. Only bytes equal (up to line endings) to the trusted
    published bytes are set aside - they are reproducible from the store, so Git may
    replace them. Anything else (an authored edit, foreign bytes, staged changes) is left
    for Git to refuse. None when nothing blocks."""
    from . import checkouts
    entries = _status_entries(checkout.root)
    if not entries:
        return None
    target_tree = checkouts.tree_blobs(checkout, target_commit)
    head_tree = checkouts.tree_blobs(checkout, 'HEAD')
    with closing(owner._connect(readonly=True)) as connection:
        generation = checkouts.published(connection)
        files = {}
        for path, state in sorted(entries.items()):
            rel = _blocking(path, state, target_tree, head_tree)
            if rel is None:
                continue
            value, _, held = generation.get(rel, (None, None, 'unpublished'))
            content = checkouts.read(checkout.backlog, rel)
            if held or content in (None, checkouts.UNREADABLE):
                continue
            # Only bytes the store can reproduce are set aside (L2): a retained base, or a
            # derived index whose re-render carries the recorded digest. A disk file is
            # never its own authority.
            base = checkouts.reference_bytes(connection, checkout, rel)
            if base is None and checkouts.derived(rel):
                rendered = checkouts.render_derived(connection, owner.root / '.taskmaster', rel)
                base = rendered if rendered is not None and hashlib.sha1(rendered).hexdigest() == value else None
            if checkouts.same_text(base, content):  # equal to the reproducible bytes, up to line endings
                files[rel] = hashlib.sha1(content).hexdigest()
    if not files:
        return None
    return {'dir': str(Path(checkout.git_dir) / ASIDE_DIR / op_id), 'backlog': str(checkout.backlog),
            'files': files}


def _blocking(path, state, target_tree, head_tree):
    """The projection rel of a status entry Git would refuse to replace, else None."""
    if not path.startswith('.taskmaster/'):
        return None
    rel = path[len('.taskmaster/'):]
    if rel.startswith('local/'):
        return None
    untracked = state == '??' and rel in target_tree
    modified = state == ' M' and target_tree.get(rel) != head_tree.get(rel)
    return rel if untracked or modified else None


def _move_aside(backlog, aside, checkpoint=lambda stage: None):
    """Move each planned file aside, verifying the moved bytes. Returns None when every
    file moved verified; ('changed', rel) when a file changed since planning and was put
    back (or vanished); ('stuck', rel) when a changed file could not be put back because
    its path was written again - its bytes stay in the aside dir, never dropped.
    OSError propagates (sharing violations are retried first)."""
    from taskmaster.native import projection
    base = Path(aside['dir'])
    for rel, digest in sorted(aside['files'].items()):
        source = safe_path(backlog, rel)
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not projection._retry(lambda: projection._move(source, target)):
            return 'changed', rel
        checkpoint('git_aside_file_moved')
        if hashlib.sha1(target.read_bytes()).hexdigest() != digest:
            if projection._retry(lambda: projection._install(target, source)):
                return 'changed', rel
            return 'stuck', rel
    return None


def _settle_aside(aside, verified):
    """Settle one aside dir. A path left empty gets its aside bytes back; an occupied path
    drops the aside copy only when `verified(rel, content)` proves it the store's own
    bytes; anything else stays in the dir. Returns (restored, dropped, kept)."""
    from taskmaster.native import projection
    base, backlog = Path(aside['dir']), Path(aside['backlog'])
    names = aside.get('files')
    if names is None:  # every file in the dir (a stray dir, or a recorded entry being retried)
        names = sorted(path.relative_to(base).as_posix() for path in base.rglob('*') if path.is_file()) \
            if base.is_dir() else []
    restored, dropped, kept = [], [], []
    for rel in sorted(names):
        source = base / rel
        if not source.exists():
            continue  # never moved, or already settled
        try:
            target = safe_path(backlog, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            if projection._retry(lambda: projection._install(source, target)):
                restored.append(rel)
            elif verified(rel, source.read_bytes()) and projection._drop(source):
                dropped.append(rel)
            else:
                kept.append(rel)
        except (OSError, UnsafePath, ValueError):
            kept.append(rel)
    if base.is_dir():
        for directory in sorted((path for path in base.rglob('*') if path.is_dir()), key=lambda p: -len(p.parts)):
            try:
                directory.rmdir()
            except OSError:
                pass
        try:
            base.rmdir()
        except OSError:
            pass
    return restored, dropped, kept


def _aside_notices(base, restored, dropped, kept):
    notices = []
    if restored:
        notices.append(f'{len(restored)} published file(s) set aside for the checkout were put back: '
                       + ', '.join(restored[:_KEPT]))
    if dropped:
        notices.append(f'{len(dropped)} published file(s) set aside for the checkout were replaced by the checked-out '
                       'bytes (the store still holds them): ' + ', '.join(dropped[:_KEPT]))
    if kept:
        notices.append(f'{len(kept)} set-aside file(s) could not be settled and remain in {base} (their path holds '
                       'other bytes, or they could not be moved; see git_status aside): ' + ', '.join(kept[:_KEPT]))
    return notices


def _remember_unsettled(owner, entry):
    """Record one unsettled aside dir durably, or forget it once nothing in it is kept."""
    recorded = read_state(owner, ASIDE_KEY) or []
    entries = [item for item in recorded if item.get('dir') != entry['dir']]
    if entry['files']:
        entries.append(entry)
    if entries != recorded:
        write_state(owner, aside=entries or None)


def restore_asides(owner, marker):
    """Settle the files a managed checkout set aside: a path Git left empty gets its
    bytes back; where the path is occupied, the aside copy is dropped only when its
    digest is the planned one (the store's own published bytes). A copy whose bytes
    changed since planning is kept and recorded durably (`git.aside`). Returns notices."""
    aside = marker.get('aside')
    if not aside:
        return []
    planned = aside['files']
    restored, dropped, kept = _settle_aside(
        aside, lambda rel, content: hashlib.sha1(content).hexdigest() == planned.get(rel))
    _remember_unsettled(owner, {'op_id': marker['op_id'], 'dir': aside['dir'], 'backlog': aside['backlog'],
                                'files': {rel: planned.get(rel) for rel in kept}})
    return _aside_notices(aside['dir'], restored, dropped, kept)


def _store_bytes(owner):
    """verified(rel, content) for bytes with no plan: equal (up to line endings) to the
    retained published base, or to the re-render of a derived index."""
    from . import checkouts

    def verified(rel, content):
        with closing(owner._connect(readonly=True)) as connection:
            base = checkouts.main_base(connection, rel)
            if base is None and checkouts.derived(rel):
                base = checkouts.render_derived(connection, owner.root / '.taskmaster', rel)
        return checkouts.same_text(base, content)
    return verified


def sweep_asides(owner):
    """Startup, with no marker and before the exporter runs: settle the recorded unsettled
    aside dirs and any stray `taskmaster-aside/*` dir of a known checkout. A path left
    empty gets the bytes back; an occupied path drops the copy only when it is verified
    as the store's own bytes (the planned digest, or the published base); the rest is
    kept, recorded in `git.aside` and reported. Returns notices."""
    from taskmaster.native import checkouts as store
    candidates = {item['dir']: item for item in read_state(owner, ASIDE_KEY) or []}
    roots = []
    try:
        roots.append((Path(repository(owner.root)['git_dir']), owner.root / '.taskmaster'))
    except (GitRefused, OSError):
        pass
    try:
        with closing(owner._connect(readonly=True)) as connection:
            for record in store.records(connection).values():
                if record.get('linked') and record.get('git_dir') and record.get('path'):
                    roots.append((Path(record['git_dir']), Path(record['path']) / '.taskmaster'))
    except Exception as exc:  # noqa: BLE001 - a sweep never blocks startup
        LOG.warning('aside sweep could not list linked checkouts: %s', exc)
    for git_dir, backlog in roots:
        parent = git_dir / ASIDE_DIR
        if not parent.is_dir():
            continue
        for directory in sorted(parent.iterdir()):
            if directory.is_dir() and str(directory) not in candidates:
                candidates[str(directory)] = {'op_id': directory.name, 'dir': str(directory),
                                              'backlog': str(backlog), 'files': {}}
    notices, verified = [], _store_bytes(owner)
    for directory, entry in sorted(candidates.items()):
        planned = entry.get('files') or {}

        def check(rel, content, planned=planned):
            if planned.get(rel) and hashlib.sha1(content).hexdigest() == planned[rel]:
                return True
            return verified(rel, content)
        try:
            restored, dropped, kept = _settle_aside(dict(entry, files=None), check)
            _remember_unsettled(owner, {'op_id': entry.get('op_id'), 'dir': directory, 'backlog': entry['backlog'],
                                        'files': {rel: planned.get(rel) for rel in kept}})
        except Exception as exc:  # noqa: BLE001 - a sweep never blocks startup
            notices.append(f'aside dir {directory} could not be swept: {exc}'[:500])
            continue
        notices.extend(_aside_notices(directory, restored, dropped, kept))
    for notice in notices:
        LOG.warning('managed checkout aside sweep: %s', notice)
    return notices


# ── Operation ──────────────────────────────────────────────────────────────

def _request(caller_scope, request_id):
    if not all(isinstance(value, str) and 1 <= len(value) <= 256 for value in (caller_scope, request_id)):
        raise ValueError('managed Git requires caller_scope and request_id')
    return [caller_scope, request_id]


def _validate(kind, message, ref):
    if kind == 'commit':
        if not isinstance(message, str) or not message.strip() or len(message.encode('utf-8')) > MAX_MESSAGE \
                or '\0' in message:
            raise ValueError('commit requires a non-empty bounded message')
    elif kind == 'checkout':
        if (not isinstance(ref, str) or not 1 <= len(ref) <= 256 or ref.startswith('-')
                or any(ch in ref for ch in '\0\r\n')):
            raise ValueError('checkout requires a bounded ref that does not start with "-"')
    else:
        raise ValueError('managed Git kind must be commit or checkout')


# Retirement terminates the whole job, so nothing Git starts in the background
# (auto gc/maintenance, an fsmonitor daemon) may be mid-write when it does.
QUIET_GIT = ['-c', 'gc.auto=0', '-c', 'maintenance.auto=false', '-c', 'core.fsmonitor=false']


def _commands(owner, kind, files, message, ref, token, op_id, checkout_root=None):
    top = Path(owner.root if checkout_root is None else checkout_root)
    git, root = [executable(), *QUIET_GIT], str(top)
    env = environment({TOKEN_ENV: token})
    if kind == 'checkout':
        return [{'argv': [*git, 'checkout', '--quiet', ref, '--'], 'cwd': root, 'env': env}]
    _, raw = probe(top, 'ls-files', '-z', '--full-name', '--', '.taskmaster')
    tracked = {entry.decode('utf-8', 'surrogateescape') for entry in raw.split(b'\0') if entry}
    wanted = {f'.taskmaster/{rel}' for rel in files}
    # Tracked projection paths that the generation no longer contains (moves,
    # archives) are staged as deletions; local state is never staged.
    gone = {path for path in tracked - wanted
            if not path.startswith('.taskmaster/local/') and not (top / path).exists()}
    specs = base64.b64encode('\0'.join(f':(top,literal){path}' for path in sorted(wanted | gone))
                             .encode('utf-8')).decode('ascii')
    # `--only` with the same pathspec commits exactly the generation: whatever else
    # the user had staged stays staged and out of this commit. The trailer names
    # the operation so a concurrent commit is never mistaken for it.
    return [{'argv': [*git, 'add', '-A', '--pathspec-from-file=-', '--pathspec-file-nul'], 'cwd': root, 'env': env,
             'stdin_b64': specs},
            {'argv': [*git, 'commit', '--quiet', '--only', '--pathspec-from-file=-', '--pathspec-file-nul',
                      '-m', message, '--trailer', f'{TRAILER}: {op_id}'], 'cwd': root, 'env': env,
             'stdin_b64': specs}]


def _bounded_results(results):
    kept = []
    for item in results:
        kept.append({key: (value[-4096:] if isinstance(value, str) else value) for key, value in item.items()})
    return kept


def run(owner, *, kind, caller_scope, request_id, message=None, ref=None, timeout=GIT_TIMEOUT, worktree=None,
        sync_timeout=None):
    """`sync_timeout` bounds the pre-sync (protocol.SYNC_TIMEOUT by default); `timeout`
    bounds the Git child."""
    from .protocol import SYNC_TIMEOUT, validate_sync_timeout
    request = _request(caller_scope, request_id)
    _validate(kind, message, ref)
    sync_timeout = validate_sync_timeout(SYNC_TIMEOUT if sync_timeout is None else sync_timeout)
    if worktree is not None and (not isinstance(worktree, str) or not worktree):
        raise ValueError('worktree must be an absolute checkout path')
    if type(timeout) not in (int, float) or not 1 <= timeout <= 3600:
        raise ValueError('managed Git timeout must be 1..3600 seconds')
    active = owner.git_active
    if active is not None and active.get('request') == request:
        return {'state': 'in_progress', 'op_id': active['op_id']}
    settled = _receipt(owner, request)
    if settled is not None:
        return dict(settled, replayed=True)
    marker = read_state(owner, MARKER_KEY)
    if marker is not None and marker.get('request') == request:
        # This request is the unsettled operation: never start it again.
        return {'state': 'recovery_required', 'op_id': marker['op_id'], 'pin': owner.git_pin,
                'active': _public(marker)}
    if not owner.publication.acquire(timeout=PUBLICATION_TIMEOUT):
        return {'state': 'pending', 'reason': 'publisher busy' + (
            '; a managed Git operation is in progress' if owner.git_active else '')}
    try:
        return _run_held(owner, kind, request, message, ref, timeout, worktree, sync_timeout)
    finally:
        owner.publication.release()


def _refused(reason, **extra):
    return dict(extra, state='refused', reason=reason)


def _run_held(owner, kind, request, message, ref, timeout, worktree=None, sync_timeout=None):
    if owner.stopping.is_set():
        return _refused('coordinator stopping')
    if owner.git_pin is not None:
        return _refused('managed Git recovery required', pin=owner.git_pin)
    marker = read_state(owner, MARKER_KEY)
    if marker is not None:
        owner.git_pin = {'state': 'recovery_required', 'reason': 'unsettled managed Git marker'}
        return _refused('managed Git recovery required', active=_public(marker))
    settled = _receipt(owner, request)
    if settled is not None:
        return dict(settled, replayed=True)
    from . import checkouts
    try:
        # Step 9: a validated linked worktree of the same repository is managed like
        # the main checkout; a different repository is refused (common-root mismatch).
        checkout = checkouts.resolve(owner.root, worktree)
        root = checkout.root
        repo = repository(root)
        before = snapshot(root, repo)
        if before['locks']:
            return _refused('Git lock present; another Git process may be running', locks=before['locks'])
        if checkout.linked:
            with closing(owner._connect(readonly=True)) as connection:
                from taskmaster.native import checkouts as store
                held = {rel: reason for rel, (reason, _) in store.holds(connection, checkout.id).items()
                        if kind != 'checkout' or reason != 'drift'}
            if held:
                return _refused('the linked checkout has held projection paths; resolve them first',
                                paths=sorted(held)[:_KEPT])
        target = None
        if kind == 'checkout':
            code, commit = _text(root, 'rev-parse', '--verify', '-q', ref + '^{commit}', ok=(0, 1))
            if code:
                return _refused(f'unknown checkout target {ref!r}')
            _, full = _text(root, 'rev-parse', '--symbolic-full-name', ref, ok=(0, 1, 128))
            target = {'ref': ref, 'commit': commit, 'branch': full if full.startswith('refs/heads/') else None}
    except GitRefused as exc:
        return _refused(str(exc))
    op_id = uuid.uuid4().hex
    # One coherent generation: import external edits, then publish through the
    # captured target. Later domain writes stay pending for the next generation.
    synced = owner.sync(caller_scope=f'git:{request[0]}'[:256], request_id=f'{op_id}:pre',
                        import_files=True, through=0, files=None, take_file=False,
                        **({'worktree': str(root)} if checkout.linked else {}),
                        **({} if sync_timeout is None else {'timeout': sync_timeout}))
    # D6: a checkout may leave held drift behind. Held drift is Git's own bytes, never
    # import authority; Git itself refuses to overwrite anything it considers modified.
    drift = _held_drift(owner, checkout) if kind == 'checkout' else set()
    if synced.get('state') != 'synchronized' and not (drift and _only_drift(synced, drift)):
        return _refused('projections are not synchronized; resolve the listed paths first', sync=synced)
    gen, files, mismatched = generation(owner, synced.get('through', 0), checkout.backlog)
    mismatched = [rel for rel in mismatched if rel not in drift]
    if mismatched:
        return _refused('projection files differ from the published generation', paths=mismatched[:_KEPT],
                        paths_omitted=max(0, len(mismatched) - _KEPT))
    token = secrets.token_hex(32)
    try:
        commands = _commands(owner, kind, files, message, ref, token, op_id, checkout_root=root)
        aside = _aside_plan(owner, checkout, target['commit'], op_id) if kind == 'checkout' else None
        # Re-observed after the barrier published: reconciliation compares against
        # the state Git actually starts from, not the pre-publication tree.
        before = snapshot(root, repo)
        if before['locks']:
            return _refused('Git lock present; another Git process may be running', locks=before['locks'])
    except GitRefused as exc:
        return _refused(str(exc))
    marker = {'op_id': op_id, 'request': request, 'kind': kind, 'phase': 'prepared',
              'contained': jobs.supported(), 'platform': sys.platform,
              'job': jobs.new_name() if jobs.supported() else None,
              'identity': jobs.identity() if jobs.supported() else None,
              'token_hash': hashlib.sha256(token.encode()).hexdigest(), 'pre': before, 'generation': gen,
              'target': target, 'started': time.time(),
              'checkout': dict(checkout.public(), git_dir=str(checkout.git_dir))}
    if aside:
        marker['aside'] = aside
    write_state(owner, marker=marker)  # durable before any child exists (and before any aside)
    owner.git_active = marker
    try:
        owner.checkpoint('git_marker_written')
        if aside:
            try:
                moved = _move_aside(checkout.backlog, aside, owner.checkpoint)
            except OSError as exc:
                # Nothing was run: put back what moved and settle as refused in place.
                notices = restore_asides(owner, marker)
                write_state(owner, marker=None)
                return _refused(f'a published file could not be set aside for the checkout ({exc}); nothing was '
                                'run', notices=notices)
            if moved is not None and moved[0] == 'stuck':
                # Changed bytes sit in the aside dir and their path was written again:
                # never guess. The marker stays; recovery keeps and reports them.
                reason = (f'{moved[1]} changed while being set aside for the checkout and its path was written '
                          f"again; the changed bytes are kept in {aside['dir']}; nothing was run; run git recover")
                owner.git_pin = {'state': 'recovery_required', 'op_id': op_id, 'reason': reason}
                return {'state': 'recovery_required', 'op_id': op_id, 'reason': reason, 'active': _public(marker)}
            if moved is not None:
                notices = restore_asides(owner, marker)
                write_state(owner, marker=None)
                return _refused(f'{moved[1]} changed while being set aside for the checkout; nothing was run',
                                notices=notices)
            owner.checkpoint('git_aside_moved')
            try:
                marker['pre'] = snapshot(root, repo)  # the tree Git starts from
            except GitRefused as exc:
                notices = restore_asides(owner, marker)
                write_state(owner, marker=None)
                return _refused(str(exc), notices=notices)
            write_state(owner, marker=marker)
        return _execute(owner, marker, commands, timeout)
    except BaseException as exc:
        # Fail closed: the durable marker stays and publication is pinned.
        owner.git_pin = {'state': 'recovery_required', 'reason': f'managed Git interrupted: {exc}'[:500],
                         'op_id': op_id}
        raise
    finally:
        owner.git_active = None


def _execute(owner, marker, commands, timeout):
    child = ManagedChild(marker['job'] or 'posix', checkpoint=owner.checkpoint)
    results, done, permitted, quiet = [], None, False, False
    try:
        try:
            child.start()
            child.assign()
            marker['phase'] = 'launch'
            write_state(owner, marker=marker)  # recorded before permission can exist
            permitted = True
            child.permit(commands)
            done, results = child.results(timeout)
            owner.checkpoint('git_helper_done')
        finally:
            if child.process is not None:
                quiet = child.retire(RETIRE_TIMEOUT)
            else:
                quiet = True  # no helper was ever started
            child.close()
    except (OSError, ValueError) as exc:
        if not permitted and quiet:
            notices = restore_asides(owner, marker)  # nothing could have launched Git
            write_state(owner, marker=None)
            return _refused(f'managed Git could not start: {exc}', notices=notices)
        raise
    if not quiet:
        owner.git_pin = {'state': 'recovery_required', 'op_id': marker['op_id'],
                         'reason': 'managed Git boundary not proven empty after retirement'}
        return {'state': 'ambiguous', 'op_id': marker['op_id'], 'reason': owner.git_pin['reason'],
                'results': _bounded_results(results)}
    if not child.contained:
        # A helper's exit does not prove its hooks' background children stopped.
        # The outcome is previewed, but only an operator acknowledgement settles it.
        reason = ('no verified child-lifetime boundary on this platform; confirm no Git/hook process from this '
                  'operation remains, then run git recover with acknowledge_quiescent')
        owner.git_pin = {'state': 'recovery_required', 'op_id': marker['op_id'], 'reason': reason}
        try:
            preview = reconcile(owner, marker, results=results, done=done)
        except (GitRefused, OSError) as exc:
            preview = {'state': None, 'notices': [f'cannot inspect Git state: {exc}'[:500]]}
        return {'state': 'recovery_required', 'op_id': marker['op_id'], 'reason': reason,
                'outcome_preview': preview}
    marker['phase'] = 'quiesced'
    write_state(owner, marker=marker)  # proven: a crash from here on needs no acknowledgement
    owner.checkpoint('git_quiesced')
    report = reconcile(owner, marker, results=results, done=done)
    return settle(owner, marker, report)


def reconcile(owner, marker, *, results=None, done=None):
    """Classify what Git did, from Git state alone; results only annotate."""
    linked = _checkout_of(owner, marker)
    root = owner.root if linked is None else linked.root
    repo = repository(root)
    after = snapshot(root, repo)
    pre, kind = marker['pre'], marker['kind']
    report = {'state': None, 'op_id': marker['op_id'], 'kind': kind, 'request': marker['request'],
              'pre': pre, 'post': after, 'notices': []}
    if results is not None:
        report['results'] = _bounded_results(results)
        report['helper_done'] = bool(done)
    if after['locks']:
        # Only a commit may leave a harmless stale index.lock (its HEAD did not move).
        # A checkout holding index.lock was stopped mid-unpack: the tree is partly switched.
        stale_index = (kind == 'commit' and after['locks'] == ['index.lock'] and after['head'] == pre['head']
                       and after['ref'] == pre['ref'])
        if not stale_index:
            report['state'] = 'ambiguous'
            report['notices'].append(f"Git locks remain after quiescence: {', '.join(after['locks'])}")
            return report
        report['notices'].append('stale .git/index.lock left by the retired Git process; remove it after inspection')
    if kind == 'commit':
        if after['head'] == pre['head'] and after['ref'] == pre['ref']:
            if after['head'] and _head_records_generation(marker, root):
                # N1: HEAD already records exactly this generation - a successful no-op,
                # never an empty commit and never a failure.
                report.update(state='completed', no_changes=True, commit=None, generation_verified=True)
                report['notices'].append('nothing to commit: HEAD already records the published generation')
            else:
                report['state'] = 'failed'
        elif after['ref'] == pre['ref'] and after['head'] and _parents(root, after['head']) == (
                [pre['head']] if pre['head'] else []):
            report['commit'] = after['head']
            problems = _verify_tree(owner, marker, root)
            report['generation_verified'] = problems == [] if problems is not None else None
            if marker['op_id'] not in _trailer_ops(root, after['head']):
                report['state'] = 'ambiguous'
                report['notices'].append(f'HEAD moved by one commit that lacks this operation\'s {TRAILER} trailer '
                                         '(a concurrent commit?)')
            elif problems is None:
                report['state'] = 'ambiguous'
                report['notices'].append('no recorded generation blobs; the commit cannot be verified')
            elif problems:
                report['state'] = 'ambiguous'
                report['notices'].append('committed projection blobs differ from the recorded generation: '
                                         + ', '.join(problems[:_KEPT]))
            else:
                report['state'] = 'completed'
        else:
            report['state'] = 'ambiguous'
            report['notices'].append('HEAD moved but not by exactly one commit on the original branch')
    else:
        target = marker['target']
        on_target = after['head'] == target['commit'] and (target['branch'] is None or after['ref'] == target['branch'])
        if on_target and (after['head'], after['ref']) != (pre['head'], pre['ref']):
            report['state'] = 'completed'
        elif (after['head'], after['ref']) == (pre['head'], pre['ref']):
            unchanged = after['index'] == pre['index'] and (
                'worktree' not in pre or after.get('worktree') == pre['worktree'])
            if on_target:
                report['state'] = 'completed'  # already there: checkout was a no-op
            elif unchanged:
                report['state'] = 'failed'
            else:
                report['state'] = 'ambiguous'
                report['notices'].append('HEAD is unchanged but the index or working tree changed: the checkout '
                                         'may have stopped part-way; inspect before accepting')
        else:
            report['state'] = 'ambiguous'
            report['notices'].append('HEAD is neither the original nor the requested checkout target')
    if results is not None and report['state'] in ('completed', 'failed') and not report.get('no_changes'):
        codes = [item.get('returncode') for item in results]
        expected_ok = report['state'] == 'completed'
        if expected_ok != (bool(done) and all(code == 0 for code in codes)):
            report['notices'].append(f'Git exit status {codes} disagrees with repository state; state wins')
    return report


def _parents(root, commit):
    _, line = _text(root, 'rev-list', '--parents', '-n', '1', commit)
    return line.split()[1:]


def settle(owner, marker, report, *, recovered=False):
    """Clear the marker only for a reconciled completed/failed outcome."""
    from . import checkouts
    drift = {}
    linked = None
    marked_linked = bool((marker.get('checkout') or {}).get('linked'))
    if report['state'] in ('completed', 'failed'):
        report['notices'].extend(restore_asides(owner, marker))  # before drift is judged
    if report['state'] in ('completed', 'failed') and marked_linked:
        try:
            linked = _checkout_of(owner, marker)
        except (GitRefused, OSError, ValueError) as exc:
            report['notices'].append(f'linked checkout could not be observed after the operation: {exc}'[:500])
    if marked_linked and linked is None:
        pass  # never judge the main checkout by a linked operation's outcome
    elif report['state'] == 'completed' and marker['kind'] == 'checkout' and linked is not None:
        # A linked checkout holds its own drift; the main checkout is not affected.
        drift = checkouts.checkout_drift(owner, linked)
        report['drift'] = {'state': 'pending' if drift else 'clean', 'count': len(drift),
                           'paths': sorted(drift)[:200], 'checkout': linked.public()}
        if drift:
            report['notices'].append(f'{len(drift)} projection file(s) in {linked.root} differ from its base after '
                                     f'checkout; {checkouts.LINKED_DRIFT}')
        drift = {}
    elif report['state'] == 'completed' and marker['kind'] == 'checkout':
        # The checked-out bytes are drift, not rollback authority: record, never import.
        drift = checkout_drift(owner)
        report['drift'] = {'state': 'pending' if drift else 'clean', 'count': len(drift),
                           'paths': sorted(drift)[:200]}
        if drift:
            report['notices'].append(f'{len(drift)} projection file(s) differ from the published generation after '
                                     f'checkout; {DRIFT_GUIDANCE}')
    if report['state'] in ('completed', 'failed'):
        last = dict(report, recovered=recovered, settled=time.time())
        if marker['kind'] == 'checkout' and report['state'] == 'completed' and not marked_linked:
            state = {'op_id': marker['op_id'], 'target': marker.get('target'), 'files': drift}
            write_state(owner, marker=None, drift=state if drift else None, **_settled(owner, last))
        else:
            write_state(owner, marker=None, **_settled(owner, last))
        if linked is not None or not marked_linked:
            _remember(owner, linked)
        owner.git_pin = None
        owner.export_needed.set()
        return last
    marker['outcome'] = report
    write_state(owner, marker=marker)
    owner.git_pin = {'state': 'ambiguous', 'op_id': marker['op_id'],
                     'reason': 'managed Git outcome is ambiguous; inspect and run git recover with accept_outcome'}
    return report


def _remember(owner, linked):
    """Step 10: the HEAD a settled operation left is observed, so the next sync does
    not mistake a managed commit or checkout for a bypassed one."""
    from . import checkouts
    try:
        checkout = linked or checkouts.optional_main(owner.root)
        if checkout is not None:
            checkouts.remember(owner, checkout, checkouts.observe(checkout))
    except (GitRefused, OSError) as exc:
        LOG.warning('checkout observation not recorded: %s', exc)


# ── Recovery ───────────────────────────────────────────────────────────────

def recover(owner, *, acknowledge_quiescent=False, accept_outcome=False, release_drift=None, timeout=None,
            worktree=None):
    """Prove the recorded child boundary is over, reconcile, then (maybe) clear.

    `release_drift` is an explicit verb: `import` (ordinary sync imports the held bytes)
    or `take_published` (the held bytes, retained first, receive the published file)."""
    from . import checkouts
    if release_drift is False:
        release_drift = None
    if release_drift is not None and release_drift not in checkouts.RELEASE_MODES:
        raise ValueError("release_drift must be 'import' or 'take_published'")
    if worktree is not None and release_drift is None:
        raise ValueError('worktree applies only to release_drift')
    acquired = owner.publication.acquire(timeout=-1 if timeout is None else timeout)
    if not acquired:
        return {'state': 'pending', 'reason': 'publisher busy'}
    try:
        marker = read_state(owner, MARKER_KEY)
        linked = None
        if worktree is not None:
            try:
                linked = checkouts.resolve(owner.root, worktree)
            except GitRefused as exc:
                raise ValueError(str(exc)) from None
            linked = linked if linked.linked else None
        if linked is not None:
            # A linked checkout is a view: independent of any main-checkout marker.
            released, kept = checkouts.release(owner, linked, release_drift)
            return {'state': 'pending' if kept else 'clear', 'release': release_drift, 'released_drift': released,
                    'kept': kept, 'checkout': linked.public()}
        if marker is None:
            result = {'state': 'clear', 'checkout': {'id': 'main', 'path': str(owner.root), 'linked': False}}
            if release_drift == 'import':
                # Explicit: ordinary sync may now import (or repair) these paths. A derived
                # index has no authored content to import: it stays held until re-rendered.
                state = read_state(owner, DRIFT_KEY) or {}
                files = dict(state.get('files') or {})
                kept = {rel: checkouts.DERIVED_GUIDANCE for rel in files if checkouts.derived(rel)}
                released = sorted(rel for rel in files if rel not in kept)
                remaining = {rel: files[rel] for rel in kept}
                write_state(owner, drift=dict(state, files=remaining) if remaining else None)
                checkouts.release_main(owner, released)
                result.update(release=release_drift, released_drift=released, kept=kept,
                              state='pending' if kept else 'clear')
            elif release_drift == 'take_published':
                released, kept = checkouts.take_published_main(owner)
                result.update(release=release_drift, released_drift=released, kept=kept,
                              state='pending' if kept else 'clear')
            owner.git_pin = None
            owner.export_needed.set()
            result.update(last=read_state(owner, LAST_KEY), drift=read_state(owner, DRIFT_KEY))
            return result
        outcome = marker.get('outcome')
        if outcome is None:
            not_launched = marker['phase'] == 'prepared'
            proven, reason = _quiesce(owner, marker, acknowledge_quiescent)
            if not proven:
                if acknowledge_quiescent and accept_outcome:
                    return _accept_unreconciled(owner, marker, reason)
                owner.git_pin = {'state': 'recovery_required', 'op_id': marker['op_id'], 'reason': reason}
                return {'state': 'recovery_required', 'op_id': marker['op_id'], 'reason': reason,
                        'active': _public(marker)}
            owner.checkpoint('git_recovery_settling')
            if not_launched:
                # Permission is only sent after `launch` is durable, so Git never ran:
                # later repository changes are someone else's, not this outcome.
                return settle(owner, marker, _not_launched(marker), recovered=True)
            vanished = _vanished_linked(owner, marker)
            if vanished:
                # The boundary is proven empty and the linked worktree Git operated in no
                # longer exists: nothing is left to reconcile or to publish into.
                return settle(owner, marker, _vanished(marker, vanished), recovered=True)
            if marker['phase'] != 'quiesced':
                marker['phase'] = 'quiesced'
                write_state(owner, marker=marker)
            try:
                outcome = reconcile(owner, marker)
            except (GitRefused, OSError) as exc:
                reason = f'cannot inspect Git state: {exc}'[:500]
                if acknowledge_quiescent and accept_outcome:
                    return _accept_unreconciled(owner, marker, reason)
                owner.git_pin = {'state': 'recovery_required', 'op_id': marker['op_id'], 'reason': reason}
                return dict(owner.git_pin, state='recovery_required')
            if acknowledge_quiescent:
                outcome['notices'].append('boundary acknowledged by operator, not proven by the job')
        if outcome['state'] == 'ambiguous' and accept_outcome:
            outcome = dict(outcome, state='accepted', reconciled=True,
                           notices=[*outcome['notices'], 'ambiguous outcome accepted by operator'])
            return _release(owner, outcome, marker)
        return settle(owner, marker, outcome, recovered=True)
    finally:
        owner.publication.release()


def _release(owner, outcome, marker=None):
    if marker is not None:
        outcome = dict(outcome, notices=[*outcome.get('notices', []), *restore_asides(owner, marker)])
    last = dict(outcome, recovered=True, settled=time.time())
    write_state(owner, marker=None, **_settled(owner, last))
    owner.git_pin = None
    owner.export_needed.set()
    return last


def _not_launched(marker):
    return {'state': 'failed', 'op_id': marker['op_id'], 'kind': marker['kind'], 'request': marker['request'],
            'pre': marker['pre'], 'post': None,
            'notices': ['operation was not launched: permission is only sent after phase launch is durable']}


def _vanished_linked(owner, marker) -> str | None:
    """Why the linked worktree a marker operated in is gone (its admin directory was
    removed or replaced), or None (main checkout, or the worktree still exists)."""
    from . import checkouts
    from taskmaster.native import checkouts as store
    recorded = marker.get('checkout') or {}
    if not recorded.get('linked'):
        return None
    git_dir = recorded.get('git_dir')
    if git_dir is None:  # a marker written before git_dir was recorded
        with closing(owner._connect(readonly=True)) as connection:
            git_dir = (store.record(connection, recorded['id']) or {}).get('git_dir')
    try:
        return checkouts._replaced(checkouts.main(owner.root), recorded['id'], {'git_dir': git_dir})
    except (GitRefused, OSError):
        return None


def _vanished(marker, why):
    return {'state': 'failed', 'op_id': marker['op_id'], 'kind': marker['kind'], 'request': marker['request'],
            'pre': marker['pre'], 'post': None,
            'notices': [f"the linked worktree {(marker.get('checkout') or {}).get('path')} vanished ({why}) after "
                        'its managed Git boundary was proven empty; settled as failed without reconciliation. A '
                        'commit may still exist on its branch: inspect the branch before relying on it']}


def _accept_unreconciled(owner, marker, reason):
    """Operator path when the boundary or Git state cannot be inspected at all."""
    report = None
    try:
        report = reconcile(owner, marker)
    except (GitRefused, OSError):
        pass
    outcome = {'state': 'accepted', 'reconciled': False, 'op_id': marker['op_id'], 'kind': marker['kind'],
               'request': marker['request'], 'pre': marker['pre'], 'observed': report,
               'notices': [reason, 'boundary acknowledged and outcome accepted by operator; unreconciled: '
                                   'inspect HEAD, index and projections before relying on them']}
    LOG.warning('managed Git outcome accepted unreconciled: %s', reason)
    return _release(owner, outcome, marker)


def _quiesce(owner, marker, acknowledged):
    """(proven, reason). Only the recorded private job may prove the boundary."""
    if marker['phase'] == 'quiesced':
        return True, None
    if marker.get('contained') and jobs.supported() and marker.get('platform') == sys.platform:
        mismatch = _identity_mismatch(marker)
        if mismatch:
            return False, mismatch
        try:
            recorded = jobs.Job.open(marker['job'])
        except (OSError, ValueError) as exc:
            return False, f'recorded managed Git job is inaccessible: {exc}'[:500]
        if recorded is not None:
            with recorded:
                owner.checkpoint('git_recovery_retiring')
                try:
                    quiet = recorded.retire(RETIRE_TIMEOUT)
                except OSError as exc:
                    return False, f'recorded managed Git job could not be retired: {exc}'[:500]
                if not quiet:
                    return False, 'recorded managed Git job still has active processes after retirement'
                owner.checkpoint('git_recovery_quiesced')
            return True, None
        if marker['phase'] == 'prepared':
            return True, None  # permission is only ever sent after `launch` is durable
        if acknowledged:
            return True, None
        return False, ('managed Git job is gone but Git may have been launched; confirm no Git/hook process from '
                       'this operation remains, then run git recover with acknowledge_quiescent')
    if marker['phase'] == 'prepared':
        return True, None  # an uncontained helper exits on EOF before permission
    if acknowledged:
        return True, None
    return False, ('no verified child-lifetime boundary on this platform; confirm no Git/hook process from this '
                   'operation remains, then run git recover with acknowledge_quiescent')


def _identity_mismatch(marker):
    """A Local\\ job is only visible in its own logon session (and openable at its
    integrity level): recovering elsewhere would look like a missing job."""
    recorded = marker.get('identity')
    if not recorded:
        return None
    try:
        current = jobs.identity()
    except OSError as exc:
        return f'cannot determine this coordinator\'s logon session: {exc}'[:500]
    if current == recorded:
        return None
    return (f"recorded managed Git job belongs to logon session {recorded.get('session')} at integrity "
            f"{recorded.get('integrity')}; this coordinator runs in logon session {current['session']} at "
            f"integrity {current['integrity']}; run recovery from the original session")


def startup(owner):
    """Called before the exporter can publish: pin if a marker exists."""
    marker = read_state(owner, MARKER_KEY)
    if marker is not None:
        owner.git_pin = {'state': 'recovering', 'op_id': marker['op_id'],
                         'reason': 'recovering an interrupted managed Git operation'}
    else:
        # Nothing runs in the checkout yet: settle aside dirs a past operation left.
        try:
            sweep_asides(owner)
        except Exception:  # noqa: BLE001 - a sweep never blocks startup
            LOG.exception('managed checkout aside sweep failed')
    return marker is not None
