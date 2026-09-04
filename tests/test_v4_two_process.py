"""Two interleaved load/save cycles on one machine must not clobber."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture()
def v4_two_task_project(tmp_path, monkeypatch):
    from taskmaster import backlog_server, taskmaster_v3 as v3

    tm = tmp_path / ".taskmaster"
    (tm / "tasks").mkdir(parents=True)
    (tm / "local").mkdir()
    (tm / "local" / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    backlog = {
        "meta": {"project": "t", "schema_version": 4},
        "epics": [{"id": "e", "name": "E", "status": "active"}],
        "phases": [{"id": "p1", "name": "P1", "status": "active"}],
    }
    (tm / "backlog.yaml").write_text(yaml.dump(backlog), encoding="utf-8")
    for number in (1, 2):
        task = {
            "id": f"e-00{number}",
            "title": f"T{number}",
            "epic": "e",
            "order": float(number),
            "status": "todo",
            "priority": "medium",
        }
        frontmatter, body = v3.task_v4_to_file(task)
        v3.write_task_file(tm / "tasks" / f"e-00{number}.md", frontmatter, body)
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    monkeypatch.setattr(backlog_server, "CONFIG_PATH", tm / "taskmaster.json")
    monkeypatch.setattr(
        backlog_server,
        "LEGACY_CONFIG_PATH",
        tmp_path / ".claude" / "taskmaster.json",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_interleaved_writers_do_not_clobber_each_other(v4_two_task_project):
    """Two writers that both start from the same state must both survive.

    This is the write-loss defect the SQLite store exists to fix: the old
    boundary kept one module-level baseline, so whichever writer saved last
    reverted the other one's task. Each transaction now reads and writes under
    the store's writer lock, so both edits land.
    """
    from taskmaster import backlog_server

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def edit(task_id: str, title: str) -> None:
        try:
            barrier.wait(timeout=30)
            with backlog_server._transaction(tool="test-writer") as data:
                backlog_server._find_task(data, task_id)[0]["title"] = title
                backlog_server._mutate_and_save(data)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [
        threading.Thread(target=edit, args=("e-001", "A-edited-1")),
        threading.Thread(target=edit, args=("e-002", "B-edited-2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors

    final = backlog_server._load()
    titles = {
        task["id"]: task["title"]
        for epic in final["epics"]
        for task in epic["tasks"]
    }
    assert titles["e-001"] == "A-edited-1"
    assert titles["e-002"] == "B-edited-2"
