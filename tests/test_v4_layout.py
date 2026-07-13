"""Tests for the v4 sharded storage layout (team-relayout, epic 1)."""
from __future__ import annotations

import os
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


class TestV4Allocators:
    def test_next_task_id_scans_dir_incl_archive(self, tmp_path):
        bp = _write_v4_project(
            tmp_path, epics=[{"id": "e", "name": "E"}],
            tasks=[{"id": "e-001", "title": "a", "epic": "e", "order": 1.0},
                   {"id": "e-002", "title": "b", "epic": "e", "order": 2.0}],
        )
        arch = bp.parent / "tasks" / "archive"
        arch.mkdir()
        fm, body = v3.task_v4_to_file({"id": "e-005", "title": "old", "epic": "e", "order": 5.0})
        v3.write_task_file(arch / "e-005.md", fm, body)
        assert v3.next_task_id(bp, "e") == "e-006"

    def test_next_task_id_empty_epic(self, tmp_path):
        bp = _write_v4_project(tmp_path, epics=[{"id": "e", "name": "E"}], tasks=[])
        assert v3.next_task_id(bp, "e") == "e-001"

    def test_next_task_id_ignores_other_epics(self, tmp_path):
        bp = _write_v4_project(
            tmp_path, epics=[{"id": "e", "name": "E"}, {"id": "auth", "name": "A"}],
            tasks=[{"id": "auth-009", "title": "x", "epic": "auth", "order": 1.0}],
        )
        assert v3.next_task_id(bp, "e") == "e-001"

    def test_next_task_id_rejects_overlapping_epic_prefix(self, tmp_path):
        bp = _write_v4_project(
            tmp_path,
            epics=[{"id": "e", "name": "E"}, {"id": "e-auth", "name": "Auth"}],
            tasks=[
                {"id": "e-002", "title": "valid", "epic": "e", "order": 1.0},
                {"id": "e-auth-009", "title": "other", "epic": "e-auth", "order": 1.0},
            ],
        )
        assert v3.next_task_id(bp, "e") == "e-003"

    def test_next_task_id_rejects_malformed_archive_suffix(self, tmp_path):
        bp = _write_v4_project(
            tmp_path, epics=[{"id": "e", "name": "E"}],
            tasks=[{"id": "e-002", "title": "valid", "epic": "e", "order": 1.0}],
        )
        arch = bp.parent / "tasks" / "archive"
        arch.mkdir()
        fm, body = v3.task_v4_to_file(
            {"id": "e-foo009", "title": "malformed", "epic": "e", "order": 9.0})
        v3.write_task_file(arch / "e-foo009.md", fm, body)
        assert v3.next_task_id(bp, "e") == "e-003"

    def test_next_task_order_is_max_plus_one(self, tmp_path):
        bp = _write_v4_project(
            tmp_path, epics=[{"id": "e", "name": "E"}],
            tasks=[{"id": "e-001", "title": "a", "epic": "e", "order": 1.0},
                   {"id": "e-002", "title": "b", "epic": "e", "order": 2.0}],
        )
        assert v3.next_task_order(bp, "e") == 3.0

    def test_next_task_order_empty_epic_is_one(self, tmp_path):
        bp = _write_v4_project(tmp_path, epics=[{"id": "e", "name": "E"}], tasks=[])
        assert v3.next_task_order(bp, "e") == 1.0

    def test_order_between_is_midpoint(self):
        assert v3.order_between(1.0, 2.0) == 1.5


