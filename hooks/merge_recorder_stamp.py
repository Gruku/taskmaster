# User intent: after a merge succeeds, stamp the rung it reached onto the task,
# through the server's own recorder so the write is v3-correct — and against the
# checkout that owns the backlog, even when the merge ran in a linked worktree.
"""merge_recorder_stamp.py — Stamp module for hooks/merge_recorder.py.

Called by merge_recorder.py as:
    python merge_recorder_stamp.py <SRC_BRANCH> [PROJECT_CWD]

SRC_BRANCH: the source (feature) branch that was merged in.
PROJECT_CWD: optional; defaults to Path.cwd(). Production PostToolUse hooks
    inherit the user's project cwd so the default is correct in prod; the
    explicit argv lets the test harness point the resolver at the project root
    by setting subprocess cwd=<test project dir> (NOT an env-var seam).

CARDINAL RULE: NEVER BLOCKS. Wrap the ENTIRE body in try/except so a Python
exception can never propagate to the shell as a non-zero exit.  The shell hook
also has `|| true` around this call, but defence-in-depth is correct here.

PERSISTENCE: we delegate the actual write to backlog_server.backlog_record_merge.
That is the ONE v3-correct path — it splits the heavy `merge_status` field into
tasks/<id>.md, recomputes the slim `merge_gate_state` mirror, and persists
without reformatting the whole backlog.yaml.  Importing backlog_server is
acceptable here: PostToolUse fires only on a *successful* merge, so it is not
latency-critical (unlike merge_gate_decide.py, which must stay light).
backlog_server resolves its project root from `ROOT = Path(os.environ.get(
"TASKMASTER_ROOT", Path.cwd()))` at import — so running this script with
cwd=<project> (prod inherits it; tests pass cwd=) targets the right project.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# This script lives in hooks/; the taskmaster package is at the repo root one
# level up. Subprocess invocation puts hooks/ on sys.path, not the root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The store's own busy timeout, not the merge gate's short one. This hook runs
# after a *successful* merge and is not latency-critical, and a merge landing
# while another process holds the writer is ordinary: giving up after two
# seconds silently dropped the stamp, where the old path through
# `backlog_server._load()` waited the full thirty.
BUSY_TIMEOUT_SECONDS = 30.0
LOG_MAX_BYTES = 1024 * 1024
LOG_KEEP_BYTES = 512 * 1024

_TASK_BY_BRANCH_SQL = (
    "SELECT id, doc FROM entities"
    " WHERE kind='task' AND deleted=0 AND archived=0"
    "   AND json_extract(doc,'$.branch')=?"
    " ORDER BY id"
)


def _log(root: Path, reason: str) -> None:
    """One line saying why no stamp was written. Never raises."""
    try:
        log = root / ".taskmaster" / "local" / "hook.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.time()} merge_recorder_stamp: {reason}\n")
        if log.stat().st_size > LOG_MAX_BYTES:
            log.write_bytes(log.read_bytes()[-LOG_KEEP_BYTES:])
    except Exception:
        pass


def resolve_root(cwd: Path) -> Path | None:
    """The checkout whose `.taskmaster/` owns this merge, or None.

    `TASKMASTER_ROOT` wins, exactly as it does for the server; otherwise the
    shared resolver walks to the git common dir, which is what makes a merge
    run inside a linked worktree stamp the main checkout.
    """
    pinned = os.environ.get("TASKMASTER_ROOT")
    if pinned:
        root = Path(pinned)
        return root if (root / ".taskmaster").is_dir() else None
    try:
        from taskmaster.root import resolve_root as _resolve

        root = _resolve(Path(cwd)).root
    except Exception:
        return None
    return root if (root / ".taskmaster").is_dir() else None


def store_path(root: Path) -> Path:
    """`<root>/.taskmaster/local/store.db`, from the shared definition."""
    try:
        from taskmaster.root import db_path

        return db_path(root / ".taskmaster")
    except Exception:
        return root / ".taskmaster" / "local" / "store.db"


def task_id_for_branch(db_file: Path, src: str) -> str | None:
    """The id of the task whose `branch` is `src`, read without opening a store.

    A plain sqlite read, `query_only`, on a database that must already exist:
    `mode=rw` refuses to create one. Going through `backlog_server._load()` for
    this lookup is what let a PostToolUse hook bootstrap a whole project.
    """
    uri = Path(db_file).resolve().as_uri() + "?mode=rw"
    con = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_SECONDS)
    try:
        con.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)}")
        con.execute("PRAGMA query_only=ON")
        try:
            rows = con.execute(_TASK_BY_BRANCH_SQL, (src,)).fetchall()
        except sqlite3.OperationalError:
            # An interpreter whose SQLite lacks JSON1 still has to answer.
            rows = [
                (ident, doc)
                for ident, doc in con.execute(
                    "SELECT id, doc FROM entities"
                    " WHERE kind='task' AND deleted=0 AND archived=0 ORDER BY id"
                )
                if json.loads(doc).get("branch") == src
            ]
    finally:
        con.close()
    for ident, _doc in rows:
        return ident
    return None


def pin_root(cwd: Path) -> None:
    """Pin `TASKMASTER_ROOT` before `backlog_server` freezes its own at import.

    `backlog_server.ROOT` is read from the environment once, at import time, so
    the resolution has to happen first. `resolve_root` walks to the git common
    dir, which is what makes a merge run inside a linked worktree stamp the main
    checkout's backlog instead of silently finding none. An explicit
    `TASKMASTER_ROOT` already in the environment wins, exactly as it does for
    the server.
    """
    if os.environ.get("TASKMASTER_ROOT"):
        return
    try:
        from taskmaster.root import resolve_root

        root = resolve_root(Path(cwd)).root
    except Exception:
        return
    if (root / ".taskmaster").is_dir():
        os.environ["TASKMASTER_ROOT"] = str(root)


def _git(args: list[str], cwd: Path) -> str | None:
    """Run a git subcommand, return stripped stdout or None on any error."""
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0:
            return r.stdout.strip() or None
        return None
    except Exception:
        return None


def stamp(src: str, cwd: Path) -> None:
    """Core stamp logic — silently returns on any unexpected state.

    Delegates the write to backlog_server.backlog_record_merge so the v3
    storage split (heavy merge_status -> tasks/<id>.md) and merge_gate_state
    recompute happen via the canonical path.
    """
    pin_root(cwd)
    root = resolve_root(cwd)
    if root is None:
        return

    # Hooks never bootstrap (R10). Without this check the branch lookup below
    # went through `backlog_server._load()`, which opens the store — and on a
    # fresh clone that holds projection files but no `store.db` yet, opening it
    # *creates* the database, takes the writer mutex, imports the whole backlog
    # and can rewrite every projection file. That is a migration nobody asked
    # for, run off a merge, before the recorder has even found a matching task.
    db_file = store_path(root)
    if not db_file.is_file():
        _log(root, f"no store at {db_file}; not recording this merge")
        return

    # The lookup is a read, and it is answered by a read.
    try:
        tid = task_id_for_branch(db_file, src)
    except Exception as exc:
        _log(root, f"store unreadable ({exc!r}); not recording this merge")
        return
    if not tid:
        return

    try:
        from taskmaster import backlog_server as _bs
    except Exception:
        # Import failure -> fail safe: no stamp, never blocks.
        return

    # Determine current branch (the merge TARGET, post-merge HEAD).
    current = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if not current or current == "HEAD":
        return

    # Determine merge commit SHA.
    sha = _git(["rev-parse", "HEAD"], cwd)
    if not sha:
        return

    # Resolve rung: named ladder rung, or "branch:<name>" for untracked targets.
    ladder = _bs._resolved_merge_targets()
    rung = _bs._rung_for_branch(current, ladder) or f"branch:{current}"

    # Delegate to the canonical recorder (v3-correct heavy write + state recompute).
    _bs.backlog_record_merge(tid, rung, sha)


def main() -> None:
    # Top-level fail-open guard: any exception at all => silent exit 0.
    try:
        if len(sys.argv) < 2:
            return
        src = sys.argv[1].strip()
        if not src:
            return
        # Optional argv[2] = project cwd; default to the real process cwd.
        # Production hooks inherit the user's project cwd, so Path.cwd() is
        # correct in prod.  Tests drive this by setting subprocess cwd=<dir>.
        if len(sys.argv) >= 3 and sys.argv[2].strip():
            cwd = Path(sys.argv[2].strip())
        else:
            cwd = Path.cwd()
        stamp(src, cwd)
    except Exception:
        pass  # Never raises, never prints — PostToolUse advisory only


if __name__ == "__main__":
    main()
