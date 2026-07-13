"""Tests for the v4 sharded storage layout (team-relayout, epic 1)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from taskmaster import taskmaster_v3 as v3  # noqa: E402



class TestV4Constants:
    def test_schema_v4_is_4(self):
        assert v3.SCHEMA_V4 == 4

    def test_v4_greater_than_v3(self):
        assert v3.SCHEMA_V4 > v3.SCHEMA_V3


class TestTaskV4RoundTrip:
    def test_all_fields_go_to_frontmatter(self):
        task = {
            "id": "auth-014", "title": "Login", "status": "todo",
            "epic": "auth", "order": 2.0, "priority": "high",
            "gates": {"spec": "pass"}, v3.BODY_KEY: "## Spec\n\nbody text",
        }
        fm, body = v3.task_v4_to_file(task)
        assert fm["id"] == "auth-014"
        assert fm["epic"] == "auth"
        assert fm["order"] == 2.0
        assert fm["gates"] == {"spec": "pass"}
        assert v3.BODY_KEY not in fm
        assert body == "## Spec\n\nbody text"

    def test_from_file_reattaches_body(self):
        fm = {"id": "auth-014", "title": "Login", "epic": "auth", "order": 2.0}
        task = v3.task_v4_from_file(fm, "prose")
        assert task["id"] == "auth-014"
        assert task[v3.BODY_KEY] == "prose"

    def test_empty_body_omits_body_key(self):
        task = v3.task_v4_from_file({"id": "x", "epic": "e", "order": 1.0}, "")
        assert v3.BODY_KEY not in task

    def test_round_trip_identity(self):
        task = {
            "id": "auth-014", "title": "Login", "status": "todo",
            "epic": "auth", "order": 2.0, v3.BODY_KEY: "body",
        }
        fm, body = v3.task_v4_to_file(task)
        assert v3.task_v4_from_file(fm, body) == task


def _write_v4_project(tmp_path, epics, tasks):
    """epics: list of {id,name}. tasks: list of full task dicts (with epic+order).
    Writes a slim backlog.yaml (no task lists) + tasks/<id>.md files."""
    tm = tmp_path / ".taskmaster"
    (tm / "tasks").mkdir(parents=True, exist_ok=True)
    backlog = {"meta": {"project": "t", "schema_version": 4},
               "epics": [dict(e) for e in epics], "phases": []}
    (tm / "backlog.yaml").write_text(yaml.dump(backlog), encoding="utf-8")
    for t in tasks:
        fm, body = v3.task_v4_to_file(t)
        v3.write_task_file(tm / "tasks" / f"{t['id']}.md", fm, body)
    return tm / "backlog.yaml"


class TestLoadV4:
    def test_groups_tasks_by_epic(self, tmp_path):
        bp = _write_v4_project(
            tmp_path,
            epics=[{"id": "auth", "name": "Auth"}, {"id": "ui", "name": "UI"}],
            tasks=[
                {"id": "auth-001", "title": "A", "epic": "auth", "order": 1.0},
                {"id": "ui-001", "title": "U", "epic": "ui", "order": 1.0},
            ],
        )
        data = v3.load_v4(bp)
        by_id = {e["id"]: e for e in data["epics"]}
        assert [t["id"] for t in by_id["auth"]["tasks"]] == ["auth-001"]
        assert [t["id"] for t in by_id["ui"]["tasks"]] == ["ui-001"]

    def test_orders_by_order_then_id(self, tmp_path):
        bp = _write_v4_project(
            tmp_path,
            epics=[{"id": "e", "name": "E"}],
            tasks=[
                {"id": "e-003", "title": "c", "epic": "e", "order": 2.0},
                {"id": "e-001", "title": "a", "epic": "e", "order": 1.0},
                {"id": "e-002", "title": "b", "epic": "e", "order": 1.0},
            ],
        )
        data = v3.load_v4(bp)
        # order 1.0 ties broken by id (e-001 before e-002), then 2.0
        assert [t["id"] for t in data["epics"][0]["tasks"]] == ["e-001", "e-002", "e-003"]

    def test_includes_archive_subdir(self, tmp_path):
        bp = _write_v4_project(
            tmp_path, epics=[{"id": "e", "name": "E"}],
            tasks=[{"id": "e-001", "title": "a", "epic": "e", "order": 1.0}],
        )
        arch = bp.parent / "tasks" / "archive"
        arch.mkdir()
        fm, body = v3.task_v4_to_file(
            {"id": "e-009", "title": "old", "epic": "e", "order": 9.0, "status": "archived"})
        v3.write_task_file(arch / "e-009.md", fm, body)
        data = v3.load_v4(bp)
        assert [t["id"] for t in data["epics"][0]["tasks"]] == ["e-001", "e-009"]

    def test_orphan_epic_collected(self, tmp_path):
        bp = _write_v4_project(
            tmp_path, epics=[{"id": "e", "name": "E"}],
            tasks=[
                {"id": "e-001", "title": "a", "epic": "e", "order": 1.0},
                {"id": "x-001", "title": "lost", "epic": "ghost", "order": 1.0},
            ],
        )
        data = v3.load_v4(bp)
        assert data["_orphan_tasks"] == ["x-001"]
        assert [t["id"] for t in data["epics"][0]["tasks"]] == ["e-001"]

    def test_body_survives_load(self, tmp_path):
        bp = _write_v4_project(
            tmp_path, epics=[{"id": "e", "name": "E"}],
            tasks=[{"id": "e-001", "title": "a", "epic": "e", "order": 1.0,
                    v3.BODY_KEY: "## Notes\n\nhello"}],
        )
        data = v3.load_v4(bp)
        assert data["epics"][0]["tasks"][0][v3.BODY_KEY] == "## Notes\n\nhello"