class TestSaveV4:
    def test_writes_task_files_and_slim_backlog(self, tmp_path):
        tm = tmp_path / ".taskmaster"
        (tm / "tasks").mkdir(parents=True)
        bp = tm / "backlog.yaml"
        bp.write_text(yaml.dump({"meta": {"project": "t", "schema_version": 4},
                                 "epics": [{"id": "e", "name": "E"}], "phases": []}))
        data = {
            "meta": {"project": "t", "schema_version": 4},
            "epics": [{"id": "e", "name": "E", "tasks": [
                {"id": "e-001", "title": "A", "epic": "e", "order": 1.0, "status": "todo"},
            ]}],
            "phases": [],
        }
        v3.save_v4(bp, data)
        # task file exists with all fields
        fm, _ = v3.read_task_file(tm / "tasks" / "e-001.md")
        assert fm["title"] == "A" and fm["epic"] == "e" and fm["status"] == "todo"
        # backlog.yaml carries NO task list
        on_disk = yaml.safe_load(bp.read_text())
        assert "tasks" not in on_disk["epics"][0]

    def test_round_trip_identity(self, tmp_path):
        tm = tmp_path / ".taskmaster"
        (tm / "tasks").mkdir(parents=True)
        bp = tm / "backlog.yaml"
        bp.write_text(yaml.dump({"meta": {"schema_version": 4}, "epics": [], "phases": []}))
        data = {
            "meta": {"schema_version": 4},
            "epics": [{"id": "e", "name": "E", "tasks": [
                {"id": "e-001", "title": "A", "epic": "e", "order": 1.0},
                {"id": "e-002", "title": "B", "epic": "e", "order": 2.0,
                 v3.BODY_KEY: "## Notes\n\nbody"},
            ]}],
            "phases": [],
        }
        v3.save_v4(bp, data)
        reloaded = v3.load_v4(bp)
        tasks = reloaded["epics"][0]["tasks"]
        assert [t["id"] for t in tasks] == ["e-001", "e-002"]
        assert tasks[1][v3.BODY_KEY] == "## Notes\n\nbody"

    def test_private_keys_not_persisted(self, tmp_path):
        tm = tmp_path / ".taskmaster"
        (tm / "tasks").mkdir(parents=True)
        bp = tm / "backlog.yaml"
        bp.write_text(yaml.dump({"meta": {"schema_version": 4}, "epics": [], "phases": []}))
        data = {"meta": {"schema_version": 4}, "epics": [], "phases": [],
                "_orphan_tasks": ["x-001"]}
        v3.save_v4(bp, data)
        assert "_orphan_tasks" not in yaml.safe_load(bp.read_text())

    def test_meta_updated_not_written(self, tmp_path):
        tm = tmp_path / ".taskmaster"
        (tm / "tasks").mkdir(parents=True)
        bp = tm / "backlog.yaml"
        bp.write_text(yaml.dump({"meta": {"schema_version": 4}, "epics": [], "phases": []}))
        data = {"meta": {"schema_version": 4, "updated": "2026-07-11"},
                "epics": [], "phases": []}
        v3.save_v4(bp, data)
        assert "updated" not in yaml.safe_load(bp.read_text())["meta"]

    def test_private_fields_never_persist_at_any_level(self, tmp_path):
        tm = tmp_path / ".taskmaster"
        (tm / "tasks").mkdir(parents=True)
        bp = tm / "backlog.yaml"
        bp.write_text(yaml.dump({"meta": {"schema_version": 4}, "epics": [], "phases": []}))
        data = {
            "meta": {
                "schema_version": 4,
                "_private": "meta",
                "settings": {"keep": True, "_private": "nested-meta"},
            },
            "epics": [{
                "id": "e", "name": "E", "_private": "epic",
                "settings": {"keep": True, "_private": "nested-epic"},
                v3.BODY_KEY: "epic body",
                "tasks": [{
                    "id": "e-001", "title": "A", "epic": "e", "order": 1.0,
                    "_private": "task",
                    "settings": {
                        "keep": True,
                        "_private": "nested-task",
                        "items": [{"keep": True, "_private": "nested-list"}],
                    },
                    v3.BODY_KEY: "task body",
                }],
            }],
            "phases": [{
                "id": "p", "name": "P", "_private": "phase",
                "settings": {"keep": True, "_private": "nested-phase"},
                v3.BODY_KEY: "phase body",
            }],
            "_private": "root",
        }

        v3.save_v4(bp, data)

        def assert_no_private_keys(value):
            if isinstance(value, dict):
                assert all(not key.startswith("_") for key in value)
                for child in value.values():
                    assert_no_private_keys(child)
            elif isinstance(value, list):
                for child in value:
                    assert_no_private_keys(child)

        on_disk = yaml.safe_load(bp.read_text())
        assert_no_private_keys(on_disk)
        assert on_disk["meta"]["settings"] == {"keep": True}
        assert on_disk["epics"][0]["settings"] == {"keep": True}
        assert on_disk["phases"][0]["settings"] == {"keep": True}

        task_fm, task_body = v3.read_task_file(tm / "tasks" / "e-001.md")
        epic_fm, epic_body = v3.read_task_file(tm / "epics" / "e.md")
        phase_fm, phase_body = v3.read_task_file(tm / "phases" / "p.md")
        assert_no_private_keys(task_fm)
        assert_no_private_keys(epic_fm)
        assert_no_private_keys(phase_fm)
        assert task_fm["settings"] == {
            "keep": True, "items": [{"keep": True}],
        }
        assert task_body.removesuffix("\n") == "task body"
        assert epic_body.removesuffix("\n") == "epic body"
        assert phase_body.removesuffix("\n") == "phase body"


