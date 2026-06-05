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

import subprocess
import sys
from pathlib import Path


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
    try:
        import backlog_server as _bs
    except Exception:
        # Import failure -> fail safe: no stamp, never blocks.
        return

    # Find the task whose branch == SRC (grab its id for the recorder).
    try:
        data = _bs._load()
    except Exception:
        return

    tid = None
    for epic in (data.get("epics") or []):
        for t in (epic.get("tasks") or []):
            if t.get("branch") == src:
                tid = t.get("id")
                break
        if tid:
            break
    if not tid:
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
