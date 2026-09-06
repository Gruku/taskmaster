"""v3 -> v4 adoption tests.

`taskmaster_v3.migrate_v3_to_v4` is gone: the store adopts whatever schema it
finds when it first opens a project (design spec decision 9), so opening the
store *is* the migration. These are the same contracts, asserted against the
store: every task field lands in `tasks/<id>.md`, `backlog.yaml` loses its task
lists and gains the v4 marker, machine-local state moves under `local/`, the
retired `snapshots/` directory goes away, and re-opening changes nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from taskmaster import store  # noqa: E402
from taskmaster import taskmaster_v3 as v3  # noqa: E402


def _v3_project(tmp_path: Path) -> Path:
    tm = tmp_path / ".taskmaster"
    (tm / "tasks").mkdir(parents=True)
    (tm / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
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
    # use_v3 is a retired pref; keeping it here proves adoption relocates a
    # stale viewer.json (and its dead keys) to local/ without choking.
    (tm / "viewer.json").write_text('{"use_v3": true}', encoding="utf-8")
    (tm / "auto").mkdir()
    (tm / "auto" / "state.json").write_text("{}", encoding="utf-8")
    (tm / "snapshots").mkdir()
    (tm / "snapshots" / "old.json").write_text("{}", encoding="utf-8")
    return backlog_path


@pytest.fixture()
def adopted(tmp_path):
    backlog_path = _v3_project(tmp_path)
    store.reset_for_tests()
    opened = store.open_store(backlog_path=backlog_path, session="v4-adoption-test")
    yield backlog_path, opened
    store.reset_for_tests()


def test_adoption_moves_all_fields_to_task_files(adopted):
    backlog_path, _opened = adopted
    on_disk = yaml.safe_load(backlog_path.read_text(encoding="utf-8"))
    assert on_disk["meta"]["schema_version"] == v3.SCHEMA_V4
    assert "tasks" not in on_disk["epics"][0]
    fm1, body1 = v3.read_task_file(backlog_path.parent / "tasks" / "e-001.md")
    assert fm1["epic"] == "e" and fm1["order"] == 1.0
    assert fm1["priority"] == "high" and fm1["notes"] == "important"
    assert body1.strip() == "## Spec\n\nbody"
    fm2, _ = v3.read_task_file(backlog_path.parent / "tasks" / "e-002.md")
    assert fm2["epic"] == "e" and fm2["order"] == 2.0
    assert fm2["status"] == "done"


def test_adoption_moves_local_and_deletes_snapshots(adopted):
    backlog_path, _opened = adopted
    assert (backlog_path.parent / "local" / "viewer.json").exists()
    assert (backlog_path.parent / "local" / "auto" / "state.json").exists()
    assert not (backlog_path.parent / "viewer.json").exists()
    assert not (backlog_path.parent / "auto").exists()
    assert not (backlog_path.parent / "snapshots").exists()


def test_adoption_is_idempotent(adopted):
    backlog_path, opened = adopted
    before = backlog_path.read_text(encoding="utf-8")
    store.reset_for_tests()
    reopened = store.open_store(backlog_path=backlog_path, session="v4-adoption-again")
    assert backlog_path.read_text(encoding="utf-8") == before
    tasks = [t for e in reopened.load_dict()["epics"] for t in e["tasks"]]
    assert len(tasks) == 2


def test_round_trip_after_adoption(adopted):
    backlog_path, opened = adopted
    data = v3.load_v4(backlog_path)
    ids = [task["id"] for epic in data["epics"] for task in epic["tasks"]]
    assert ids == ["e-001", "e-002"]
    # And the store's own view agrees with the projection it just wrote.
    store_ids = [t["id"] for e in opened.load_dict()["epics"] for t in e["tasks"]]
    assert store_ids == ids
