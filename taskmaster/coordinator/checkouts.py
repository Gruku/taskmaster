"""User intent: linked Git worktrees share the main checkout's authority but keep their own
observed bases, and Git operations that bypass the coordinator are detected on the next
sync as drift or reported, never imported as authority to roll the store back (N13 9-10).

Checkout identity comes from Git's per-worktree admin directory, never a path alone.
HEAD/ref observations persist per checkout; the movement since the last observation
decides whether a differing projection file is an authored edit or bytes Git put
there (an older or foreign generation). Linked checkouts are published only on
demand, by copying the main checkout's published generation with compare-and-swap.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import time

from taskmaster.native import checkouts as store
from taskmaster.native import projection
from taskmaster.projection_paths import UnsafePath

MAX_LINKED = 32
REFLOG_LIMIT = 256
PLAIN_COMMITS = ('commit:', 'commit (amend):', 'commit (initial):')
NO_BASE = ('no trusted base in this checkout for bytes that differ from the published generation; import them '
           'with sync take_file (worktree), or release them with git recover --release-drift --worktree to '
           'receive the published file')
LINKED_DRIFT = ('checkout drift: Git put bytes here that differ from this checkout\'s base; they are not imported '
                'or overwritten; restore them, take them with sync take_file, or release them with git recover '
                '--release-drift --worktree to receive the published file')


@dataclass(frozen=True)
class Checkout:
    id: str
    root: Path
    git_dir: Path
    common_dir: Path
    linked: bool

    @property
    def backlog(self) -> Path:
        return self.root / '.taskmaster'

    def public(self) -> dict:
        return {'id': self.id, 'path': str(self.root), 'linked': self.linked}


def _norm(path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _layout(path):
    from .git import GitRefused, _text
    try:
        _, out = _text(path, 'rev-parse', '--show-toplevel', '--absolute-git-dir', '--git-common-dir')
    except GitRefused as exc:
        raise GitRefused(f'not a Git checkout: {path}: {exc}') from None
    lines = out.splitlines()
    if len(lines) != 3:
        raise GitRefused(f'unexpected Git layout for {path}')
    common = Path(lines[2])
    if not common.is_absolute():
        common = Path(path) / common
    return Path(lines[0]), Path(lines[1]), common


def main(root) -> Checkout:
    """The coordinator root must be the main checkout: its own top level and git dir."""
    from .git import GitRefused
    top, git_dir, common = _layout(root)
    if _norm(top) != _norm(root):
        raise GitRefused('coordinator root is not the Git top level of its checkout')
    if _norm(git_dir) != _norm(common):
        raise GitRefused('coordinator root is a linked worktree; the store lives at the main checkout')
    return Checkout(store.MAIN, Path(root).resolve(), git_dir.resolve(), common.resolve(), False)


def optional_main(root) -> Checkout | None:
    """The main checkout, or None when the root is not a Git top level (no detection)."""
    from .git import GitRefused
    try:
        return main(root)
    except (GitRefused, OSError):
        return None


def resolve(root, worktree=None) -> Checkout:
    """The checkout an operation names: main by default, else a validated linked worktree."""
    from .git import GitRefused
    owner = main(root)
    if worktree is None:
        return owner
    if not isinstance(worktree, str) or not worktree or len(worktree) > 4096 or '\0' in worktree:
        raise ValueError('worktree must be an absolute checkout path')
    path = Path(worktree)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('worktree must be an absolute path to an existing checkout')
    if _norm(path) == _norm(owner.root):
        return owner
    top, git_dir, common = _layout(path)
    if _norm(common) != _norm(owner.common_dir):
        raise GitRefused(f'common-root mismatch: {path} belongs to Git repository {common}, not {owner.common_dir}')
    if _norm(top) != _norm(path):
        raise GitRefused(f'worktree must be the top level of its checkout ({top})')
    if _norm(git_dir.parent) != _norm(common / 'worktrees'):
        raise GitRefused(f'{path} is not a linked worktree of {owner.root}')
    info = os.stat(git_dir)
    key = f'{_norm(common)}\0{git_dir.name}\0{info.st_dev}:{info.st_ino}'
    ident = 'wt-' + hashlib.sha256(key.encode('utf-8', 'surrogateescape')).hexdigest()[:24]
    return Checkout(ident, top.resolve(), git_dir.resolve(), common.resolve(), True)


# ── Observations ───────────────────────────────────────────────────────────

def observe(checkout: Checkout) -> dict:
    from .git import _text
    code, head = _text(checkout.root, 'rev-parse', '--verify', '-q', 'HEAD^{commit}', ok=(0, 1))
    code_ref, ref = _text(checkout.root, 'symbolic-ref', '-q', 'HEAD', ok=(0, 1))
    return {'head': head if code == 0 else None, 'ref': ref if code_ref == 0 else None}


def _paths(raw: bytes) -> set[str]:
    return {entry.decode('utf-8', 'surrogateescape')[len('.taskmaster/'):] for entry in raw.split(b'\0')
            if entry.startswith(b'.taskmaster/')}


def movement(checkout: Checkout, old: str | None, new: str | None) -> tuple[str, set[str] | None, list[str]]:
    """(kind, projection paths Git changed between old and new or None if unknown, reflog actions).

    `none`: HEAD did not move. `commits`: every reflog action since `old` was a plain
    commit, which records the worktree without rewriting it. `rewrite`: anything else
    (checkout, switch, reset, merge, pull, rebase, cherry-pick), including an
    unreadable reflog. `unknown`: nothing was observed before."""
    from .git import probe
    if old is None or new is None:
        return ('none' if old == new else 'unknown'), None, []
    if old == new:
        return 'none', set(), []
    actions, found = [], False
    code, raw = probe(checkout.root, 'reflog', 'show', '--format=%H%x09%gs', '-n', str(REFLOG_LIMIT), 'HEAD',
                      ok=(0, 128))
    if code == 0:
        for line in raw.decode('utf-8', 'replace').splitlines():
            commit, _, subject = line.partition('\t')
            if commit == old:
                found = True
                break
            actions.append(subject)
    kind = 'commits' if found and actions and all(a.startswith(PLAIN_COMMITS) for a in actions) else 'rewrite'
    code, _ = probe(checkout.root, 'cat-file', '-e', f'{old}^{{commit}}', ok=(0, 1, 128))
    if code:
        return kind, None, actions[:20]
    _, raw = probe(checkout.root, 'diff', '--name-only', '-z', '--no-renames', old, new, '--', '.taskmaster')
    return kind, _paths(raw), actions[:20]


def tree_blobs(checkout: Checkout, rev: str) -> dict[str, str]:
    from .git import probe
    _, raw = probe(checkout.root, 'ls-tree', '-r', '-z', '--full-tree', rev, '--', '.taskmaster')
    blobs = {}
    for entry in raw.split(b'\0'):
        if not entry:
            continue
        meta, _, path = entry.partition(b'\t')
        name = path.decode('utf-8', 'surrogateescape')
        if name.startswith('.taskmaster/'):
            blobs[name[len('.taskmaster/'):]] = meta.split()[2].decode()
    return blobs


def _blobs_of(content: bytes) -> set[str]:
    from .git import _blob_id
    return {_blob_id(content), _blob_id(projection._lf(content))}


def classify(checkout: Checkout, differing: dict, previous: dict | None, current: dict | None,
             released: dict | None = None, base_bytes=None) -> tuple[dict, list[str]]:
    """({rel: reason} drift, warnings) for files that differ from the checkout's base.

    `differing` maps rel -> observed bytes (None = missing). A file is drift when Git
    rewrote this checkout and changed that path since the last observation (or the
    change is unknowable), or when it carries the HEAD blob although no plain commit
    introduced it (`checkout -- p`, `restore`, `stash`). Released digests are left to
    ordinary sync."""
    warnings = []
    if current is None or current.get('head') is None:
        return {}, warnings
    old = (previous or {}).get('head')
    kind, changed, actions = movement(checkout, old, current['head'])
    if kind == 'commits' and changed and base_bytes is not None:
        blobs = tree_blobs(checkout, current['head'])
        mixed = sorted(rel for rel in changed if rel in blobs and (base_bytes(rel) is None
                                                                   or blobs[rel] not in _blobs_of(base_bytes(rel))))
        if mixed:
            warnings.append(f"unmanaged commit(s) {old[:12]}..{current['head'][:12]} in {checkout.root} committed "
                            f"projection bytes that are not this checkout's published generation (possibly a mixed "
                            f"generation): {', '.join(mixed[:20])}")
    if not differing:
        return {}, warnings
    released = released or {}
    head_blobs = None
    drift = {}
    detail = f"{kind}: {'; '.join(actions[:3]) or 'no reflog'}"
    for rel, content in sorted(differing.items()):
        observed = None if content is None else store.digest(content)
        if rel in released and released[rel] == observed:
            continue
        if kind == 'rewrite' and (changed is None or rel in changed):
            drift[rel] = f'Git rewrote this checkout since the last sync ({detail})'
            continue
        if content is None:
            continue  # a missing file Git did not remove is repaired, not drift
        if kind == 'commits' and changed is not None and rel in changed:
            continue  # this checkout's own plain commit recorded these bytes
        if head_blobs is None:
            head_blobs = tree_blobs(checkout, current['head'])
        if head_blobs.get(rel) in _blobs_of(content):
            drift[rel] = 'bytes restored from HEAD (an older committed generation), not an authored edit'
    return drift, warnings


# ── Records ────────────────────────────────────────────────────────────────

def read_record(owner, checkout_id):
    with closing(owner._connect(readonly=True)) as connection:
        return store.record(connection, checkout_id)


def write(owner, apply):
    """One durable (synchronous=FULL) coordinator transaction over checkout rows."""
    with closing(owner._connect()) as connection:
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('BEGIN IMMEDIATE')
        try:
            store.ensure(connection)
            result = apply(connection)
            connection.execute('COMMIT')
            return result
        except BaseException:
            connection.execute('ROLLBACK')
            raise


def remember(owner, checkout: Checkout, observed: dict | None, **extra):
    """Persist the HEAD/ref observation (and identity) of a checkout."""
    def apply(connection):
        value = dict(store.record(connection, checkout.id) or {})
        value.update(path=str(checkout.root), git_dir=str(checkout.git_dir), linked=checkout.linked,
                     observed=dict(observed or {}, at=time.time()), **extra)
        store.put_record(connection, checkout.id, value)
    write(owner, apply)


def register(owner, checkout: Checkout) -> list[str]:
    """Admit a linked checkout, forgetting checkouts whose Git admin directory is gone."""
    notices = []

    def apply(connection):
        known = store.records(connection)
        for ident, value in known.items():
            if ident == store.MAIN or ident == checkout.id:
                continue
            git_dir = value.get('git_dir')
            if git_dir and not Path(git_dir).is_dir():
                store.forget(connection, ident)
                notices.append(f'forgot removed worktree {value.get("path")} ({ident}): its Git admin directory is gone')
        linked = [ident for ident, value in store.records(connection).items() if value.get('linked')]
        if checkout.id not in linked and len(linked) >= MAX_LINKED:
            raise ValueError(f'more than {MAX_LINKED} linked checkouts are registered; remove unused worktrees')
        if store.record(connection, checkout.id) is None:
            store.put_record(connection, checkout.id, {'path': str(checkout.root), 'git_dir': str(checkout.git_dir),
                                                       'linked': True, 'observed': None})
    write(owner, apply)
    return notices


# ── Linked publication ─────────────────────────────────────────────────────

def published(connection, main_backlog: Path | None = None) -> dict[str, tuple[str, bytes | None, str | None]]:
    """{rel: (digest, bytes or None if untrusted, hold reason or None)} of main's generation.

    The bytes are the retained base when it matches the record, else the main
    checkout's file when it carries exactly the recorded digest (a store adopted
    from legacy may lack a base for a derived file); otherwise untrusted."""
    rows = connection.execute("SELECT p.file,p.content_hash,b.content FROM projection p LEFT JOIN projection_base b "
                              "ON b.file=p.file WHERE p.file NOT LIKE 'local/%' AND p.content_hash!='' "
                              "ORDER BY p.file").fetchall()
    found = {}
    for rel, value, content in rows:
        content = None if content is None else bytes(content)
        trusted = content if content is not None and projection._digest(content) == value else None
        if trusted is None and main_backlog is not None:
            disk = read(main_backlog, rel)
            if disk not in (None, UNREADABLE) and projection._digest(disk) == value:
                trusted = disk
        found[rel] = (value, trusted, projection.held_file(connection, rel))
    return found


def _variants(content):
    return {projection._digest(content), projection._digest(projection._lf(content)),
            projection._digest(projection._crlf(content))}


UNREADABLE = b'\0unreadable'  # never equal to any base or published bytes


def read(backlog: Path, rel: str):
    from .git import _read_projection
    try:
        return _read_projection(backlog, rel)
    except (OSError, UnsafePath):
        return UNREADABLE


def recover_intent(owner, checkout: Checkout) -> list[str]:
    """Settle a linked publication a crash interrupted; returns what was found."""
    value = read_record(owner, checkout.id) or {}
    intent = value.get('intent')
    if not intent:
        return []
    notices, settled = [], {}
    for rel, target in sorted(intent['files'].items()):
        outcome = projection.recover_checkout_file(checkout.backlog, rel, intent['token'], target)
        if outcome == 'aside kept':
            notices.append(f'sync pending: {rel}: an interrupted publication into {checkout.root} left a set-aside '
                           'file that is not the store\'s; it was kept for inspection')
        current = read(checkout.backlog, rel)
        if target is None and current is None:
            settled[rel] = None
        elif target is not None and current is not None and target in _variants(current):
            settled[rel] = current

    def apply(connection):
        for rel, content in settled.items():
            store.set_base(connection, checkout.id, rel, content)
        record = dict(store.record(connection, checkout.id) or {})
        record.pop('intent', None)
        store.put_record(connection, checkout.id, record)
    write(owner, apply)
    notices.insert(0, f'interrupted publication into {checkout.root} detected and settled '
                      f'({len(settled)} of {len(intent["files"])} file(s) recorded as published)')
    return notices


def publish(owner, checkout: Checkout, through: int) -> list[str]:
    """Copy the main checkout's published generation into a linked checkout.

    Returns pending notices. Only files carrying this
    checkout's base (or absent) are replaced; a held, dirty or unbased file is named."""
    backlog = checkout.backlog
    if not os.path.lexists(backlog):
        backlog.mkdir()  # a branch without projections still receives the generation
    with closing(owner._connect(readonly=True)) as connection:
        generation = published(connection, owner.root / '.taskmaster')
        known = store.bases(connection, checkout.id)
        holding = store.holds(connection, checkout.id)
    pending, plan = [], {}
    for rel, (value, content, held) in generation.items():
        if held:
            pending.append(f'sync pending: {rel}: not published into {checkout.root}: main projection is {held}')
            continue
        if content is None:
            pending.append(f'sync pending: {rel}: not published into {checkout.root}: no trusted published bytes')
            continue
        if rel in holding:
            pending.append(f'sync pending: {rel}: {holding[rel][0]} in {checkout.root}; {LINKED_DRIFT}')
            continue
        current = read(backlog, rel)
        if current is not None and value in _variants(current):
            if known.get(rel) != store.digest(current):
                plan[rel] = ('record', current, None)
            continue
        expected = known.get(rel)
        if current is None:
            plan[rel] = ('write', content, None)
        elif expected is not None and expected in _variants(current):
            plan[rel] = ('write', content, expected)
        else:
            pending.append(f'sync pending: {rel}: {checkout.root} holds bytes that are not this checkout\'s base; '
                           f'never overwritten ({NO_BASE if expected is None else "unimported edit"})')
    for rel, expected in sorted(known.items()):
        if rel in generation or rel.startswith('local/'):
            continue
        current = read(backlog, rel)
        if current is None:
            plan[rel] = ('forget', None, None)
        elif expected in _variants(current) and rel not in holding:
            plan[rel] = ('remove', None, expected)
        else:
            pending.append(f'sync pending: {rel}: no longer published, but {checkout.root} holds changed bytes')
    writes = {rel: item for rel, item in plan.items() if item[0] in ('write', 'remove')}
    token = secrets.token_hex(8)
    if writes:
        intent = {'token': token, 'files': {rel: (None if item[1] is None else projection._digest(item[1]))
                                            for rel, item in writes.items()}}

        def begin(connection):
            record = dict(store.record(connection, checkout.id) or {})
            record['intent'] = intent
            store.put_record(connection, checkout.id, record)
        write(owner, begin)  # durable before any file is touched
    results = {}
    for rel, (action, content, expected) in sorted(plan.items()):
        if action in ('write', 'remove'):
            owner.checkpoint('checkout_publish')
            outcome = projection.publish_checkout_file(backlog, rel, content, expected, token)
            results[rel] = outcome
            if outcome not in ('published', 'removed', 'agrees'):
                pending.append(f'sync pending: {rel}: not published into {checkout.root} ({outcome}); never overwritten')

    def finish(connection):
        for rel, (action, content, _) in plan.items():
            if action == 'record':
                store.set_base(connection, checkout.id, rel, content)
            elif action == 'forget':
                store.set_base(connection, checkout.id, rel, None)
            elif results.get(rel) in ('published', 'agrees'):
                store.set_base(connection, checkout.id, rel, content)
            elif results.get(rel) == 'removed':
                store.set_base(connection, checkout.id, rel, None)
        record = dict(store.record(connection, checkout.id) or {})
        record.pop('intent', None)
        if not pending:
            rows = sorted((rel, item[0]) for rel, item in generation.items())
            record['generation'] = {'digest': hashlib.sha256(repr(rows).encode()).hexdigest(), 'through': through}
        store.put_record(connection, checkout.id, record)
    write(owner, finish)
    return pending


