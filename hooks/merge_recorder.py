#!/usr/bin/env python3
"""merge_recorder.py — PostToolUse (Bash). NEVER blocks (exit 0 always).

Python port of merge-recorder.sh (behavior-preserving; zero subprocess spawns
on the hot path — non-matching commands exit 0 having spawned nothing).

Stamps the reached merge rung on a task when a successful `git merge` is
detected.  Looks up the task whose branch == <SRC>, reads the post-merge
HEAD to determine the target branch, maps it to a ladder rung label (or
"branch:<name>" for untracked targets), then calls merge_recorder_stamp.py
to write merge_status into .taskmaster/backlog.yaml.

CARDINAL RULE: PostToolUse is advisory — exit code is IGNORED by the harness,
but we exit 0 explicitly on every path for defensive correctness.  On any
error or uncertainty, we silently do nothing and exit 0.

Source-branch parser:
  Duplicated from merge_gate.py (~10 lines) with a comment cross-link.
  Both hooks share the same strategy: strip everything up to and including
  "merge", walk tokens, last non-flag token = branch name.
  Cross-link: plugins/taskmaster/hooks/merge_gate.py parse_src_branch().
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Must be a `git` invocation reaching `merge` as a subcommand
# (same regex as merge_gate.py GIT_MERGE_RE)
GIT_MERGE_RE = re.compile(r"git(\s+(-[A-Za-z]\S*\s+\S+|--[a-z][a-z-]*))*\s+merge\b")

# Anonymous references: pure hex SHAs (≥7 hex), HEAD~, @{, *_HEAD
ANON_REF_RE = re.compile(
    r"^[0-9a-fA-F]{7,}$|^HEAD[~^]|^@\{|^FETCH_HEAD$|^ORIG_HEAD$|^MERGE_HEAD$"
)


# The in-process stamp imports the server, so the hook interpreter must carry the
# runtime the stamp's PEP 723 header declares (a test pins these to that header). A
# fastmcp 2.x or a Python 3.10 fails inside the stamp; anything short of this uses uv.
MIN_PYTHON = (3, 11)
FASTMCP_MIN, FASTMCP_BELOW = (3, 4), 4
PYDANTIC_MIN = 2
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
HOOK_SECONDS = 10  # hooks.json timeout for this hook; the host kills it after that
LOG_MAX_BYTES = 1024 * 1024


def _release(text):
    parts = re.findall(r"\d+", text or "")[:2]
    if not parts:
        return None
    return tuple(int(p) for p in parts) + (0,) * (2 - len(parts))


def _installed(dist):
    from importlib import metadata
    try:
        return metadata.version(dist)
    except Exception:
        return None


def can_run_in_process(python=None, version_of=None) -> bool:
    """Whether this interpreter can run the stamp itself. `version_of(dist)` returns an
    installed distribution's version or None (default: importlib.metadata)."""
    python = tuple(python or sys.version_info[:2])
    version_of = version_of or _installed
    if python < MIN_PYTHON:
        return False
    fastmcp, pydantic = _release(version_of("fastmcp")), _release(version_of("pydantic"))
    if fastmcp is None or fastmcp < FASTMCP_MIN or fastmcp[0] >= FASTMCP_BELOW:
        return False
    if pydantic is None or pydantic[0] < PYDANTIC_MIN:
        return False
    return all(version_of(dist) is not None for dist in ("pyyaml", "httpx"))


def stamp_command(stamp_script: Path, stamp_args: list, *, in_process: bool | None = None,
                  uv: str | None = "") -> list | None:
    """argv for the stamp: this interpreter when it can run it, else the stamp's own
    `uv run --script` environment (its PEP 723 header); None if neither."""
    if in_process is None:
        in_process = can_run_in_process()
    if in_process:
        return [sys.executable or "python", str(stamp_script), *stamp_args]
    if uv == "":
        uv = shutil.which("uv")
    return [uv, "run", "--script", str(stamp_script), *stamp_args] if uv else None


