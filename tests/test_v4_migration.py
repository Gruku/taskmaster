"""v3 -> v4 migration tests."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from taskmaster import taskmaster_v3 as v3  # noqa: E402


def _v3_project(tmp_path: Path) -> Path:
    tm = tmp_path / ".taskmaster"
    (tm / "tasks").mkdir(parents=True)
    backlog = {
        "meta": {"project": "t", "schema_version": 3, "updated": "2026-07-01"},
        "epics": [{
            "id": "e",
            "name": "E",
            "status": "active",
            "tasks": [
                {"id": "e-001", "title": "First", "status": "todo", "priority": "high"},
                {"id": "e-002", "title": "Second", "status": "done", "priority": "low"},
            ],
        }],
        "phases": [{"id": "p1", "name": "P1"}],
    }
    backlog_path = tm / "backlog.yaml"
    backlog_path.write_text(yaml.dump(backlog), encoding="utf-8")
    v3.write_task_file(
        tm / "tasks" / "e-001.md",
        {"id": "e-001", "title": "First", "notes": "important"},
        "## Spec\n\nbody",
    )
    # use_v3 is a retired pref; keeping it here proves migration relocates a
    # stale viewer.json (and its dead keys) to local/ without choking.
    (tm / "viewer.json").write_text('{"use_v3": true}', encoding="utf-8")
    (tm / "auto").mkdir()
    (tm / "auto" / "state.json").write_text("{}", encoding="utf-8")
    (tm / "snapshots").mkdir()
    (tm / "snapshots" / "old.json").write_text("{}", encoding="utf-8")
    return backlog_path


def test_migration_moves_all_fields_to_task_files(tmp_path):
    backlog_path = _v3_project(tmp_path)
    summary = v3.migrate_v3_to_v4(backlog_path)
    assert summary["status"] == "migrated"
    assert summary["schema_after"] == v3.SCHEMA_V4
    on_disk = yaml.safe_load(backlog_path.read_text(encoding="utf-8"))
    assert on_disk["meta"]["schema_version"] == 4
    assert "tasks" not in on_disk["epics"][0]
    fm1, body1 = v3.read_task_file(backlog_path.parent / "tasks" / "e-001.md")
    assert fm1["epic"] == "e" and fm1["order"] == 1.0
    assert fm1["priority"] == "high" and fm1["notes"] == "important"
    assert body1.strip() == "## Spec\n\nbody"
    fm2, _ = v3.read_task_file(backlog_path.parent / "tasks" / "e-002.md")
    assert fm2["epic"] == "e" and fm2["order"] == 2.0
    assert fm2["status"] == "done"


def test_migration_moves_local_and_deletes_snapshots(tmp_path):
    backlog_path = _v3_project(tmp_path)
    v3.migrate_v3_to_v4(backlog_path)
    assert (backlog_path.parent / "local" / "viewer.json").exists()
    assert (backlog_path.parent / "local" / "auto" / "state.json").exists()
    assert not (backlog_path.parent / "viewer.json").exists()
    assert not (backlog_path.parent / "auto").exists()
    assert not (backlog_path.parent / "snapshots").exists()


def test_migration_idempotent(tmp_path):
    backlog_path = _v3_project(tmp_path)
    v3.migrate_v3_to_v4(backlog_path)
    again = v3.migrate_v3_to_v4(backlog_path)
    assert again["status"] == "already_v4"
    assert again["tasks_total"] == 2


def test_round_trip_after_migration(tmp_path):
    backlog_path = _v3_project(tmp_path)
    v3.migrate_v3_to_v4(backlog_path)
    data = v3.load_v4(backlog_path)
    ids = [task["id"] for epic in data["epics"] for task in epic["tasks"]]
    assert ids == ["e-001", "e-002"]
