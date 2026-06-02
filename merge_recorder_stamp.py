"""merge_recorder_stamp.py — Stamp module for merge-recorder.sh.

Called by merge-recorder.sh as:
    python merge_recorder_stamp.py <SRC_BRANCH> [PROJECT_CWD]

SRC_BRANCH: the source (feature) branch that was merged in.
PROJECT_CWD: optional; defaults to Path.cwd(). Production PostToolUse hooks
    inherit the user's project cwd so the default is correct in prod; the
    explicit argv lets the test harness point the resolver at the project root
    by setting subprocess cwd=<test project dir> (NOT an env-var seam).

CARDINAL RULE: NEVER BLOCKS. Wrap the ENTIRE body in try/except so a Python
exception can never propagate to the shell as a non-zero exit.  The shell hook
also has `|| true` around this call, but defence-in-depth is correct here.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Minimal helpers (no heavy imports from backlog_server / MCP server)
# ---------------------------------------------------------------------------

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


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def _rung_for_branch(branch: str, merge_targets: list[dict]) -> str | None:
    """Map a branch name to its rung label by string match across aliases.
    None if unmatched.
    # Cross-linked with merge-gate.sh source-branch parser and
    # taskmaster_v3.rung_for_branch — same semantics, minimal copy.
    """
    if not branch:
        return None
    for r in (merge_targets or []):
        if branch in (r.get("branches") or []) or branch == r.get("label"):
            return r.get("label")
    return None


def _default_merge_targets() -> list[dict]:
    """Same defaults as project.DEFAULT_MERGE_TARGETS — copied to avoid the import."""
    return [
        {"label": "develop", "branches": ["develop", "dev"]},
        {"label": "stage",   "branches": ["stage", "staging"]},
        {"label": "master",  "branches": ["master", "main"]},
    ]


def _load_merge_targets(project_root: Path) -> list[dict]:
    """Load merge_targets from project.yaml, fall back to defaults."""
    try:
        from project import load_project_manifest
        m = load_project_manifest(project_root)
        if m is not None:
            return m.merge_targets_resolved()
    except Exception:
        pass
    return _default_merge_targets()


def _load_backlog(project_root: Path) -> dict | None:
    """Load .taskmaster/backlog.yaml from project root. Returns raw dict or None."""
    try:
        import yaml
        for candidate in [
            project_root / ".taskmaster" / "backlog.yaml",
            project_root / ".claude" / "backlog.yaml",
            project_root / "backlog.yaml",
        ]:
            if candidate.is_file():
                return yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        return None
    except Exception:
        return None


def _save_backlog(project_root: Path, data: dict) -> None:
    """Write backlog.yaml back atomically (best-effort)."""
    try:
        import yaml
        for candidate in [
            project_root / ".taskmaster" / "backlog.yaml",
            project_root / ".claude" / "backlog.yaml",
            project_root / "backlog.yaml",
        ]:
            if candidate.is_file():
                candidate.write_text(
                    yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
                    encoding="utf-8",
                )
                return
    except Exception:
        pass


def _find_task_by_branch(data: dict, src: str) -> dict | None:
    """Return the first task whose branch == src, or None."""
    for epic in (data.get("epics") or []):
        for task in (epic.get("tasks") or []):
            if task.get("branch") == src:
                return task
    return None


def stamp(src: str, cwd: Path) -> None:
    """Core stamp logic — silently returns on any unexpected state."""
    # Resolve project root (walk up from cwd looking for .taskmaster/)
    try:
        from project import resolve_project_root
        project_root = resolve_project_root(cwd)
    except Exception:
        project_root = None

    if project_root is None:
        # Fall back: cwd itself (if .taskmaster/ is present)
        if (cwd / ".taskmaster").is_dir():
            project_root = cwd
        else:
            return

    # Load backlog
    data = _load_backlog(project_root)
    if data is None:
        return

    # Find the task whose branch == SRC
    task = _find_task_by_branch(data, src)
    if task is None:
        return

    # Determine current branch (the merge TARGET, post-merge HEAD)
    current_branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if not current_branch or current_branch == "HEAD":
        return

    # Determine merge commit SHA
    sha = _git(["rev-parse", "HEAD"], cwd)
    if not sha:
        return

    # Resolve rung label: named ladder rung or "branch:<name>"
    merge_targets = _load_merge_targets(project_root)
    rung = _rung_for_branch(current_branch, merge_targets) or f"branch:{current_branch}"

    # Write merge_status entry (idempotent overwrite per rung)
    task.setdefault("merge_status", {})[rung] = {
        "merged_at": _now(),
        "merge_commit": sha,
    }

    # Save
    _save_backlog(project_root, data)


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
