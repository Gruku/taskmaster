# User intent: block a merge whose task has no fresh passing review-gate, decided
# from the SQLite store the server writes so a gate recorded seconds ago is
# visible here. Read-only and it never imports a backlog. Solo stays fail-open; in a
# team-server clone (server mode) only a fresh, whole server answer can allow, and
# anything else blocks (team-server-008).
"""merge_gate_decide.py — Decision module for hooks/merge_gate.py.

Called by merge_gate.py as:
    python merge_gate_decide.py <SRC_BRANCH> [PROJECT_CWD]

PROJECT_CWD is optional and defaults to Path.cwd(). Production PreToolUse
hooks already inherit the user's project cwd, so the default is correct in
prod; the explicit argv is provided so callers (and tests) that launch the
hook from a different directory can point the resolver at the project root.

Prints one of:
    ALLOW
    BLOCK:<task-id>: <reason>.

The root is `taskmaster.root.resolve_root` — the same rule the server uses —
so a merge run inside a linked worktree is judged against the main checkout's
store. When `<root>/.taskmaster/local/store.db` exists and can be read, every
answer comes from it; when it is absent or unreadable, the projection is
parsed instead and the reason is logged, so a broken store cannot quietly
disable the gate. A hook never imports a backlog into a store (spec §4.5, R10).

SOLO: FAIL-OPEN.  Every uncertainty, error, or missing piece prints "ALLOW".  The
ENTIRE body is wrapped in a top-level try/except so a Python exception can never
cause a silent block.

SERVER MODE (team-server-008): FAIL-CLOSED.  `decide(..., server=True)` allows only on a
verdict from a fresh, well-formed answer of the team server's `merge-gate` view; an
unreachable server, a malformed or cached answer, an unreadable tip, a missing root or a
Python exception all print `BLOCK:-: <text>` (the `-` marks "no verdict"; merge_gate.py
words those blocks with UNAVAILABLE_GUIDANCE).  The user's explicit approval (the
approval flow in merge_gate.py) is the only bypass.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# This script lives in hooks/; the taskmaster package is at the repo root one
# level up. Subprocess invocation puts hooks/ on sys.path, not the root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_command
from taskmaster.bounded_run import run_bounded
from taskmaster.native_routing import hook_reads

BUSY_TIMEOUT_SECONDS = 2.0
LOG_MAX_BYTES = 1024 * 1024
LOG_KEEP_BYTES = 512 * 1024
SERVER_BUDGET_SECONDS = 5.0
_STARTED = time.monotonic()      # the hook's own clock; merge_gate.py passes its own start to `decide`
TEAMMATE_NOTE = " This is a teammate's task; its review-gate still applies."


def _has_marker(root: Path) -> bool:
    """Whether `root` is a team-server clone (009's marker or latch); an unreadable one counts."""
    try:
        from taskmaster import server_marker
        return server_marker.server_mode(root) is not None
    except Exception:
        return True


# ── Server mode (team-server-007, fail-closed since team-server-008) ───────────────────────────────

BYPASS = "bypass explicitly"


def unavailable_text(kind: str, message: str = "", *, retry_after=None, origin: str = "", src: str = "",
                     ids=None, problem: str = "") -> str:
    """The text of a no-verdict block, one per failure kind (design D1)."""
    if kind == "unreachable":
        return f"Taskmaster server unreachable; retry or {BYPASS} ({message})."
    if kind == "busy":
        seconds = "a few" if retry_after is None else str(max(1, int(retry_after + 0.999)))
        return f"the Taskmaster server is rate limiting this key; retry in {seconds} s or {BYPASS}."
    if kind == "server":
        return f"the Taskmaster server could not read its store; retry or {BYPASS}."
    if kind == "bad-response":
        return f"the Taskmaster server's answer was unreadable ({message}); retry or {BYPASS}."
    if kind == "refused":
        return ("the Taskmaster server did not accept this key: it may have been revoked or rotated, or the "
                "server's key list was briefly unavailable. Retry; if the operator gave you a new key, run "
                f"taskmaster remote set-key; or {BYPASS}.")
    if kind == "no-key":
        return (f"no key for the Taskmaster server at {origin}: run taskmaster remote set-key --url <the "
                f"marker's url>, or {BYPASS}.")
    if kind == "config":
        return f"this clone's .taskmaster-server.json (or its latch in .git/) can't be read ({message}); fix it, or {BYPASS}."
    if kind == "version":
        return ("the Taskmaster server's read API is not one this plugin speaks; upgrade the taskmaster plugin, "
                f"or {BYPASS}.")
    if kind == "bad-request":
        return f"the Taskmaster server refused the merge gate's request ({message}); {BYPASS} and report it."
    if kind == "ambiguous":
        return (f"several tasks use branch {src} ({', '.join(ids or [])}) and none of them is yours, so the gate "
                "can't tell which review-gate applies; set the branch on the task you are merging "
                f"(backlog_update_task), or {BYPASS}.")
    if kind == "tip":
        return f"the tip of {src} can't be read in this clone; retry, or {BYPASS}."
    if kind == "ref":
        return f"the branch name {src!r} can't be sent to the Taskmaster server ({problem}); {BYPASS}."
    if kind == "unparsed":
        return (f"the merge gate can't tell with confidence which branch this command merges ({message}); run the "
                f"merge as a plain git merge <branch> command, or {BYPASS}.")
    if kind == "internal":
        return f"the merge gate failed ({message}); retry, or {BYPASS} and report it."
    return f"the merge gate got no usable answer ({kind}: {message}); retry or {BYPASS}."


def no_verdict(kind: str, log=None, **parts) -> str:
    """`BLOCK:-: <text>`: a block with no verdict behind it. The reason also goes to the server's hook log."""
    text = unavailable_text(kind, **parts)
    if log is not None:
        log(f"blocking the merge ({kind}): {text}")
    return f"BLOCK:-: {text}"


def on_remote_unavailable(kind: str, message: str, log, **parts) -> str:
    """The decision when the team server gave no usable answer: a block (team-server-008)."""
    return no_verdict(kind, log, message=message, **parts)


def decide_remote(root: Path, src: str, cwd: Path, *, started: float | None = None, tip_ref: str | None = None,
                  clone: str | None = None) -> str:
    """The verdict for a team-server clone: the server's `merge-gate` view, the branch tip read here.

    ALLOW needs a fresh server answer of the right shape; every other path returns a `BLOCK:-:` line."""
    from taskmaster.remote import hookside
    from taskmaster.remote.client import ReadUnavailable, hook_deadline

    started = _STARTED if started is None else started
    state = None

    def log(text):
        hookside.append_log(state, "merge_gate_decide", text)
    try:
        server = hookside.server_of(root)
        state = hookside.state_dir(server)
        problem = hook_reads.ref_problem(src)
        if problem is not None:
            return no_verdict("ref", log, src=src, problem=problem)
        session = hookside.connect(root, log=log)
        deadline = hook_deadline(SERVER_BUDGET_SECONDS, started=started)
        params = {"branch": src}
        if clone:
            params["clone"] = clone
        result = session.client.read("merge-gate", params, retry_busy=True, deadline=deadline)
    except ReadUnavailable as failure:
        origin = ""
        try:
            origin = hookside.server_of(root).origin
        except Exception:  # noqa: BLE001 -- only the no-key text wants it
            pass
        return no_verdict(failure.kind, log, message=failure.message, retry_after=failure.retry_after,
                          origin=origin)
    if result.source != "server":
        return no_verdict("bad-response", log, message="not a fresh server answer")
    data = result.data
    shape = hook_reads.check_merge_gate_view(data)
    if shape is not None:
        return no_verdict("bad-response", log, message=shape)
    if data.get("ambiguous"):
        return no_verdict("ambiguous", log, src=src, ids=data.get("candidates") or [])
    tip_timeout = max(0.5, deadline - time.monotonic())
    return decide_from_view(data, src, cwd, server=True, tip_ref=tip_ref, tip_timeout=tip_timeout, log=log)


def server_clone(cwd) -> bool:
    """Whether `cwd` is in a team-server clone (009's marker or latch). An unreadable one counts as one."""
    try:
        from taskmaster import server_marker
        from taskmaster.root import resolve_root
        root = resolve_root(Path(cwd)).root
    except Exception:  # noqa: BLE001
        return merge_command.marker_file_above(cwd)
    try:
        return server_marker.server_mode(root) is not None
    except Exception:  # noqa: BLE001 -- ServerConfigError and anything else: fail closed
        return True


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


def _git_rev_parse(branch: str, cwd: Path, timeout: float = 10) -> str | None:
    """Return the full SHA of branch tip, or None on any error."""
    try:
        result = run_bounded(["git", "rev-parse", branch],
                             cwd=str(cwd), timeout=timeout, text=True,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode == 0:
            return result.stdout.strip() or None
        return None
    except Exception:
        return None


def _walk_up_for_backlog(start: Path) -> Path | None:
    """Fallback walk for a plugin whose `taskmaster` package is missing."""
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
    dir, else the nearest ancestor holding a backlog); an explicitly pinned
    root that has no backlog is an answer, not a reason to go looking elsewhere.
    """
    try:
        from taskmaster.root import resolve_root

        resolution = resolve_root(Path(cwd))
        if (resolution.root / ".taskmaster").is_dir() or _has_marker(resolution.root):
            return resolution.root
        return None
    except Exception:
        return _walk_up_for_backlog(Path(cwd))


def _store_path(root: Path) -> Path:
    """`<root>/.taskmaster/local/store.db`, from the shared definition."""
    try:
        from taskmaster.root import db_path

        return db_path(root / ".taskmaster")
    except Exception:
        return root / ".taskmaster" / "local" / "store.db"


# ── Store path ──────────────────────────────────────────────────


def _connect_ro(db_file: Path) -> sqlite3.Connection:
    """A read-only connection inside an open read transaction (`hook_reads.connect_ro`)."""
    return hook_reads.connect_ro(db_file, BUSY_TIMEOUT_SECONDS)


def decide_from_store(db_file: Path, src: str, cwd: Path, *, tip_ref: str | None = None) -> str:
    con = _connect_ro(db_file)
    try:
        data = hook_reads.merge_gate_view(con, src)
    finally:
        con.close()
    return decide_from_view(data, src, cwd, tip_ref=tip_ref)


def decide_from_view(data: dict, src: str, cwd: Path, *, server: bool = False, tip_ref: str | None = None,
                     tip_timeout: float = 10, log=None) -> str:
    """The verdict from a `merge-gate` view, local or the team server's (v1 `data`).

    `server=True` (a team-server clone): an unreadable tip is a block, and a verdict on a task that isn't the
    caller's says so."""
    if not data.get("policy_required"):
        return "ALLOW"
    task = data.get("task")
    if task is None:
        return "ALLOW"
    verdict = _verdict(task["id"], task, src, cwd, strict=server, tip_ref=tip_ref, tip_timeout=tip_timeout, log=log)
    if server and verdict.startswith("BLOCK:") and not verdict.startswith("BLOCK:-:") and not task.get("mine"):
        verdict += TEAMMATE_NOTE
    return verdict


# ── Verdict (shared by both sources) ────────────────────────────


def _verdict(task_id: str, task: dict, src: str, cwd: Path, *, strict: bool = False, tip_ref: str | None = None,
             tip_timeout: float = 10, log=None) -> str:
    """`task` carries the view's gate fields (`hook_reads.gate_fields`).

    `strict` (server mode): a tip that can't be read blocks instead of allowing. `tip_ref` names the ref whose
    tip is compared (the branch as written in the command, when `src` is a normalized name of it)."""
    if task.get("skip_merge_gate"):
        return "ALLOW"

    rg = task.get("review_gate")
    if not rg:
        return (
            f"BLOCK:{task_id}: no review-gate has been recorded for this branch."
        )

    verdict = rg.get("verdict", "")
    if verdict != "pass":
        # Only an enum verdict is quoted: the view may come from a server that passes stored text through.
        shown = verdict if verdict in hook_reads.GATE_VERDICTS else "not a recorded verdict"
        return (
            f"BLOCK:{task_id}: the review-gate verdict is '{shown}', not pass."
        )

    # Gate passed — check freshness.
    if task.get("merge_gate_freshness", "strict") == "any":
        return "ALLOW"

    gate_sha = rg.get("commit_sha", "")
    branch_tip = _git_rev_parse(tip_ref or src, cwd, tip_timeout)
    if branch_tip is None:
        if strict:
            return no_verdict("tip", log, src=tip_ref or src)
        # Can't resolve tip (no git repo, detached, etc.) — fail-open
        return "ALLOW"
    if gate_sha == branch_tip:
        return "ALLOW"
    return (
        f"BLOCK:{task_id}: the review-gate passed at "
        f"{gate_sha[:12] if gate_sha else '(unknown)'}, "
        f"but the branch tip is now {branch_tip[:12]}, so it needs re-running."
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

        # `project_root` answers only for a root that holds `.taskmaster/`
        # (both its resolution and its walk-up fallback require the directory),
        # so the legacy `.claude/` and root-layout candidates this used to probe
        # were unreachable.
        backlog_path = project_root / ".taskmaster" / "backlog.yaml"
        if not backlog_path.is_file():
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

        return _verdict(task_id, hook_reads.gate_fields(task), src, cwd)
    except Exception:
        return "ALLOW"


def server_root(cwd: Path) -> Path | None:
    """The main checkout of the clone `cwd` is in (the shared resolution rule), or None."""
    from taskmaster.root import resolve_root
    return resolve_root(Path(cwd)).root


def _decide_server(src: str, cwd: Path, started: float | None, tip_ref: str | None) -> str:
    """Server mode: every way out that isn't a verdict from a fresh server answer is a block."""
    log = None
    try:
        try:
            root = server_root(Path(cwd))
            reason = "no root"
        except Exception as exc:  # noqa: BLE001
            root, reason = None, f"{type(exc).__name__}: {exc}"
        if root is None:
            return no_verdict("config", log, message=f"this clone's team-server root can't be found ({reason})")
        clone = None
        try:
            from taskmaster.remote import clone as clone_id
            clone = clone_id.clone_id(root)
        except Exception:  # noqa: BLE001 -- no clone module before team-server-011, or no clone id: none sent
            clone = None
        return decide_remote(root, src, cwd, started=started, tip_ref=tip_ref, clone=clone)
    except Exception as exc:  # noqa: BLE001
        return no_verdict("internal", log, message=type(exc).__name__)


def decide(src: str, cwd: Path, *, started: float | None = None, server: bool | None = None,
           tip_ref: str | None = None) -> str:
    """Core decision logic — returns the string to print (ALLOW or BLOCK:...).

    Solo fails open: any exception or unexpected state returns ALLOW. Server mode fails closed (see the module
    docstring). `server` is the caller's mode (merge_gate.py passes its own, so the two can't disagree); None
    works it out with `server_clone`.
    """
    if server is None:
        try:
            server = server_clone(Path(cwd))
        except Exception:  # noqa: BLE001
            server = True
    if server:
        return _decide_server(src, cwd, started, tip_ref)
    try:
        root = project_root(Path(cwd))
    except Exception:
        return "ALLOW"
    if root is None:
        return "ALLOW"
    if _has_marker(root):
        return _decide_server(src, cwd, started, tip_ref)

    db_file = _store_path(root)
    if db_file.is_file():
        try:
            return decide_from_store(db_file, src, cwd, tip_ref=tip_ref)
        except Exception as exc:
            # Once a store exists it is the only source of truth: the
            # projection is a lagging export of it, so a gate recorded seconds
            # ago may not have reached `tasks/<id>.md`. Reading the files here
            # can BLOCK a merge the store already cleared, from a stale failing
            # gate, and the hook cannot tell that state from a current one. A
            # gate that cannot read its own authority fails open, loudly.
            _log(root, f"store unreadable ({exc!r}); allowing the merge")
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
        tip_ref = sys.argv[3].strip() if len(sys.argv) >= 4 and sys.argv[3].strip() else None
        print(decide(src, cwd, tip_ref=tip_ref))
    except Exception:
        print("ALLOW")


if __name__ == "__main__":
    main()
