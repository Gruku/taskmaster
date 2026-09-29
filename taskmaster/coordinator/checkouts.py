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

import base64
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import time

from taskmaster.native import checkouts as store
from taskmaster.native import projection
from taskmaster.projection_parse import classify as classify_path
from taskmaster.projection_paths import UnsafePath

MAX_LINKED = 32
REFLOG_LIMIT = 256
HISTORY_LIMIT = 5000  # commits walked per chunk of paths when proving bytes are new
HISTORY_CHUNK = 100   # paths per `git log` (command-line length)
# Present while Git is part-way through an operation in a checkout's git dir.
IN_PROGRESS = ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'SQUASH_MSG', 'AUTO_MERGE', 'rebase-merge',
               'rebase-apply')
PLAIN_COMMITS = ('commit:', 'commit (amend):', 'commit (initial):')
RELEASE_MODES = ('import', 'take_published')
DISCARDED_KEY = 'git.discarded'  # bytes a take_published release let publication replace
DISCARDED_KEPT = 32
DISCARDED_BYTES = 256 * 1024
NO_BASE = ('no trusted base in this checkout for bytes that differ from the published generation; import them '
           'with sync take_file (worktree), or receive the published file with git recover --release-drift '
           'take-published --worktree W')
DERIVED_GUIDANCE = ('derived index: rendered from the store, never imported; re-render it with git recover '
                    '--release-drift take-published (its current bytes are retained)')
LINKED_DRIFT = ('checkout drift: Git put bytes here that differ from this checkout\'s base; they are not imported '
                'or overwritten; restore them, take them with sync take_file, or run git recover --release-drift '
                'import|take-published --worktree W')


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
    return Checkout(_identity(common, git_dir), top.resolve(), git_dir.resolve(), common.resolve(), True)


def _identity(common, git_dir) -> str:
    """A linked checkout's id: its admin directory (common dir, name, device:inode)."""
    info = os.stat(git_dir)
    key = f'{_norm(common)}\0{Path(git_dir).name}\0{info.st_dev}:{info.st_ino}'
    return 'wt-' + hashlib.sha256(key.encode('utf-8', 'surrogateescape')).hexdigest()[:24]


def _replaced(checkout: Checkout, ident: str, value: dict) -> str | None:
    """Why a recorded linked checkout no longer exists in Git, or None."""
    git_dir = value.get('git_dir')
    if not git_dir or not Path(git_dir).is_dir():
        return 'its Git admin directory is gone'
    try:
        if _identity(checkout.common_dir, git_dir) != ident:
            return 'its Git admin directory was replaced (worktree removed and re-added under the same name)'
    except OSError:
        return 'its Git admin directory is gone'
    return None


def derived(rel: str) -> bool:
    """A projection rendered wholly from other entities, with no authored content of its
    own (today only `ideas/IDEAS.md`): never import input, only ever re-rendered.
    Local state is not a projection at all."""
    if rel.startswith('local/'):
        return False
    try:
        classify_path(rel)
    except ValueError:
        return True
    return False


def render_derived(connection, backlog: Path, rel: str) -> bytes | None:
    """The store's current bytes for a derived file, line endings matched as the exporter
    matches them; None when the store would not write one. Uses its own read transaction."""
    from taskmaster.native_routing import projection as routing
    row = connection.execute('SELECT kind FROM projection WHERE file=?', (rel,)).fetchone()
    if row is None:
        return None
    own = not connection.in_transaction
    if own:
        connection.execute('BEGIN')
    try:
        text, target = routing.store_version(connection, backlog, row[0], None, rel)
        if text is None or target != rel:
            return None
        return routing._Render(connection, backlog)._matched(rel, text.encode('utf-8'))
    finally:
        if own:
            connection.rollback()


# ── Observations ───────────────────────────────────────────────────────────

def observe(checkout: Checkout) -> dict:
    from .git import _text
    code, head = _text(checkout.root, 'rev-parse', '--verify', '-q', 'HEAD^{commit}', ok=(0, 1))
    code_ref, ref = _text(checkout.root, 'symbolic-ref', '-q', 'HEAD', ok=(0, 1))
    return {'head': head if code == 0 else None, 'ref': ref if code_ref == 0 else None}


def _paths(raw: bytes) -> set[str]:
    return {entry.decode('utf-8', 'surrogateescape')[len('.taskmaster/'):] for entry in raw.split(b'\0')
            if entry.startswith(b'.taskmaster/')}


