# User intent: archiving is a store operation, not a word in a document — every route that
# archives or un-archives an epic or a phase must move the row's flag, log the transition,
# and (for epics) cascade to the tasks the epic owns, exactly like the dedicated tool.
"""Explicit archive/unarchive transitions for epics and phases (fix wave F6)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import store


def _seed(root: Path) -> Path:
    backlog_path = root / ".taskmaster"
    for sub in ("tasks", "local"):
        (backlog_path / sub).mkdir(parents=True, exist_ok=True)
    (backlog_path / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    (backlog_path / "local" / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    document = {
        "version": 4,
        "project": "archive",
        "meta": {"project": "archive", "schema_version": 4},
        "epics": [{"id": "core", "name": "Core", "status": "active", "done_when": "n/a"}],
        "phases": [{"id": "dev", "name": "Development", "status": "active"}],
    }
    (backlog_path / "backlog.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return backlog_path


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    root = tmp_path / "project"
    backlog_path = _seed(root)
    monkeypatch.setattr(bs, "ROOT", root)
    monkeypatch.setattr(bs, "CONFIG_PATH", backlog_path / "missing.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", root / ".claude" / "missing.json")
    bs.backlog_add_task("Owned", "core", phase="dev")
    return backlog_path


def _row(backlog_path: Path, kind: str, ident: str) -> sqlite3.Row:
    store.checkpoint_all()
    with sqlite3.connect(store.db_path(backlog_path)) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT kind,id,archived,doc FROM entities WHERE kind=? AND id=?",
            (kind, ident),
        ).fetchone()


def _ops(backlog_path: Path, kind: str, ident: str) -> list[str]:
    store.checkpoint_all()
    with sqlite3.connect(store.db_path(backlog_path)) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT op FROM changes WHERE kind=? AND id=? ORDER BY seq",
                (kind, ident),
            )
        ]


def test_update_phase_to_archived_flips_the_row_and_logs_the_transition(project):
    result = bs.backlog_update_phase("dev", "status", "archived")

    assert not result.startswith("Error"), result
    row = _row(project, "phase", "dev")
    assert row["archived"] == 1, "the phase document said archived but the row did not"
    assert "archive" in _ops(project, "phase", "dev")


def test_update_phase_back_out_of_archived_clears_the_row_flag(project):
    bs.backlog_update_phase("dev", "status", "archived")

    bs.backlog_update_phase("dev", "status", "active")

    row = _row(project, "phase", "dev")
    assert row["archived"] == 0
    assert "unarchive" in _ops(project, "phase", "dev")


def test_reactivating_an_archived_epic_clears_the_row_flag(project):
    bs.backlog_archive_epic("core", reason="done")
    assert _row(project, "epic", "core")["archived"] == 1

    result = bs.backlog_update_epic("core", "status", "active")

    assert not result.startswith("Error"), result
    row = _row(project, "epic", "core")
    assert row["archived"] == 0, "the epic went active while its row stayed archived"
    assert "unarchive" in _ops(project, "epic", "core")


def test_batch_epic_archive_flips_the_row_and_cascades_like_the_dedicated_tool(project):
    result = bs.backlog_batch_update("update_epic core status archived")

    assert "1 applied" in result, result
    assert "cascaded" in result, result
    assert _row(project, "epic", "core")["archived"] == 1
    assert "archive" in _ops(project, "epic", "core")
    assert _row(project, "task", "core-001")["archived"] == 1, (
        "the batch archive skipped the task cascade the dedicated tool applies"
    )
    assert (project / "tasks" / "archive" / "core-001.md").exists()


def test_batch_epic_archive_refuses_an_already_archived_epic(project):
    bs.backlog_archive_epic("core", reason="done")

    result = bs.backlog_batch_update("update_epic core status archived")

    assert "already archived" in result, result