def release(owner, checkout: Checkout) -> list[str]:
    """Explicit: a linked checkout adopts the published generation for held/unbased paths.

    The observed bytes become the base (so the next publication replaces exactly
    them); a missing held path simply loses its hold and is written."""
    backlog = checkout.backlog
    with closing(owner._connect(readonly=True)) as connection:
        holding = store.holds(connection, checkout.id)
        known = store.bases(connection, checkout.id)
        generation = published(connection)
    targets = set(holding)
    for rel, (value, _, _) in generation.items():
        current = read(backlog, rel)
        if current is not None and rel not in known and value not in _variants(current):
            targets.add(rel)
    released = {}
    for rel in sorted(targets):
        current = read(backlog, rel)
        if current != UNREADABLE:
            released[rel] = current

    def apply(connection):
        for rel, content in released.items():
            store.set_hold(connection, checkout.id, rel, None)
            if content is not None:
                store.set_base(connection, checkout.id, rel, content)
    write(owner, apply)
    return sorted(released)


# ── Bypass detection (step 10) ─────────────────────────────────────────────

def _base_reader(owner, checkout: Checkout):
    def base_bytes(rel):
        with closing(owner._connect(readonly=True)) as connection:
            if checkout.linked:
                found = store.base(connection, checkout.id, rel)
                return None if found is None else found[1]
            row = connection.execute('SELECT p.content_hash,b.content FROM projection p JOIN projection_base b '
                                     'ON b.file=p.file WHERE p.file=?', (rel,)).fetchone()
        if row is None or row[1] is None or projection._digest(bytes(row[1])) != row[0]:
            return None
        return bytes(row[1])
    return base_bytes