def movement(checkout: Checkout, old: str | None, new: str | None
             ) -> tuple[str, set[str] | None, list[str], set[str]]:
    """(kind, projection paths Git changed between old and new or None if unknown, reflog
    actions, commits the reflog recorded since old).

    `none`: HEAD did not move. `commits`: every reflog action since `old` was a plain
    commit, which records the worktree without rewriting it. `rewrite`: anything else
    (checkout, switch, reset, merge, pull, rebase, cherry-pick), including an
    unreadable reflog. `unknown`: nothing was observed before."""
    from .git import probe
    if old is None or new is None:
        return ('none' if old == new else 'unknown'), None, [], set()
    if old == new:
        return 'none', set(), [], set()
    actions, commits, found = [], set(), False
    code, raw = probe(checkout.root, 'reflog', 'show', '--format=%H%x09%gs', '-n', str(REFLOG_LIMIT), 'HEAD',
                      ok=(0, 128))
    if code == 0:
        for line in raw.decode('utf-8', 'replace').splitlines():
            commit, _, subject = line.partition('\t')
            if commit == old:
                found = True
                break
            actions.append(subject)
            commits.add(commit)
    kind = 'commits' if found and actions and all(a.startswith(PLAIN_COMMITS) for a in actions) else 'rewrite'
    code, _ = probe(checkout.root, 'cat-file', '-e', f'{old}^{{commit}}', ok=(0, 1, 128))
    if code:
        return kind, None, actions[:20], commits
    _, raw = probe(checkout.root, 'diff', '--name-only', '-z', '--no-renames', old, new, '--', '.taskmaster')
    return kind, _paths(raw), actions[:20], commits


def in_progress(checkout: Checkout) -> str | None:
    """Why Git is part-way through an operation in this checkout (its result is not an
    authored edit yet), or None."""
    from .git import probe
    found = [name for name in IN_PROGRESS if os.path.lexists(checkout.git_dir / name)]
    if found:
        return f"a Git operation is in progress ({', '.join(found)})"
    _, raw = probe(checkout.root, 'ls-files', '-u', '-z')
    return 'the index has unmerged entries' if raw else None


def history_blobs(checkout: Checkout, rels, exclude=frozenset()) -> tuple[set[str], bool]:
    """(blob ids these paths had in any commit Git still knows - every ref, the stash and
    every reflog - except the `exclude` commits, whether the bounded walk was complete).

    Bounded: at most HISTORY_LIMIT commits per chunk of paths; a walk that reaches the
    bound is incomplete and callers must treat unmatched bytes as unverifiable."""
    from .git import probe
    blobs, complete, names = set(), True, sorted(rels)
    for start in range(0, len(names), HISTORY_CHUNK):
        specs = [f':(literal).taskmaster/{rel}' for rel in names[start:start + HISTORY_CHUNK]]
        _, raw = probe(checkout.root, 'log', '--all', '--reflog', '--no-abbrev', '--raw', '--no-renames', '--root',
                       '-m', '--format=%x01%H', '-n', str(HISTORY_LIMIT), '--', *specs)
        seen, skip = set(), False
        for line in raw.decode('utf-8', 'replace').splitlines():
            if line.startswith('\x01'):
                seen.add(line[1:])
                skip = line[1:] in exclude
            elif line.startswith(':') and not skip:
                fields = line.partition('\t')[0].split()
                if len(fields) >= 4 and fields[3].strip('0'):
                    blobs.add(fields[3])
        complete = complete and len(seen) < HISTORY_LIMIT
    return blobs, complete


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


def _blobs_of(content) -> set[str]:
    """The Git blob ids of `content` as is and LF-normalised; `content` may be bytes or
    their `sync_files.Digests` (a full sync keeps digests, never every file's bytes)."""
    from .git import _blob_id
    if not isinstance(content, bytes):
        return {content.blob, content.blob_lf}
    return {_blob_id(content), _blob_id(projection._lf(content))}


def _digest_of(content) -> str | None:
    """sha1 of bytes, or of the bytes a `Digests` describes; None for a missing file."""
    if content is None:
        return None
    return store.digest(content) if isinstance(content, bytes) else content.digest