class TestDirtyScopedSave:
    def _project(self, tmp_path):
        tm = tmp_path / ".taskmaster"
        (tm / "tasks").mkdir(parents=True)
        bp = tm / "backlog.yaml"
        bp.write_text(yaml.dump({"meta": {"schema_version": 4}, "epics": [], "phases": []}))
        data = {"meta": {"schema_version": 4}, "phases": [],
                "epics": [{"id": "e", "name": "E", "tasks": [
                    {"id": "e-001", "title": "A", "epic": "e", "order": 1.0},
                    {"id": "e-002", "title": "B", "epic": "e", "order": 2.0},
                ]}]}
        v3.save_v4(bp, data)   # baseline write of both files
        return bp, data

    def test_unchanged_task_file_not_rewritten(self, tmp_path):
        import copy
        bp, data = self._project(tmp_path)
        snapshot = copy.deepcopy(data)
        f1 = bp.parent / "tasks" / "e-001.md"
        f2 = bp.parent / "tasks" / "e-002.md"
        m1_before, m2_before = f1.stat().st_mtime_ns, f2.stat().st_mtime_ns
        # Touch only e-002 in memory.
        data["epics"][0]["tasks"][1]["title"] = "B-renamed"
        # Make mtime resolution observable.
        os.utime(f1, ns=(m1_before, m1_before))
        os.utime(f2, ns=(m2_before, m2_before))
        v3.save_v4(bp, data, snapshot=snapshot)
        assert f1.stat().st_mtime_ns == m1_before  # unchanged task not rewritten
        fm2, _ = v3.read_task_file(f2)
        assert fm2["title"] == "B-renamed"

    def test_new_task_written(self, tmp_path):
        import copy
        bp, data = self._project(tmp_path)
        snapshot = copy.deepcopy(data)
        data["epics"][0]["tasks"].append(
            {"id": "e-003", "title": "C", "epic": "e", "order": 3.0})
        v3.save_v4(bp, data, snapshot=snapshot)
        assert (bp.parent / "tasks" / "e-003.md").exists()

    def test_removed_task_file_deleted(self, tmp_path):
        import copy
        bp, data = self._project(tmp_path)
        snapshot = copy.deepcopy(data)
        data["epics"][0]["tasks"] = [t for t in data["epics"][0]["tasks"] if t["id"] != "e-002"]
        v3.save_v4(bp, data, snapshot=snapshot)
        assert not (bp.parent / "tasks" / "e-002.md").exists()
        assert (bp.parent / "tasks" / "e-001.md").exists()


class TestConcurrentDiskMerge:
    def _project(self, tmp_path):
        import copy
        tm = tmp_path / ".taskmaster"
        (tm / "tasks").mkdir(parents=True)
        bp = tm / "backlog.yaml"
        bp.write_text(yaml.dump({"meta": {"schema_version": 4}, "epics": [], "phases": []}))
        data = {"meta": {"schema_version": 4}, "phases": [],
                "epics": [{"id": "e", "name": "E", "tasks": [
                    {"id": "e-001", "title": "A", "epic": "e", "order": 1.0,
                     "status": "todo", "priority": "medium"},
                ]}]}
        v3.save_v4(bp, data)
        return bp, data, copy.deepcopy(data)

    def test_disjoint_disk_field_preserved(self, tmp_path):
        bp, data, snapshot = self._project(tmp_path)
        # Another process adds `assignee` on disk (a field we never touched).
        f = bp.parent / "tasks" / "e-001.md"
        fm, body = v3.read_task_file(f)
        fm["assignee"] = "jdoe"
        v3.write_task_file(f, fm, body)
        # We change only `status` in memory.
        data["epics"][0]["tasks"][0]["status"] = "in-progress"
        v3.save_v4(bp, data, snapshot=snapshot)
        fm2, _ = v3.read_task_file(f)
        assert fm2["status"] == "in-progress"   # our change
        assert fm2["assignee"] == "jdoe"       # disk-only change preserved

    def test_same_field_in_memory_wins(self, tmp_path):
        bp, data, snapshot = self._project(tmp_path)
        f = bp.parent / "tasks" / "e-001.md"
        fm, body = v3.read_task_file(f)
        fm["title"] = "disk title"
        v3.write_task_file(f, fm, body)
        data["epics"][0]["tasks"][0]["title"] = "memory title"
        v3.save_v4(bp, data, snapshot=snapshot)
        fm2, _ = v3.read_task_file(f)
        assert fm2["title"] == "memory title"

    def test_disk_only_change_kept_when_field_untouched(self, tmp_path):
        bp, data, snapshot = self._project(tmp_path)
        f = bp.parent / "tasks" / "e-001.md"
        fm, body = v3.read_task_file(f)
        fm["title"] = "disk title"
        v3.write_task_file(f, fm, body)
        # We change a DIFFERENT field, leaving title at its snapshot value.
        data["epics"][0]["tasks"][0]["status"] = "done"
        v3.save_v4(bp, data, snapshot=snapshot)
        fm2, _ = v3.read_task_file(f)
        assert fm2["title"] == "disk title"   # remote change survives
        assert fm2["status"] == "done"

    def test_disk_private_fields_are_rejected_without_losing_legitimate_edits(
        self, tmp_path
    ):
        bp, data, snapshot = self._project(tmp_path)
        f = bp.parent / "tasks" / "e-001.md"
        fm, _ = v3.read_task_file(f)
        fm["assignee"] = "jdoe"
        fm["_disk_private"] = "must not persist"
        fm["metadata"] = {
            "label": "keep",
            "_nested_private": {"secret": True},
        }
        v3.write_task_file(f, fm, "disk body")

        data["epics"][0]["tasks"][0]["status"] = "done"
        v3.save_v4(bp, data, snapshot=snapshot)

        merged_fm, merged_body = v3.read_task_file(f)
        assert merged_fm["status"] == "done"
        assert merged_fm["assignee"] == "jdoe"
        assert merged_fm["metadata"] == {"label": "keep"}
        assert "_disk_private" not in merged_fm
        assert merged_body.removesuffix("\n") == "disk body"