def detect(owner, checkout: Checkout, selected, drift) -> tuple[dict, dict, list[str]]:
    """(current observation, {rel: reason} newly held as drift, warnings).

    Compares every selected file with this checkout's base; the files that differ
    are classified against the HEAD movement since the last observation. New drift
    is durable before anything is imported."""
    record = read_record(owner, checkout.id) or {}
    current = observe(checkout)
    backlog = checkout.backlog
    with closing(owner._connect(readonly=True)) as connection:
        generation = {rel: value for rel, (value, _, _) in published(connection).items()}
        # Quarantined and flagged main files are already held with their bytes kept.
        skipped = {row[0] for row in connection.execute('SELECT file FROM projection WHERE quarantined=1')}
        skipped.update(projection.flagged_files(connection))
        known = store.bases(connection, checkout.id) if checkout.linked else dict(generation)
    differing = {}
    for rel in selected:
        if rel in drift or rel.startswith('local/') or (not checkout.linked and rel in skipped):
            continue
        content = read(backlog, rel)
        if content == UNREADABLE:
            continue
        expected = known.get(rel)
        if expected is None:
            if content is None:
                continue
            if checkout.linked and generation.get(rel) in _variants(content):
                continue  # identical bytes establish this checkout's base
            differing[rel] = content
        elif content is None or expected not in _variants(content):
            differing[rel] = content
    released = record.get('released') or {}
    # A release covers exactly the bytes it saw, once: consumed when they are imported or gone.
    kept = {rel: value for rel, value in released.items() if rel in differing
            and (None if differing[rel] is None else store.digest(differing[rel])) == value}
    if kept != released:
        def consume(connection):
            value = dict(store.record(connection, checkout.id) or {})
            value['released'] = kept
            store.put_record(connection, checkout.id, value)
        write(owner, consume)
    found, warnings = classify(checkout, differing, record.get('observed'), current, kept,
                               base_bytes=_base_reader(owner, checkout))
    if found:
        hold(owner, checkout, {rel: differing.get(rel) for rel in found}, found)
        warnings.append(f'{len(found)} projection file(s) in {checkout.root} were put there by Git since the last '
                        f'sync and are held as drift, not imported: ' + ', '.join(sorted(found)[:20]))
    return current, found, warnings