def classify(checkout: Checkout, differing: dict, previous: dict | None, current: dict | None,
             released: dict | None = None, base_bytes=None) -> tuple[dict, list[str]]:
    """({rel: reason} drift, warnings) for files that differ from the checkout's base.

    `differing` maps rel -> observed bytes or their `sync_files.Digests` (None = missing).
    A file is drift (held, never imported) when:
    - Git is part-way through an operation (merge/cherry-pick/revert/rebase/squash state
      or unmerged index entries): every differing file, missing ones included;
    - Git rewrote this checkout and changed that path since the last observation (or the
      change is unknowable);
    - its bytes equal a blob that path had in any commit Git still knows (refs, stash,
      reflogs) other than the plain commits made since the last observation: `checkout
      <rev> -- p`, `restore --source`, reverts, stash applies, and a hand revert to an
      earlier committed value. A bounded history walk that cannot finish holds too.
    Anything else is authored: bytes no earlier commit holds, including a plain commit
    of them. An unknown previous observation (first sync after upgrade) is judged by the
    same byte rules instead of holding everything. Released digests go to ordinary sync."""
    warnings = []
    if current is None or current.get('head') is None:
        return {}, warnings
    old = (previous or {}).get('head')
    kind, changed, actions, commits = movement(checkout, old, current['head'])
    if kind == 'commits' and changed and base_bytes is not None:
        blobs = tree_blobs(checkout, current['head'])
        mixed = sorted(rel for rel in changed if rel in blobs and (base_bytes(rel) is None
                                                                   or blobs[rel] not in _blobs_of(base_bytes(rel))))
        if mixed:
            warnings.append(f"unmanaged commit(s) {old[:12]}..{current['head'][:12]} in {checkout.root} committed "
                            f"projection bytes that are not this checkout's published generation (possibly a mixed "
                            f"generation): {', '.join(mixed[:20])}")
    released = released or {}
    candidates = {rel: content for rel, content in sorted(differing.items())
                  if not (rel in released and released[rel] == _digest_of(content))}
    if not candidates:
        return {}, warnings
    busy = in_progress(checkout)
    if busy:
        return {rel: f'{busy}; its result is not an authored edit' for rel in candidates}, warnings
    drift, check = {}, {}
    detail = f"{kind}: {'; '.join(actions[:3]) or 'no reflog'}"
    for rel, content in candidates.items():
        if kind == 'rewrite' and (changed is None or rel in changed):
            drift[rel] = f'Git rewrote this checkout since the last sync ({detail})'
        elif content is not None:
            check[rel] = content  # a missing file Git did not remove is repaired, not drift
    if check:
        # The plain commits since the last observation are this checkout's own; every
        # other commit Git knows is an earlier (or foreign) generation.
        known, complete = history_blobs(checkout, check, commits if kind == 'commits' else frozenset())
        for rel, content in check.items():
            if known & _blobs_of(content):
                drift[rel] = ('bytes equal an earlier committed version of this path (restored, reverted or '
                              'applied by Git), not an authored edit')
            elif not complete:
                drift[rel] = (f'history of this path exceeds {HISTORY_LIMIT} commits: these bytes cannot be '
                              'proven to be a new authored edit')
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
    """Admit a linked checkout, forgetting checkouts whose Git admin directory is gone or
    now belongs to another checkout (the id is recomputed from the directory itself)."""
    notices = []

    def apply(connection):
        known = store.records(connection)
        for ident, value in known.items():
            if ident == store.MAIN or ident == checkout.id or not value.get('linked'):
                continue
            why = _replaced(checkout, ident, value)
            if why:
                store.forget(connection, ident)
                notices.append(f'forgot removed worktree {value.get("path")} ({ident}): {why}')
        linked = [ident for ident, value in store.records(connection).items() if value.get('linked')]
        if checkout.id not in linked and len(linked) >= MAX_LINKED:
            raise ValueError(f'more than {MAX_LINKED} linked checkouts are registered; remove unused worktrees')
        if store.record(connection, checkout.id) is None:
            store.put_record(connection, checkout.id, {'path': str(checkout.root), 'git_dir': str(checkout.git_dir),
                                                       'linked': True, 'observed': None})
    write(owner, apply)
    return notices


# ── Linked publication ─────────────────────────────────────────────────────

def published_digests(connection) -> dict[str, str]:
    """{rel: recorded digest} of main's generation: `published` without loading any bytes."""
    return dict(connection.execute("SELECT file,content_hash FROM projection WHERE file NOT LIKE 'local/%' "
                                   "AND content_hash!='' ORDER BY file"))


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
            elif derived(rel):
                # A derived file is the store's render: rendering it again is trusted
                # exactly when it reproduces the recorded digest.
                rendered = render_derived(connection, main_backlog, rel)
                if rendered is not None and projection._digest(rendered) == value:
                    trusted = rendered
        found[rel] = (value, trusted, projection.held_file(connection, rel))
    return found


