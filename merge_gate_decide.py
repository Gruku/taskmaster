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

CARDINAL RULE: FAIL-OPEN.  Every uncertainty, error, or missing piece
prints "ALLOW".  The ENTIRE body is wrapped in a top-level try/except so a
Python exception can never cause a silent block.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


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


def decide(src: str, cwd: Path) -> str:
    """Core decision logic — returns the string to print (ALLOW or BLOCK:...).

    Fail-open: any exception or unexpected state returns ALLOW.
    """
    # --- Load project manifest ---
    try:
        # Walk up from cwd to find project root containing .taskmaster/
        from project import load_project_manifest, resolve_project_root

        project_root = resolve_project_root(cwd)
        if project_root is None:
            return "ALLOW"

        manifest = load_project_manifest(project_root)
        if manifest is None:
            return "ALLOW"

        if not manifest.conventions.policies.review_gate_required_for_merge:
            return "ALLOW"

    except Exception:
        return "ALLOW"

    # Policy is ON — proceed to check the task.

    # --- Load backlog ---
    try:
        import yaml

        # Try to find backlog relative to project_root, using the same
        # priority order as taskmaster_v3._resolve_artifact_root.
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

    # --- Find task whose branch == SRC ---
    try:
        task = None
        for epic in raw.get("epics", []):
            for t in epic.get("tasks", []):
                if t.get("branch") == src:
                    task = t
                    break
            if task:
                break

        if task is None:
            return "ALLOW"

    except Exception:
        return "ALLOW"

    # --- Check skip_merge_gate ---
    try:
        if task.get("skip_merge_gate"):
            return "ALLOW"
    except Exception:
        return "ALLOW"

    # --- Read gate record ---
    try:
        task_id = task.get("id", "unknown")
        gates = task.get("gates") or {}

        # v3 keeps `gates` as a HEAVY field in the per-task file, not in the
        # slim backlog.yaml index (see taskmaster_v3.HEAVY_FIELDS). Hydrate it
        # from there so this hook can see what backlog_record_gate wrote.
        from taskmaster_v3 import detect_schema_version, task_file_path, read_task_file, SCHEMA_V3

        if detect_schema_version(raw) >= SCHEMA_V3:
            md_path = task_file_path(backlog_path, task_id)
            fm, _body = read_task_file(md_path)
            if "gates" in fm:
                gates = fm["gates"] or {}

        rg = gates.get("review-gate")

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
        freshness = task.get("merge_gate_freshness", "strict")
        if freshness == "any":
            return "ALLOW"

        # strict: commit_sha must match current branch tip
        gate_sha = rg.get("commit_sha", "")
        branch_tip = _git_rev_parse(src, cwd)

        if branch_tip is None:
            # Can't resolve tip (no git repo, detached, etc.) — fail-open
            return "ALLOW"

        if gate_sha == branch_tip:
            return "ALLOW"

        return (
            f"BLOCK:{task_id}: review-gate was run on {gate_sha[:12] if gate_sha else '(unknown)'} "
            f"but branch tip is now {branch_tip[:12]}. "
            "Run /taskmaster:review-gate to re-validate. "
            "Set merge_gate_freshness: any to skip the SHA check."
        )

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