def run_stamp(argv: list, *, detach: bool, log: Path | None = None) -> None:
    """Run the stamp. A `uv run` stamp may first build its environment (seconds), so it
    is started detached to keep the hook inside its hooks.json timeout, with its output
    appended to hook.log. On Windows it breaks away from the host's job when the job
    allows it, so a kill-on-close job ending with the session does not kill it."""
    if not detach:
        # The stamp records the current merge first and budgets its replay inside this.
        subprocess.run(argv, capture_output=True, timeout=HOOK_SECONDS - 1,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return
    sink = subprocess.DEVNULL
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        sink = open(log, "ab")
    try:
        common = dict(stdin=subprocess.DEVNULL, stdout=sink, stderr=sink, close_fds=True)
        if sys.platform != 'win32':
            subprocess.Popen(argv, start_new_session=True, **common)
            return
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(argv, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **common)
        except OSError:
            # The host's job forbids breakaway: the stamp then lives only as long as it.
            subprocess.Popen(argv, creationflags=flags, **common)
    finally:
        if sink is not subprocess.DEVNULL:
            sink.close()


def _project(cwd: Path):
    """The project that owns this merge, by the shared rule (standard library only)."""
    pinned = os.environ.get("TASKMASTER_ROOT")
    if pinned:
        root = Path(pinned)
    else:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from taskmaster.root import resolve_root
            root = resolve_root(Path(cwd)).root
        except Exception:
            return None
    return root if (root / ".taskmaster").is_dir() else None


def _hook_log(root):
    return root / ".taskmaster" / "local" / "hook.log" if root is not None else None


def _log(root, reason: str) -> None:
    """One line saying why a merge was not (or not yet) recorded. Never raises."""
    path = _hook_log(root)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            path.write_bytes(path.read_bytes()[-LOG_MAX_BYTES // 2:])
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.time()} merge_recorder: {reason}\n")
    except Exception:
        pass


def _git_out(cwd: Path, *args: str):
    try:
        completed = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=10,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def parse_src_branch(command: str) -> str:
    """Parse source branch (cross-link: merge_gate.py parse_src_branch).

    Strategy: drop everything up to and including "merge", walk tokens,
    last non-flag token = branch name.
    """
    after_merge = "\n".join(
        re.sub(r".*\bmerge[ \t\r\f\v]*", "", line) for line in command.splitlines()
    )
    src = ""
    for tok in after_merge.split():
        if tok.startswith("-"):
            continue  # flag — skip
        src = tok
        # Keep updating: last non-flag token is the branch name
    return src


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0

    # -- Short-circuit: command must contain "merge" -----------------------------
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command:
        return 0
    if "merge" not in command:
        return 0

    # -- Must be a `git` invocation reaching `merge` as a subcommand -------------
    if not GIT_MERGE_RE.search(command):
        return 0

    # -- Only act on SUCCESSFUL merges --------------------------------------------
    # (jq semantics: .tool_response.exit_code // .exit_code // 1 — null/false
    #  fall through; a non-object tool_response made jq error -> not "0" -> exit 0)
    tool_response = data.get("tool_response")
    if tool_response is None:
        exit_code = None
    elif isinstance(tool_response, dict):
        exit_code = tool_response.get("exit_code")
    else:
        return 0
    if exit_code is None or exit_code is False:
        exit_code = data.get("exit_code")
    if exit_code is None or exit_code is False:
        exit_code = 1
    if str(exit_code) != "0":
        return 0

    # -- Parse source branch; reject anonymous refs --------------------------------
    src = parse_src_branch(command)
    if not src:
        return 0
    if ANON_REF_RE.search(src):
        return 0

    # -- Resolve what was merged NOW: the stamp may run seconds later, detached ----
    cwd = Path(data["cwd"]) if isinstance(data.get("cwd"), str) and data.get("cwd") else Path.cwd()
    root = _project(cwd)
    target = _git_out(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    sha = _git_out(cwd, "rev-parse", "HEAD")
    if not target or target == "HEAD" or not sha:
        _log(root, f"merge of {src} left a detached HEAD or an unreadable one; not recording it")
        return 0

    # -- Delegate to the stamp (fail-open, but never silent) ------------------------
    stamp_script = Path(__file__).parent / "merge_recorder_stamp.py"
    if not stamp_script.is_file():
        _log(root, f"{stamp_script} is missing; not recording the merge of {src}")
        return 0

    argv = stamp_command(stamp_script, [src, target, sha, str(cwd)])
    if argv is None:
        _log(root, f"cannot run the merge stamp: {sys.executable} lacks the stamp's runtime and uv is not "
                   f"on PATH; not recording {src} -> {target} {sha}")
        return 0
    try:
        run_stamp(argv, detach=argv[0] != (sys.executable or "python"), log=_hook_log(root))
    except subprocess.TimeoutExpired:
        _log(root, f"merge stamp for {src} -> {target} {sha} was stopped at the hook limit; it records or "
                   "queues the current merge first, so see its own lines above")
    except Exception as exc:
        _log(root, f"could not start the merge stamp ({exc!r}); not recording {src} -> {target} {sha}")

    return 0


if __name__ == "__main__":
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # advisory hook — never blocks