def _variants(content):
    return {projection._digest(content), projection._digest(projection._lf(content)),
            projection._digest(projection._crlf(content))}


def same_text(reference: bytes | None, content) -> bool:
    """`content` differs from `reference` at most by line-ending normalisation (D7).

    The store records published bytes verbatim, so an authored file that mixes CRLF
    with lone LF is a mixed base; Git's eol/autocrlf conversion then writes a uniform
    form no digest variant of the mixed bytes matches. Equal text is equal to the
    base: never drift, never an import."""
    return (isinstance(reference, bytes) and isinstance(content, bytes) and content != UNREADABLE
            and projection._lf(reference) == projection._lf(content))


def main_base(connection, rel: str) -> bytes | None:
    """The main checkout's trusted base bytes for `rel` (they carry the recorded digest)."""
    row = connection.execute('SELECT p.content_hash,b.content FROM projection p JOIN projection_base b '
                             'ON b.file=p.file WHERE p.file=?', (rel,)).fetchone()
    if row is None or row[1] is None or projection._digest(bytes(row[1])) != row[0]:
        return None
    return bytes(row[1])


def reference_bytes(connection, checkout, rel: str) -> bytes | None:
    """What a checkout's file is expected to equal: a linked checkout's own base, else
    (unbased, or the main checkout) the trusted published bytes."""
    if checkout is not None and checkout.linked:
        found = store.base(connection, checkout.id, rel)
        if found is not None:
            return found[1]
    return main_base(connection, rel)


UNREADABLE = b'\0unreadable'  # never equal to any base or published bytes
UNSEEN = 'unreadable'  # a classified digest no observed file can carry
MAX_READ = 64 * 1024 * 1024  # == git.MAX_PROJECTION_BYTES, the bound `read` applies


def read(backlog: Path, rel: str):
    from .git import _read_projection
    try:
        return _read_projection(backlog, rel)
    except (OSError, UnsafePath):
        return UNREADABLE


def _recorded(scan, rel, *, fresh=False):
    """Digests recorded under the file's unchanged fingerprint, or None (any doubt)."""
    if scan is None:
        return None
    try:
        return scan.digests(rel, fresh=fresh)
    except (OSError, ValueError):
        return None


def _scan_read(scan, rel):
    """`read` through a sync's scan: the same bytes/None/UNREADABLE answers, with the
    before/after identity checks of `observe` (a file changing mid-read is UNREADABLE)."""
    try:
        observed = scan.observe(rel, authored=False, limit=MAX_READ)
    except (OSError, ValueError):
        return UNREADABLE
    return None if observed is None else observed.content


def observed_read(backlog: Path, rel: str, scan=None):
    """`read` through sync_files, the one projection reader sync may use, with or
    without a scan (a named-file sync has none)."""
    from . import sync_files
    if scan is not None:
        return _scan_read(scan, rel)
    try:
        observed = sync_files._read_observed(backlog, rel, MAX_READ)
    except (OSError, ValueError):
        return UNREADABLE
    return None if observed is None else observed.content


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


def publish(owner, checkout: Checkout, through: int, *, scan=None) -> list[str]:
    """Copy the main checkout's published generation into a linked checkout.

    Returns pending notices. Only files carrying this
    checkout's base (or absent) are replaced; a held, dirty or unbased file is named.
    With the sync's `scan`, a file whose fresh fingerprint still carries recorded
    digests equal to both the generation and this checkout's base needs nothing and
    is not read; every write still compares the bytes on disk (compare-and-swap)."""
    backlog = checkout.backlog
    if not os.path.lexists(backlog):
        backlog.mkdir()  # a branch without projections still receives the generation
    with closing(owner._connect(readonly=True)) as connection:
        generation = published(connection, owner.root / '.taskmaster')
        known = store.bases(connection, checkout.id)
        holding = store.holds(connection, checkout.id)

    def base_bytes(rel):
        # Only for files that differ from their base digest (line-ending-only differences).
        with closing(owner._connect(readonly=True)) as connection:
            found = store.base(connection, checkout.id, rel)
        return None if found is None else found[1]
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
        hit = _recorded(scan, rel, fresh=True)
        if hit is not None and value in hit.variants:
            if known.get(rel) == hit.digest:
                continue
            if hit.digest == value:
                plan[rel] = ('record', content, None)  # the file holds exactly the trusted bytes
                continue
        current = read(backlog, rel)
        if current is not None and (value in _variants(current) or same_text(content, current)):
            if known.get(rel) != store.digest(current):
                plan[rel] = ('record', current, None)
            continue
        expected = known.get(rel)
        if current is None:
            plan[rel] = ('write', content, None)
        elif expected is not None and expected in _variants(current):
            plan[rel] = ('write', content, expected)
        elif expected is not None and same_text(base_bytes(rel), current):
            plan[rel] = ('write', content, store.digest(current))  # the base, up to line endings
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


