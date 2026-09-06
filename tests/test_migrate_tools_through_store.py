"""User intent: the two legacy migration tools must not write the projection
themselves. Opening the store is the migration (spec decision 9 — adoption on
first open), so `backlog_migrate_v3` and `backlog_migrate_v4` canonicalize the
layout, open the store and report what it holds. Driven through the server with
the projection-bypass guard armed, so a returning file writer fails loudly.
"""
from __future__ import annotations

import yaml

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store


def _v2_project(root):
    """A v2 backlog: one file, heavy fields inline, no schema marker."""
    tm = root / ".taskmaster"
    tm.mkdir(parents=True, exist_ok=True)
    (tm / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    (tm / "backlog.yaml").write_text(
        yaml.safe_dump(
            {
                "meta": {"project": "legacy"},
                "phases": [{"id": "dev", "name": "Development", "order": 1}],
                "epics": [
                    {
                        "id": "e1",
                        "name": "E1",
                        "status": "active",
                        "tasks": [
                            {
                                "id": "e1-001",
                                "title": "Heavy",
                                "status": "todo",
                                "priority": "medium",
                                "phase": "dev",
                                "description": "a long description that belongs in a file",
                                "notes": "notes that belong in a file",
                            }
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return tm / "backlog.yaml"


@pytest.fixture()
def v2_project(tmp_path, monkeypatch):
    bp = _v2_project(tmp_path)
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    monkeypatch.setattr(bs, "CONFIG_PATH", tmp_path / ".taskmaster" / "taskmaster.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", tmp_path / ".claude" / "taskmaster.json")
    monkeypatch.chdir(tmp_path)
    store.reset_for_tests()
    return tmp_path, bp


def test_migrate_v3_adopts_through_the_store(v2_project):
    root, bp = v2_project
    out = bs.backlog_migrate_v3()

    assert "Error" not in out, out
    # The store adopted the projection: the task is a committed row.
    with store.transaction(backlog_path=bp, tool="test-read") as tx:
        ids = [tid for tid, _doc, _body in tx.list("task")]
    assert ids == ["e1-001"], ids
    # And the report names what the store now holds.
    assert "task: 1" in out and "epic: 1" in out, out
    # Adoption mutates, so its answer names the commit, like every other
    # mutating tool (R6, decision 7). It used to print `max seq` in prose only.
    assert out.rstrip().endswith("]") and "[seq " in out, out


def test_migrate_v4_adopts_through_the_store(v2_project):
    root, bp = v2_project
    out = bs.backlog_migrate_v4()

    assert "Error" not in out, out
    with store.transaction(backlog_path=bp, tool="test-read") as tx:
        ids = [tid for tid, _doc, _body in tx.list("task")]
    assert ids == ["e1-001"], ids
    # Heavy fields survive the adoption, and land in the task's own file.
    with store.transaction(backlog_path=bp, tool="test-read") as tx:
        doc = tx.get("task", "e1-001")
    assert doc.get("description") == "a long description that belongs in a file"
    task_md = root / ".taskmaster" / "tasks" / "e1-001.md"
    assert task_md.exists()
    assert "a long description" in task_md.read_text(encoding="utf-8")
    # The index gained the schema marker and lost the heavy fields.
    projected = yaml.safe_load(bp.read_text(encoding="utf-8"))
    assert projected["meta"]["schema_version"] >= 4
    assert all("tasks" not in epic for epic in projected["epics"])


def test_migrate_tools_are_idempotent(v2_project):
    _root, bp = v2_project
    bs.backlog_migrate_v4()
    again = bs.backlog_migrate_v4()
    assert "Error" not in again, again
    with store.transaction(backlog_path=bp, tool="test-read") as tx:
        ids = [tid for tid, _doc, _body in tx.list("task")]
    assert ids == ["e1-001"], ids


def test_migrate_v3_reports_a_missing_backlog(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    monkeypatch.setattr(bs, "CONFIG_PATH", tmp_path / ".taskmaster" / "taskmaster.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", tmp_path / ".claude" / "taskmaster.json")
    monkeypatch.chdir(tmp_path)
    store.reset_for_tests()
    assert "no backlog" in bs.backlog_migrate_v3().lower()


def test_migrate_v3_canonicalizes_a_legacy_layout(tmp_path, monkeypatch):
    """A `.claude/` project is moved into `.taskmaster/` and then adopted —
    the store refuses to open anywhere else, so the move has to happen first."""
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "backlog.yaml").write_text(
        yaml.safe_dump(
            {
                "meta": {"project": "legacy"},
                "phases": [],
                "epics": [
                    {
                        "id": "e1",
                        "name": "E1",
                        "status": "active",
                        "tasks": [
                            {"id": "e1-001", "title": "T", "status": "todo",
                             "priority": "medium"}
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    monkeypatch.setattr(bs, "CONFIG_PATH", tmp_path / ".taskmaster" / "taskmaster.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", tmp_path / ".claude" / "taskmaster.json")
    monkeypatch.chdir(tmp_path)
    store.reset_for_tests()

    out = bs.backlog_migrate_v3()

    assert "Error" not in out, out
    assert (tmp_path / ".taskmaster" / "backlog.yaml").exists()
    with store.transaction(
        backlog_path=tmp_path / ".taskmaster" / "backlog.yaml", tool="test-read"
    ) as tx:
        assert [tid for tid, _d, _b in tx.list("task")] == ["e1-001"]


def test_no_file_writer_remains_in_the_migration_path():
    """The tools must not reach the legacy savers, whatever the input shape."""
    from taskmaster import taskmaster_v3 as v3

    for name in ("save_v3", "save_v4", "migrate_v2_to_v3", "migrate_v3_to_v4"):
        assert not hasattr(v3, name), (
            f"taskmaster_v3.{name} still exists; the store owns projection writes"
        )
