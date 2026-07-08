"""Tests for canonicalize_layout — the .claude/ or root → .taskmaster/ migrator (ISS-004)."""
import json
from pathlib import Path

import pytest


def _write(p: Path, content: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_no_backlog_returns_no_backlog(tmp_path):
    from taskmaster.taskmaster_v3 import canonicalize_layout
    summary = canonicalize_layout(tmp_path)
    assert summary["status"] == "no_backlog"
    assert summary["moved"] == []


def test_already_canonical_is_noop(tmp_path):
    from taskmaster.taskmaster_v3 import canonicalize_layout
    _write(tmp_path / ".taskmaster" / "backlog.yaml", "meta: {schema_version: 3}\n")
    _write(tmp_path / ".taskmaster" / "handovers" / "h.md", "x")
    summary = canonicalize_layout(tmp_path)
    assert summary["status"] == "already_canonical"
    assert summary["moved"] == []
    # Files untouched
    assert (tmp_path / ".taskmaster" / "backlog.yaml").exists()
    assert (tmp_path / ".taskmaster" / "handovers" / "h.md").exists()


def test_root_layout_migrates(tmp_path):
    from taskmaster.taskmaster_v3 import canonicalize_layout
    _write(tmp_path / "backlog.yaml", "meta: {schema_version: 3}\n")
    _write(tmp_path / "PROGRESS.md", "progress")
    _write(tmp_path / "tasks" / "T-001.md", "task1")
    _write(tmp_path / "handovers" / "h.md", "handover")
    summary = canonicalize_layout(tmp_path)
    assert summary["status"] == "migrated"
    assert summary["source"] == "root"
    # Destination has the files
    assert (tmp_path / ".taskmaster" / "backlog.yaml").read_text() == "meta: {schema_version: 3}\n"
    assert (tmp_path / ".taskmaster" / "PROGRESS.md").read_text() == "progress"
    assert (tmp_path / ".taskmaster" / "tasks" / "T-001.md").read_text() == "task1"
    assert (tmp_path / ".taskmaster" / "handovers" / "h.md").read_text() == "handover"
    # Sources gone
    assert not (tmp_path / "backlog.yaml").exists()
    assert not (tmp_path / "PROGRESS.md").exists()
    assert not (tmp_path / "tasks").exists()
    assert not (tmp_path / "handovers").exists()


def test_claude_layout_migrates_and_deletes_config(tmp_path):
    from taskmaster.taskmaster_v3 import canonicalize_layout
    _write(tmp_path / ".claude" / "backlog.yaml", "meta: {schema_version: 3}\n")
    _write(tmp_path / ".claude" / "PROGRESS.md", "progress")
    _write(tmp_path / ".claude" / "tasks" / "T-001.md", "task1")
    _write(tmp_path / ".claude" / "handovers" / "h.md", "handover")
    _write(tmp_path / ".claude" / "issues" / "ISS-001.md", "issue")
    _write(tmp_path / ".claude" / "taskmaster.json", json.dumps({
        "backlog_path": ".claude/backlog.yaml",
    }))
    # Foreign content that must NOT be touched
    _write(tmp_path / ".claude" / "settings.json", "{}")
    _write(tmp_path / ".claude" / "hooks" / "guard.sh", "#!/bin/sh")

    summary = canonicalize_layout(tmp_path)
    assert summary["status"] == "migrated"
    assert summary["source"] == "claude"

    # Migrated content
    assert (tmp_path / ".taskmaster" / "backlog.yaml").exists()
    assert (tmp_path / ".taskmaster" / "PROGRESS.md").exists()
    assert (tmp_path / ".taskmaster" / "tasks" / "T-001.md").exists()
    assert (tmp_path / ".taskmaster" / "handovers" / "h.md").exists()
    assert (tmp_path / ".taskmaster" / "issues" / "ISS-001.md").exists()
    # Config deleted
    assert not (tmp_path / ".claude" / "taskmaster.json").exists()
    assert summary["deleted_config"] == ".claude\\taskmaster.json" or \
           summary["deleted_config"] == ".claude/taskmaster.json"
    # Foreign content untouched
    assert (tmp_path / ".claude" / "settings.json").exists()
    assert (tmp_path / ".claude" / "hooks" / "guard.sh").exists()
    # Source artifact dirs gone (backlog/PROGRESS/etc.)
    assert not (tmp_path / ".claude" / "backlog.yaml").exists()
    assert not (tmp_path / ".claude" / "tasks").exists()
    assert not (tmp_path / ".claude" / "handovers").exists()
    # .claude/ itself remains because foreign content is still there
    assert (tmp_path / ".claude").exists()


def test_idempotent_second_run_is_already_canonical(tmp_path):
    from taskmaster.taskmaster_v3 import canonicalize_layout
    _write(tmp_path / ".claude" / "backlog.yaml", "meta: {schema_version: 3}\n")
    _write(tmp_path / ".claude" / "tasks" / "T-001.md", "task1")
    canonicalize_layout(tmp_path)
    summary = canonicalize_layout(tmp_path)
    assert summary["status"] == "already_canonical"


def test_dry_run_does_not_modify_anything(tmp_path):
    from taskmaster.taskmaster_v3 import canonicalize_layout
    _write(tmp_path / ".claude" / "backlog.yaml", "x")
    _write(tmp_path / ".claude" / "tasks" / "T-001.md", "task1")
    summary = canonicalize_layout(tmp_path, dry_run=True)
    assert summary["status"] == "would_migrate"
    assert len(summary["would_move"]) == 2  # backlog.yaml + tasks/T-001.md
    # Nothing moved
    assert (tmp_path / ".claude" / "backlog.yaml").exists()
    assert (tmp_path / ".claude" / "tasks" / "T-001.md").exists()
    assert not (tmp_path / ".taskmaster" / "backlog.yaml").exists()


def test_conflict_with_different_content_aborts_migration(tmp_path):
    from taskmaster.taskmaster_v3 import canonicalize_layout
    _write(tmp_path / ".claude" / "backlog.yaml", "claude version")
    _write(tmp_path / ".taskmaster" / "backlog.yaml", "DIFFERENT taskmaster version")
    summary = canonicalize_layout(tmp_path)
    # Both layouts have backlog.yaml — ambiguous
    assert summary["status"] == "ambiguous"
    # Both files preserved untouched
    assert (tmp_path / ".claude" / "backlog.yaml").read_text() == "claude version"
    assert (tmp_path / ".taskmaster" / "backlog.yaml").read_text() == "DIFFERENT taskmaster version"


def test_conflict_in_artifact_subdir_aborts_migration(tmp_path):
    """Same backlog location but conflicting artifact files → conflicts status."""
    from taskmaster.taskmaster_v3 import canonicalize_layout
    _write(tmp_path / ".claude" / "backlog.yaml", "x")
    _write(tmp_path / ".claude" / "handovers" / "h.md", "claude content")
    _write(tmp_path / ".taskmaster" / "handovers" / "h.md", "taskmaster content")
    summary = canonicalize_layout(tmp_path)
    assert summary["status"] == "conflicts"
    assert len(summary["conflicts"]) == 1
    # Nothing moved — sources still in place
    assert (tmp_path / ".claude" / "handovers" / "h.md").read_text() == "claude content"
    assert (tmp_path / ".taskmaster" / "handovers" / "h.md").read_text() == "taskmaster content"
    assert (tmp_path / ".claude" / "backlog.yaml").exists()


def test_partial_prior_migration_idempotent_cleanup(tmp_path):
    """If the same file already exists at dst with identical bytes, the source
    is removed and the migration completes — represents resuming a half-run."""
    from taskmaster.taskmaster_v3 import canonicalize_layout
    _write(tmp_path / ".claude" / "backlog.yaml", "x")
    _write(tmp_path / ".claude" / "handovers" / "h.md", "same bytes")
    _write(tmp_path / ".taskmaster" / "handovers" / "h.md", "same bytes")
    summary = canonicalize_layout(tmp_path)
    assert summary["status"] == "migrated"
    # The summary records the destination side — that's the file the user keeps.
    skipped = {s.replace("\\", "/") for s in summary["skipped_already_at_dst"]}
    assert ".taskmaster/handovers/h.md" in skipped
    # Source duplicate removed
    assert not (tmp_path / ".claude" / "handovers" / "h.md").exists()
    # Destination kept
    assert (tmp_path / ".taskmaster" / "handovers" / "h.md").read_text() == "same bytes"


def test_auto_dir_merges_with_preexisting_taskmaster_auto(tmp_path):
    """`.taskmaster/auto/` may already exist from before the migrator landed.
    Each file is enumerated separately, so the merge is automatic."""
    from taskmaster.taskmaster_v3 import canonicalize_layout
    _write(tmp_path / ".claude" / "backlog.yaml", "x")
    _write(tmp_path / ".claude" / "auto" / "state.json", "from-claude")
    _write(tmp_path / ".taskmaster" / "auto" / "sessions" / "v3-001.json", "preexisting-session")
    summary = canonicalize_layout(tmp_path)
    assert summary["status"] == "migrated"
    # Both ended up in canonical auto/
    assert (tmp_path / ".taskmaster" / "auto" / "state.json").read_text() == "from-claude"
    assert (tmp_path / ".taskmaster" / "auto" / "sessions" / "v3-001.json").read_text() == "preexisting-session"


def test_only_v3_artifacts_moved_other_claude_files_untouched(tmp_path):
    """Claude Code's own files in .claude/ (settings.json, hooks/, etc.) must
    never be moved — only the items in _CANONICALIZE_ITEMS."""
    from taskmaster.taskmaster_v3 import canonicalize_layout
    _write(tmp_path / ".claude" / "backlog.yaml", "x")
    _write(tmp_path / ".claude" / "settings.json", '{"theme":"dark"}')
    _write(tmp_path / ".claude" / "settings.local.json", '{}')
    _write(tmp_path / ".claude" / "hooks" / "x.sh", "#!/bin/sh")
    _write(tmp_path / ".claude" / "scheduled_tasks.lock", "lock")
    canonicalize_layout(tmp_path)
    # Foreign files all still in .claude/
    assert (tmp_path / ".claude" / "settings.json").exists()
    assert (tmp_path / ".claude" / "settings.local.json").exists()
    assert (tmp_path / ".claude" / "hooks" / "x.sh").exists()
    assert (tmp_path / ".claude" / "scheduled_tasks.lock").exists()
    # Backlog moved
    assert (tmp_path / ".taskmaster" / "backlog.yaml").exists()
    assert not (tmp_path / ".claude" / "backlog.yaml").exists()