def retain(connection, checkout_id: str, rel: str, content: bytes, why: str) -> None:
    """Keep bytes a release lets publication replace (newest DISCARDED_KEPT entries; a
    file above DISCARDED_BYTES keeps its digest and size only). Caller owns the transaction."""
    row = connection.execute('SELECT value_json FROM sync_state WHERE key=?', (DISCARDED_KEY,)).fetchone()
    kept = [] if row is None else json.loads(row[0])
    entry = {'checkout': checkout_id, 'file': rel, 'digest': store.digest(content), 'size': len(content),
             'at': time.time(), 'why': why}
    if len(content) <= DISCARDED_BYTES:
        entry['content_base64'] = base64.b64encode(content).decode('ascii')
    connection.execute('INSERT INTO sync_state(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET '
                       'value_json=excluded.value_json', (DISCARDED_KEY, json.dumps((kept + [entry])[-DISCARDED_KEPT:])))


def discarded(connection) -> list[dict]:
    row = connection.execute('SELECT value_json FROM sync_state WHERE key=?', (DISCARDED_KEY,)).fetchone()
    return [] if row is None else [{key: value for key, value in item.items() if key != 'content_base64'}
                                   for item in json.loads(row[0])]


def _changed(reason):
    return (f'bytes changed since they were held ({reason}); not released: import them with sync take_file, '
            'or restore the held bytes first')


def release(owner, checkout: Checkout, mode: str) -> tuple[list[str], dict[str, str]]:
    """Explicit release of a linked checkout's held and unbased paths: (released, kept).

    `take_published`: a held path whose bytes are still exactly the held digest (or is
    missing), and an unbased differing path, adopts the published generation: its bytes
    are retained, then become the base the next publication replaces by compare-and-swap.
    A held path whose bytes changed since the hold is kept (never silently discarded).
    `import`: the hold is dropped and the current digest recorded as released, so
    ordinary sync imports those bytes against this checkout's base; unbased paths need
    take_file."""
    backlog = checkout.backlog
    with closing(owner._connect(readonly=True)) as connection:
        holding = store.holds(connection, checkout.id)
        known = store.bases(connection, checkout.id)
        generation = published(connection)
    released, kept = {}, {}
    for rel, (reason, value) in sorted(holding.items()):
        current = read(backlog, rel)
        if current == UNREADABLE:
            kept[rel] = 'unreadable; not released'
        elif mode == 'import' and derived(rel):
            kept[rel] = DERIVED_GUIDANCE
        elif mode == 'take_published' and current is not None and store.digest(current) != value:
            kept[rel] = _changed(reason)
        else:
            released[rel] = current
    for rel, (value, _, _) in generation.items():
        if rel in holding or rel in known:
            continue
        current = read(backlog, rel)
        if current in (None, UNREADABLE) or value in _variants(current):
            continue
        if mode == 'import':
            kept[rel] = (DERIVED_GUIDANCE if derived(rel) else
                         'no base in this checkout to import against; import it with sync take_file')
        else:
            released[rel] = current

    def apply(connection):
        record = dict(store.record(connection, checkout.id) or {})
        for rel, content in released.items():
            store.set_hold(connection, checkout.id, rel, None)
            if mode == 'import':
                record.setdefault('released', {})[rel] = None if content is None else store.digest(content)
            elif content is not None:
                retain(connection, checkout.id, rel, content, 'released to receive the published file')
                store.set_base(connection, checkout.id, rel, content)
        if mode == 'import' and record:
            store.put_record(connection, checkout.id, record)
    write(owner, apply)
    return sorted(released), kept


