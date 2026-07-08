"""End-to-end tests for the PreCompact snapshot hook (hooks/snapshot.py).

The hook is invoked as a subprocess so the path-resolution logic in
``Path(__file__).resolve().parent.parent`` is exercised in real conditions.
All tests are hermetic: they only touch ``tmp_path`` and the hook itself.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HOOK_PATH = Path(__file__).parent.parent / "hooks" / "snapshot.py"


def _run_hook(workdir: Path, env: dict | None = None) -> "subprocess.CompletedProcess":
    import subprocess

    merged_env = {**os.environ}
    if env:
        merged_env.update(env)

    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        cwd=str(workdir),
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _make_v3_backlog_yaml() -> str:
    """Return a minimal but valid v3 backlog as a YAML string."""
    data = {
        "meta": {
            "project": "test-project",
            "schema_version": 3,
            "updated": "2026-01-01",
        },
        "context": {},
        "epics": [
            {
                "id": "core",
                "name": "Core",
                "tasks": [
                    {
                        "id": "T-001",
                        "title": "First task",
                        "status": "todo",
                        "priority": "high",
                    },
                    {
                        "id": "T-002",
                        "title": "Second task",
                        "status": "in-progress",
                        "priority": "medium",
                    },
                ],
            }
        ],
        "phases": [
            {"id": "phase-1", "status": "active"},
        ],
    }
    return yaml.dump(data, allow_unicode=True)


def _make_v2_backlog_yaml() -> str:
    """Return a minimal v2 backlog (no schema_version key) as a YAML string."""
    data = {
        "meta": {
            "project": "legacy-project",
            # Intentionally omit schema_version → defaults to v2
        },
        "epics": [
            {
                "id": "alpha",
                "name": "Alpha",
                "tasks": [
                    {
                        "id": "LT-001",
                        "title": "Legacy task",
                        "status": "done",
                        "priority": "low",
                    }
                ],
            }
        ],
    }
    return yaml.dump(data, allow_unicode=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_hook_writes_snapshot_for_v3_backlog(tmp_path: Path):
    """Hook writes last.json with all required fields for a v3 backlog."""
    from taskmaster import taskmaster_v3 as v3

    # Write the v3 backlog index under .taskmaster/
    backlog_dir = tmp_path / ".taskmaster"
    backlog_dir.mkdir()
    bp = backlog_dir / "backlog.yaml"
    bp.write_text(_make_v3_backlog_yaml(), encoding="utf-8")

    # Confirm the fixture is actually detected as v3
    raw = yaml.safe_load(bp.read_text(encoding="utf-8"))
    assert v3.detect_schema_version(raw) == 3

    result = _run_hook(
        tmp_path,
        env={"TASKMASTER_ROOT": str(tmp_path)},
    )

    assert result.returncode == 0, f"hook exited non-zero: {result.stderr!r}"
    assert result.stderr == "", f"unexpected stderr: {result.stderr!r}"

    snap_path = tmp_path / ".taskmaster" / "snapshots" / "last.json"
    assert snap_path.exists(), "snapshot file was not created"

    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    assert "taken_at" in snap
    assert "schema_version" in snap
    assert "structural_hash" in snap
    assert "tasks" in snap
    assert "phase_active" in snap
    # Verify tasks from the fixture are present
    assert "T-001" in snap["tasks"]
    assert "T-002" in snap["tasks"]
    # Active phase captured
    assert snap["phase_active"] == "phase-1"


def test_hook_writes_snapshot_for_v2_backlog_in_legacy_claude_dir(tmp_path: Path):
    """Legacy `.claude/`-layout v2 backlog: hook still finds it and writes the
    snapshot, AND emits a deprecation warning to stderr pointing at the migrator.
    """
    backlog_dir = tmp_path / ".claude"
    backlog_dir.mkdir()
    bp = backlog_dir / "backlog.yaml"
    bp.write_text(_make_v2_backlog_yaml(), encoding="utf-8")

    result = _run_hook(
        tmp_path,
        env={"TASKMASTER_ROOT": str(tmp_path)},
    )

    assert result.returncode == 0, f"hook exited non-zero: {result.stderr!r}"
    assert "deprecated" in result.stderr.lower()
    assert "backlog_canonicalize_layout" in result.stderr

    snap_path = tmp_path / ".claude" / "snapshots" / "last.json"
    assert snap_path.exists(), "snapshot file was not created for v2 backlog"

    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    assert "taken_at" in snap
    # v2 data gets run through take_snapshot which hard-codes schema_version=SCHEMA_V3 (3)
    assert "schema_version" in snap


def test_hook_no_op_when_no_backlog_present(tmp_path: Path):
    """Hook exits 0 silently when no backlog.yaml can be found."""
    result = _run_hook(
        tmp_path,
        env={"TASKMASTER_ROOT": str(tmp_path)},
    )

    assert result.returncode == 0
    assert result.stderr == "", f"unexpected stderr: {result.stderr!r}"

    # No snapshot directory should have been created anywhere under tmp_path
    for candidate in (
        tmp_path / ".taskmaster" / "snapshots" / "last.json",
        tmp_path / ".claude" / "snapshots" / "last.json",
        tmp_path / "snapshots" / "last.json",
    ):
        assert not candidate.exists(), f"unexpected snapshot at {candidate}"


def test_hook_exits_zero_on_corrupt_backlog(tmp_path: Path):
    """Hook exits 0 even when the backlog file contains garbage, writing to stderr."""
    backlog_dir = tmp_path / ".taskmaster"
    backlog_dir.mkdir()
    bp = backlog_dir / "backlog.yaml"
    # Write a file that yaml.safe_load will choke on
    bp.write_bytes(b"\x00\x01invalid:::yaml: [[")

    result = _run_hook(
        tmp_path,
        env={"TASKMASTER_ROOT": str(tmp_path)},
    )

    assert result.returncode == 0, f"hook exited non-zero: {result.returncode}"
    assert "taskmaster snapshot hook" in result.stderr, (
        f"expected error message in stderr, got: {result.stderr!r}"
    )


def test_hook_finds_backlog_via_walk_up(tmp_path: Path):
    """Hook discovers backlog.yaml by walking up from a nested subdirectory."""
    # Place the backlog at the root
    backlog_dir = tmp_path / ".taskmaster"
    backlog_dir.mkdir()
    bp = backlog_dir / "backlog.yaml"
    bp.write_text(_make_v3_backlog_yaml(), encoding="utf-8")

    # Run the hook from a deeply nested directory (no TASKMASTER_ROOT override
    # so the hook uses its cwd-based walk-up logic)
    nested = tmp_path / "deep" / "nested"
    nested.mkdir(parents=True)

    result = _run_hook(nested)

    assert result.returncode == 0, f"hook exited non-zero: {result.stderr!r}"

    snap_path = tmp_path / ".taskmaster" / "snapshots" / "last.json"
    assert snap_path.exists(), (
        "snapshot was not found; walk-up discovery may have failed. "
        f"stderr: {result.stderr!r}"
    )