def hold(owner, checkout: Checkout, contents: dict, reasons: dict) -> None:
    """Durably hold drift: main in `git.drift`, a linked checkout in its own holds."""
    if not checkout.linked:
        from . import git as managed_git
        state = dict(managed_git.read_state(owner, managed_git.DRIFT_KEY) or {'op_id': None, 'target': None})
        files = dict(state.get('files') or {})
        files.update({rel: None if content is None else store.digest(content) for rel, content in contents.items()})
        state.update(files=files, reasons=dict(state.get('reasons') or {}, **reasons))
        managed_git.write_state(owner, drift=state)
        return

    def apply(connection):
        for rel, content in contents.items():
            store.set_hold(connection, checkout.id, rel, 'drift', None if content is None else store.digest(content))
    write(owner, apply)


def prune_drift(owner, checkout: Checkout) -> set[str]:
    """Release linked drift holds whose file again carries the base (or published) bytes."""
    with closing(owner._connect(readonly=True)) as connection:
        holding = {rel: value for rel, value in store.holds(connection, checkout.id).items() if value[0] == 'drift'}
        known = store.bases(connection, checkout.id)
        generation = {rel: value for rel, (value, _, _) in published(connection).items()}
    cleared = []
    for rel in holding:
        content = read(checkout.backlog, rel)
        if content == UNREADABLE:
            continue
        expected = known.get(rel)
        if content is None:
            if expected is None and rel not in generation:
                cleared.append(rel)
        elif (expected is not None and expected in _variants(content)) or (
                expected is None and generation.get(rel) in _variants(content)):
            cleared.append(rel)
    if cleared:
        def apply(connection):
            for rel in cleared:
                store.set_hold(connection, checkout.id, rel, None)
        write(owner, apply)
    return set(holding) - set(cleared)