def take_published_main(owner) -> tuple[list[str], dict[str, str]]:
    """Explicit: the main checkout's drift paths receive the published generation.

    Only a path still carrying exactly its held bytes (or missing) is replaced, by
    compare-and-swap after the bytes are retained; anything else stays held. A derived
    index (D5) is re-rendered from the store, whether it is held as drift or merely
    differs from its published bytes (a bypassed restore). Caller holds publication."""
    from . import git as managed_git
    backlog = owner.root / '.taskmaster'
    files = dict((managed_git.read_state(owner, managed_git.DRIFT_KEY) or {}).get('files') or {})
    rendered = {}
    with closing(owner._connect(readonly=True)) as connection:
        generation = published(connection, backlog)
        for rel, (value, _, reason) in generation.items():
            if not derived(rel) or (rel not in files and reason is not None):
                continue
            current = read(backlog, rel)
            if rel not in files and (current in (None, UNREADABLE) or value in _variants(current)):
                continue
            if rel not in files:
                files[rel] = None if current is None else store.digest(current)
            rendered[rel] = render_derived(connection, backlog, rel)
    kept, plan, stale = {}, {}, []
    for rel, value in sorted(files.items()):
        current = read(backlog, rel)
        if current == UNREADABLE:
            kept[rel] = 'unreadable; not released'
            continue
        observed = None if current is None else store.digest(current)
        if observed is not None and observed != value:
            kept[rel] = _changed('drift')
            continue
        target = generation.get(rel)
        if rel in rendered and target is not None and target[2] in (None, projection.DRIFT_REASON):
            if rendered[rel] is None:
                kept[rel] = 'the store renders no bytes for this derived file now; not released'
                continue
            if projection._digest(rendered[rel]) != target[0]:
                stale.append(rel)  # the exporter records the fresh render's digest
            plan[rel] = (current, observed, rendered[rel])
            continue
        if target is not None and (target[2] not in (None, projection.DRIFT_REASON) or target[1] is None):
            kept[rel] = f'no trusted published bytes ({target[2] or "untrusted"}); not released'
            continue
        plan[rel] = (current, observed, None if target is None else target[1])

    def keep(connection):
        for rel, (current, _, _) in plan.items():
            if current is not None:
                retain(connection, store.MAIN, rel, current, 'released to receive the published file')
    write(owner, keep)  # durable before any file is replaced
    released, token = [], secrets.token_hex(8)
    for rel, (_, observed, content) in sorted(plan.items()):
        outcome = projection.publish_checkout_file(backlog, rel, content, observed, token)
        if outcome in ('published', 'removed', 'agrees'):
            released.append(rel)
        else:
            kept[rel] = f'not replaced ({outcome}); still held'
    managed_git.drop_drift(owner, released)
    refresh = [rel for rel in stale if rel in released]
    if refresh:
        def mark(connection):
            for rel in refresh:
                connection.execute('UPDATE projection SET exported_seq=NULL WHERE file=?', (rel,))
        write(owner, mark)
        owner.export_needed.set()
    return released, kept


# ── Bypass detection (step 10) ─────────────────────────────────────────────

def _base_reader(owner, checkout: Checkout):
    def base_bytes(rel):
        with closing(owner._connect(readonly=True)) as connection:
            if checkout.linked:
                found = store.base(connection, checkout.id, rel)
                return None if found is None else found[1]
            return main_base(connection, rel)
    return base_bytes


class DetectInterrupted(Exception):
    """The classification ran out of the sync's budget, or the coordinator is stopping. Nothing
    was held or recorded; the files read so far are fingerprinted in the sync's scan, so a retry
    of the same sync continues where this pass stopped."""


