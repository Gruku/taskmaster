# User intent: block a merge whose task has no fresh passing review-gate, decided
# from the SQLite store the server writes so a gate recorded seconds ago is
# visible here. Read-only, fail-open, and it never imports a backlog.
"""merge_gate_decide.py — Decision module for hooks/merge_gate.py.

Called by merge_gate.py as:
    python merge_gate_decide.py <SRC_BRANCH> [PROJECT_CWD]

PROJECT_CWD is optional and defaults to Path.cwd(). Production PreToolUse
hooks already inherit the user's project cwd, so the default is correct in
prod; the explicit argv is provided so callers (and tests) that launch the
hook from a different directory can point the resolver at the project root.

Prints one of:
    ALLOW
    BLOCK:<task-id>: <reason>. Run /taskmaster:review-gate.

The root is `taskmaster.root.resolve_root` — the same rule the server uses —
so a merge run inside a linked worktree is judged against the main checkout's
store. When `<root>/.taskmaster/local/store.db` exists every answer comes from
it; when it does not, the projection is parsed instead and the reason is
logged. A hook never imports a backlog into a store (spec §4.5, R10).

CARDINAL RULE: FAIL-OPEN.  Every uncertainty, error, or missing piece
prints "ALLOW".  The ENTIRE body is wrapped in a top-level try/except so a
Python exception can never cause a silent block.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# This script lives in hooks/; the taskmaster package is at the repo root one
# level up. Subprocess invocation puts hooks/ on sys.path, not the root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BUSY_TIMEOUT_SECONDS = 2.0
LOG_MAX_BYTES = 1024 * 1024
LOG_KEEP_BYTES = 512 * 1024

_TASK_BY_BRANCH_SQL = (
    "SELECT id, doc FROM entities"
    " WHERE kind='task' AND deleted=0 AND archived=0"
    "   AND json_extract(doc,'$.branch')=?"
)


def _log(root: Path, reason: str) -> None:
    """One line saying which source the decision came from. Never raises."""
    try:
        log = root / ".taskmaster" / "local" / "hook.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.time()} merge_gate_decide: {reason}\n")
        if log.stat().st_size > LOG_MAX_BYTES:
            log.write_bytes(log.read_bytes()[-LOG_KEEP_BYTES:])
    except Exception:
        pass


def _git_rev_parse(branch: str, cwd: Path) -> str | None:
    """Return the full SHA of branch tip, or None on any error."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", branch],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
        return None
    except Exception:
        return None


def _walk_up_for_backlog(start: Path) -> Path | None:
    current = Path(start).resolve()
    while True:
        if (current / ".taskmaster").is_dir():
            return current
        if current.parent == current:
            return None
        current = current.parent


def project_root(cwd: Path) -> Path | None:
    """The checkout whose `.taskmaster/` governs this merge, or None.

    `resolve_root` is the shared rule (`TASKMASTER_ROOT`, else the git common
    dir, else cwd); an explicitly pinned root that has no backlog is an answer,
    not a reason to go looking elsewhere.
    """
    try:
        from taskmaster.root import resolve_root

        resolution = resolve_root(Path(cwd))
        if (resolution.root / ".taskmaster").is_dir():
            return resolution.root
        if resolution.source in ("env", "explicit"):
            return None
    except Exception:
        pass
    return _walk_up_for_backlog(Path(cwd))


# ── Store path ──────────────────────────────────────────────────


def _connect_ro(db_file: Path) -> sqlite3.Connection:
    """Read-only handle: the gate must never write a row or hold a lock."""
    uri = Path(db_file).resolve().as_uri()
    try:
        return sqlite3.connect(uri + "?mode=ro", uri=True, timeout=BUSY_TIMEOUT_SECONDS)
    except sqlite3.OperationalError:
        # A WAL database cannot be opened `mode=ro` when its `-shm` is absent
        # and cannot be created (design spec 3.1). Failing open here would
        # disable the gate; `query_only` reads instead, and still writes nothing.
        con = sqlite3.connect(uri + "?mode=rw", uri=True, timeout=BUSY_TIMEOUT_SECONDS)
        con.execute("PRAGMA query_only=ON")
        return con


def _task_for_branch(con: sqlite3.Connection, src: str):
    try:
        rows = con.execute(_TASK_BY_BRANCH_SQL, (src,)).fetchall()
    except sqlite3.OperationalError:
        # An interpreter whose SQLite lacks JSON1 still has to decide.
        rows = [
            (ident, doc)
            for ident, doc in con.execute(
                "SELECT id, doc FROM entities"
                " WHERE kind='task' AND deleted=0 AND archived=0"
            )
            if json.loads(doc).get("branch") == src
        ]
    for ident, doc in rows:
        return ident, json.loads(doc)
    return None, None


def _policy_on(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT doc FROM entities WHERE kind='project' AND deleted=0 LIMIT 1"
    ).fetchone()
    if not row:
        return False
    manifest = json.loads(row[0])
    conventions = manifest.get("conventions") or {}
    policies = conventions.get("policies") or {}
    return bool(policies.get("review_gate_required_for_merge"))


def decide_from_store(db_file: Path, src: str, cwd: Path) -> str:
    con = _connect_ro(db_file)
    try:
        if not _policy_on(con):
            return "ALLOW"
        task_id, task = _task_for_branch(con, src)
    finally:
        con.close()
    if task is None:
        return "ALLOW"
    return _verdict(task_id, task, src, cwd)