def release_main(owner, released: list[str]) -> None:
    """Record the digests an operator released in the main checkout: ordinary sync may
    import or repair exactly those bytes instead of re-detecting them as drift."""
    checkout = optional_main(owner.root)
    if checkout is None:
        return
    digests = {}
    for rel in released:
        content = read(checkout.backlog, rel)
        if content != UNREADABLE:
            digests[rel] = None if content is None else store.digest(content)

    def apply(connection):
        value = dict(store.record(connection, store.MAIN) or {})
        value['released'] = digests
        store.put_record(connection, store.MAIN, value)
    write(owner, apply)


def checkout_drift(owner, checkout: Checkout) -> dict:
    """After a managed checkout in a linked worktree: every path whose bytes differ from
    its base (including files only the new tree has, and missing ones) is held."""
    from . import sync_files
    with closing(owner._connect(readonly=True)) as connection:
        known = store.bases(connection, checkout.id)
        generation = {rel: value for rel, (value, _, _) in published(connection).items()}
    paths = set(known) | set(generation) | {rel for rel in sync_files.discover(checkout.backlog).files}
    contents = {}
    for rel in sorted(paths):
        if rel.startswith('local/'):
            continue
        content = read(checkout.backlog, rel)
        expected = known.get(rel, generation.get(rel) if rel not in known else None)
        if content is None:
            if expected is not None:
                contents[rel] = None
        elif content == UNREADABLE or expected is None or expected not in _variants(content):
            contents[rel] = None if content == UNREADABLE else content
    if contents:
        hold(owner, checkout, contents, {rel: 'managed checkout' for rel in contents})
    return {rel: None if content is None else store.digest(content) for rel, content in contents.items()}