def detect(owner, checkout: Checkout, selected, drift, *, scan=None, deadline=None) -> tuple[dict, dict, list[str], dict]:
    """(current observation, {rel: reason} newly held as drift, warnings, {rel: digest
    or None (missing) or UNSEEN} of the bytes classified).

    Compares every selected file with this checkout's base; the files that differ
    are classified against the HEAD movement since the last observation. New drift
    is durable before anything is imported. The caller imports a file only while it
    still carries exactly the classified bytes.

    With the sync's `scan`, a file whose fingerprint is unchanged since its digests
    were recorded is compared by those digests (the classified digest is that
    recorded digest); a file that differs, or any miss, is read in full.

    `deadline` (time.monotonic()) bounds the pass, and the coordinator's stopping signal ends
    it: either raises DetectInterrupted before anything is held."""
    from . import sync_files
    record = read_record(owner, checkout.id) or {}
    current = observe(checkout)
    backlog = checkout.backlog

    def reference(rel):
        # Read only for the few files whose digests differ (line-ending-only differences).
        with closing(owner._connect(readonly=True)) as connection:
            return reference_bytes(connection, checkout, rel)
    with closing(owner._connect(readonly=True)) as connection:
        # Digests only: a store's retained bases are never all in memory at once.
        generation = published_digests(connection)
        # Quarantined and flagged main files are already held with their bytes kept.
        skipped = {row[0] for row in connection.execute('SELECT file FROM projection WHERE quarantined=1')}
        skipped.update(projection.flagged_files(connection))
        known = store.bases(connection, checkout.id) if checkout.linked else dict(generation)
    differing, seen = {}, {}
    for rel in selected:
        if owner.stopping.is_set():
            raise DetectInterrupted('coordinator stopping')
        if deadline is not None and time.monotonic() >= deadline:
            raise DetectInterrupted('time budget exhausted')
        if rel in drift or rel.startswith('local/') or (not checkout.linked and rel in skipped):
            continue
        # A fresh lstat, taken after HEAD was observed: a file Git rewrote between
        # discovery and this observation must not be judged by discovery's lstat.
        hit = _recorded(scan, rel, fresh=True)
        if hit is not None:
            expected = known.get(rel)
            if (expected in hit.variants if expected is not None else
                    checkout.linked and generation.get(rel) in hit.variants):
                seen[rel] = hit.digest
                continue
        content = read(backlog, rel) if scan is None else _scan_read(scan, rel)
        if content == UNREADABLE:
            seen[rel] = UNSEEN
            continue
        seen[rel] = None if content is None else store.digest(content)
        expected = known.get(rel)
        # A differing file is kept as its digests, never its bytes: memory stays bounded
        # however many files a full sync classifies (N16 batched sync).
        if expected is None:
            if content is None:
                continue
            if checkout.linked and (generation.get(rel) in _variants(content)
                                    or same_text(reference(rel), content)):
                continue  # identical bytes (or text) establish this checkout's base
            differing[rel] = sync_files.Digests.of(content)
        elif content is None or (expected not in _variants(content) and not same_text(reference(rel), content)):
            differing[rel] = None if content is None else sync_files.Digests.of(content)
    released = record.get('released') or {}
    # A release covers exactly the bytes it saw, once: consumed when they are imported or gone.
    kept = {rel: value for rel, value in released.items() if rel in differing
            and _digest_of(differing[rel]) == value}
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
    return current, found, warnings, seen


def hold(owner, checkout: Checkout, contents: dict, reasons: dict) -> None:
    """Durably hold drift: main in `git.drift`, a linked checkout in its own holds.
    `contents` maps rel -> bytes, their `Digests`, or None (missing)."""
    if not checkout.linked:
        from . import git as managed_git
        state = dict(managed_git.read_state(owner, managed_git.DRIFT_KEY) or {'op_id': None, 'target': None})
        files = dict(state.get('files') or {})
        files.update({rel: _digest_of(content) for rel, content in contents.items()})
        state.update(files=files, reasons=dict(state.get('reasons') or {}, **reasons))
        managed_git.write_state(owner, drift=state)
        return

    def apply(connection):
        for rel, content in contents.items():
            store.set_hold(connection, checkout.id, rel, 'drift', _digest_of(content))
    write(owner, apply)


def prune_drift(owner, checkout: Checkout) -> set[str]:
    """Release linked drift holds whose file again carries the base (or published) bytes."""
    with closing(owner._connect(readonly=True)) as connection:
        holding = {rel: value for rel, value in store.holds(connection, checkout.id).items() if value[0] == 'drift'}
        known = store.bases(connection, checkout.id)
        generation = published_digests(connection)
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
        else:
            with closing(owner._connect(readonly=True)) as connection:
                if same_text(reference_bytes(connection, checkout, rel), content):
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
        with closing(owner._connect(readonly=True)) as connection:
            for rel in [rel for rel, content in contents.items() if content is not None]:
                if (rel in known or rel in generation) and same_text(reference_bytes(connection, checkout, rel),
                                                                     contents[rel]):
                    del contents[rel]
    if contents:
        hold(owner, checkout, contents, {rel: 'managed checkout' for rel in contents})
    return {rel: None if content is None else store.digest(content) for rel, content in contents.items()}