# ── Verdict (shared by both sources) ────────────────────────────


def _verdict(task_id: str, task: dict, src: str, cwd: Path) -> str:
    if task.get("skip_merge_gate"):
        return "ALLOW"

    gates = task.get("gates") or {}
    rg = gates.get("review-gate") if isinstance(gates, dict) else None
    if not rg:
        return (
            f"BLOCK:{task_id}: no review-gate has been run on this branch. "
            "Run /taskmaster:review-gate."
        )

    verdict = rg.get("verdict", "")
    if verdict != "pass":
        return (
            f"BLOCK:{task_id}: review-gate verdict is '{verdict}' (not pass). "
            "Run /taskmaster:review-gate."
        )

    # Gate passed — check freshness.
    if task.get("merge_gate_freshness", "strict") == "any":
        return "ALLOW"

    gate_sha = rg.get("commit_sha", "")
    branch_tip = _git_rev_parse(src, cwd)
    if branch_tip is None:
        # Can't resolve tip (no git repo, detached, etc.) — fail-open
        return "ALLOW"
    if gate_sha == branch_tip:
        return "ALLOW"
    return (
        f"BLOCK:{task_id}: review-gate was run on "
        f"{gate_sha[:12] if gate_sha else '(unknown)'} "
        f"but branch tip is now {branch_tip[:12]}. "
        "Run /taskmaster:review-gate to re-validate. "
        "Set merge_gate_freshness: any to skip the SHA check."
    )


# ── Projection fallback (no store on disk yet) ──────────────────


def decide_from_files(project_root: Path, src: str, cwd: Path) -> str:
    """Today's YAML/markdown reader, kept for projects with no store yet."""
    try:
        from taskmaster.project import load_project_manifest

        manifest = load_project_manifest(project_root)
        if manifest is None:
            return "ALLOW"
        if not manifest.conventions.policies.review_gate_required_for_merge:
            return "ALLOW"
    except Exception:
        return "ALLOW"

    try:
        import yaml

        backlog_path = None
        for candidate in [
            project_root / ".taskmaster" / "backlog.yaml",
            project_root / ".claude" / "backlog.yaml",
            project_root / "backlog.yaml",
        ]:
            if candidate.is_file():
                backlog_path = candidate
                break

        if backlog_path is None:
            return "ALLOW"

        raw = yaml.safe_load(backlog_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return "ALLOW"

    try:
        tasks = []
        for epic in raw.get("epics", []):
            tasks.extend(epic.get("tasks") or [])
        if not tasks:
            # The v4 projection keeps the task index in tasks/<id>.md rather
            # than inline in backlog.yaml. Without this the gate found no task
            # and fell open on every v4 project.
            from taskmaster.taskmaster_v3 import (
                iter_task_files, read_task_file, task_v4_from_file,
            )
            for task_file in iter_task_files(backlog_path):
                frontmatter, body = read_task_file(task_file)
                tasks.append(task_v4_from_file(frontmatter, body.rstrip("\n")))

        task = next((t for t in tasks if t.get("branch") == src), None)
        if task is None:
            return "ALLOW"

        task_id = task.get("id", "unknown")
        # `gates` is a HEAVY field: on v3 it never lives inline in the slim
        # backlog.yaml index, only in the per-task file.
        from taskmaster.taskmaster_v3 import (
            SCHEMA_V3, detect_schema_version, read_task_file, task_file_path,
        )

        if detect_schema_version(raw) >= SCHEMA_V3:
            frontmatter, _body = read_task_file(task_file_path(backlog_path, task_id))
            if "gates" in frontmatter:
                task = dict(task)
                task["gates"] = frontmatter["gates"] or {}

        return _verdict(task_id, task, src, cwd)
    except Exception:
        return "ALLOW"


def decide(src: str, cwd: Path) -> str:
    """Core decision logic — returns the string to print (ALLOW or BLOCK:...).

    Fail-open: any exception or unexpected state returns ALLOW.
    """
    try:
        root = project_root(Path(cwd))
    except Exception:
        return "ALLOW"
    if root is None:
        return "ALLOW"

    db_file = root / ".taskmaster" / "local" / "store.db"
    if db_file.is_file():
        try:
            return decide_from_store(db_file, src, cwd)
        except Exception as exc:
            _log(root, f"store unreadable ({exc!r}); failing open")
            return "ALLOW"

    _log(root, f"no store at {db_file}; reading the projection instead")
    try:
        return decide_from_files(root, src, cwd)
    except Exception:
        return "ALLOW"


def main() -> None:
    # Top-level fail-open guard: any exception at all => ALLOW.
    try:
        if len(sys.argv) < 2:
            print("ALLOW")
            return
        src = sys.argv[1].strip()
        if not src:
            print("ALLOW")
            return
        # Optional argv[2] = project cwd; default to the real process cwd.
        # Production hooks inherit the user's project cwd, so Path.cwd() is
        # correct in prod.
        if len(sys.argv) >= 3 and sys.argv[2].strip():
            cwd = Path(sys.argv[2].strip())
        else:
            cwd = Path.cwd()
        print(decide(src, cwd))
    except Exception:
        print("ALLOW")


if __name__ == "__main__":
    main()
