"""User intent: a packaged pre-commit validator users may call from their own hook,
so staged projections are either the managed generation or refused with guidance.

Taskmaster never installs or overwrites hooks. Usage inside a user's hook:
    uv run <plugin>/taskmaster_cli.py git-hook pre-commit
    (from a source checkout: python -m taskmaster.coordinator.git_hook pre-commit)
A marketplace-cache <plugin> path is versioned and vanishes on upgrade; resolve it when
the hook runs (docs/runbooks/release-packaging.md, "Wiring the pre-commit check").
Runs in the hook's own Git context (GIT_INDEX_FILE etc. are deliberately kept).
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

TOKEN_ENV = 'TASKMASTER_MANAGED_GIT'
# The installed plugin's own front door: `python -m` works only from a source checkout.
_CLI = f'uv run "{(Path(__file__).resolve().parents[2] / "taskmaster_cli.py").as_posix()}"'
GUIDANCE = ('Taskmaster projections (.taskmaster/) may only be committed as one coherent generation.\n'
            f'  Commit them with:  {_CLI} git commit -m "<message>"\n'
            '  Or leave them out: git restore --staged .taskmaster\n'
            f'  If a managed operation was interrupted: {_CLI} git status')
_KEPT = 20


def _git(root, *args, stdin=None):
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    completed = subprocess.run(['git', *args], cwd=root, input=stdin, capture_output=True, timeout=120, **flags)
    if completed.returncode:
        raise RuntimeError(f'git {args[0]} failed: {completed.stderr.decode("utf-8", "replace").strip()[:500]}')
    return completed.stdout


def _blob_id(content):
    return hashlib.sha1(b'blob %d\0' % len(content) + content).hexdigest()


def _variants(content):
    lf = content.replace(b'\r\n', b'\n')
    return {hashlib.sha1(value).hexdigest() for value in (content, lf, lf.replace(b'\n', b'\r\n'))}


def _same_text(base, content) -> bool:
    """D7/D8: equal to the trusted published bytes up to line endings (Git's eol
    conversion writes a mixed-EOL generation in a uniform form)."""
    return base is not None and base.replace(b'\r\n', b'\n') == content.replace(b'\r\n', b'\n')


def _published_bases(connection, published, staged) -> dict:
    """{rel: bytes} of the staged paths' retained published bases that carry the recorded digest."""
    bases = {}
    for path in staged:
        rel = path[len('.taskmaster/'):]
        row = connection.execute('SELECT content FROM projection_base WHERE file=?', (rel,)).fetchone()
        if row is not None and row[0] is not None and hashlib.sha1(bytes(row[0])).hexdigest() == published.get(rel):
            bases[rel] = bytes(row[0])
    return bases


def _same(left, right) -> bool:
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def _is_linked(root) -> bool:
    lines = _git(root, 'rev-parse', '--path-format=absolute', '--git-dir', '--git-common-dir').decode(
        'utf-8', 'surrogateescape').splitlines()
    return not _same(lines[0], lines[1])


def _main_worktree(root) -> Path | None:
    for line in _git(root, 'worktree', 'list', '--porcelain').decode('utf-8', 'surrogateescape').splitlines():
        if line.startswith('worktree '):
            return Path(line[len('worktree '):])
    return None


def check(root) -> tuple[bool, str]:
    root = Path(root)
    staged = [path.decode('utf-8', 'surrogateescape') for path in
              _git(root, 'diff', '--cached', '--name-only', '-z', '--no-renames', '--', '.taskmaster').split(b'\0')
              if path]
    staged = [path for path in staged if not path.startswith('.taskmaster/local/')]
    if not staged:
        return True, 'no projection files staged'
    # The main checkout (git dir == common dir; also submodules and --separate-git-dir)
    # holds its own store; a linked worktree's authority is the main worktree Git lists
    # first. Never "no native store" while a store exists that could not be resolved.
    local = root / '.taskmaster' / 'local' / 'store.db'
    try:
        main_root = _main_worktree(root) if _is_linked(root) else root
    except (RuntimeError, OSError, IndexError):
        main_root = None
    if main_root is None:
        return False, f'cannot resolve the main checkout of {root}; refusing projection staging\n{GUIDANCE}'
    store = main_root / '.taskmaster' / 'local' / 'store.db'
    if not store.exists():
        if local.exists() and not _same(main_root, root):
            return False, (f'cannot resolve the native store: {root} has one but its main checkout {main_root} '
                           f'does not; refusing projection staging\n{GUIDANCE}')
        return True, 'no native store'
    try:
        with closing(sqlite3.connect(store.as_uri() + '?mode=ro', uri=True, timeout=30)) as connection:
            connection.execute('PRAGMA query_only=ON')
            row = connection.execute("SELECT value_json FROM sync_state WHERE key='git.managed'").fetchone()
            published = dict(connection.execute(
                "SELECT file,content_hash FROM projection WHERE file NOT LIKE 'local/%'").fetchall())
            bases = _published_bases(connection, published, staged)
    except sqlite3.Error as exc:
        return False, f'cannot read the native store ({exc}); refusing projection staging\n{GUIDANCE}'
    marker = None if row is None else json.loads(row[0])
    token = os.environ.get(TOKEN_ENV, '')
    if not marker or not token or hashlib.sha256(token.encode()).hexdigest() != marker.get('token_hash'):
        return False, f'unmanaged staging of {len(staged)} projection file(s) refused\n{GUIDANCE}'
    listing = _git(root, 'ls-files', '-s', '-z', '--full-name', '--', '.taskmaster')
    wanted, blobs = set(staged), {}
    for entry in listing.split(b'\0'):
        if entry:
            meta, _, path = entry.partition(b'\t')
            name = path.decode('utf-8', 'surrogateescape')
            if name in wanted:
                blobs[name] = meta.split()[1].decode()
    stale = []
    for path in staged:
        rel = path[len('.taskmaster/'):]
        digest = published.get(rel)
        if path not in blobs:
            if digest is not None:
                stale.append(f'{path} (deleted but published)')
            continue
        try:
            content = (root / path).read_bytes()
        except OSError:
            content = None
        if (digest is None or content is None
                or (digest not in _variants(content) and not _same_text(bases.get(rel), content))
                or blobs[path] not in {_blob_id(content), _blob_id(content.replace(b'\r\n', b'\n'))}):
            stale.append(path)
    if stale:
        return False, ('staged projections differ from the managed generation: ' + ', '.join(stale[:_KEPT])
                       + f'\n{GUIDANCE}')
    return True, 'managed generation validated'


def main(argv=None, root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('hook', choices=['pre-commit'])
    parser.parse_args(argv)
    try:
        if root is None:
            root = _git(Path.cwd(), 'rev-parse', '--show-toplevel').decode('utf-8').strip()
        ok, reason = check(root)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        ok, reason = False, f'taskmaster pre-commit validation failed: {exc}\n{GUIDANCE}'
    if not ok:
        print(f'taskmaster: {reason}', file=sys.stderr)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
