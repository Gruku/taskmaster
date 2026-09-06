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


class TestStoreExport:
    """`save_v4` is gone — the store owns every write under `.taskmaster/`.

    These are the same contracts, asserted against the exporter that replaced
    it: every task field lands in `tasks/<id>.md`, `backlog.yaml` carries no
    task lists, `meta.updated` is never written, and no `_private` key reaches
    the projection at any nesting level.
    """

    def _adopt(self, tmp_path, data):
        from taskmaster import store

        tm = tmp_path / ".taskmaster"
        tm.mkdir(parents=True, exist_ok=True)
        (tm / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
        bp = tm / "backlog.yaml"
        bp.write_text(yaml.dump(data), encoding="utf-8")
        store.reset_for_tests()
        return bp, store.open_store(backlog_path=bp, session="v4-export-test")

    def test_writes_task_files_and_slim_backlog(self, tmp_path):
        bp, _opened = self._adopt(tmp_path, {
            "meta": {"project": "t", "schema_version": 3},
            "epics": [{"id": "e", "name": "E", "tasks": [
                {"id": "e-001", "title": "A", "epic": "e", "order": 1.0, "status": "todo"},
            ]}],
            "phases": [],
        })
        fm, _ = v3.read_task_file(bp.parent / "tasks" / "e-001.md")
        assert fm["title"] == "A" and fm["epic"] == "e" and fm["status"] == "todo"
        on_disk = yaml.safe_load(bp.read_text())
        assert "tasks" not in on_disk["epics"][0]

    def test_round_trip_identity(self, tmp_path):
        bp, opened = self._adopt(tmp_path, {
            "meta": {"schema_version": 3},
            "epics": [{"id": "e", "name": "E", "tasks": [
                {"id": "e-001", "title": "A", "epic": "e", "order": 1.0},
                {"id": "e-002", "title": "B", "epic": "e", "order": 2.0,
                 v3.BODY_KEY: "## Notes\n\nbody"},
            ]}],
            "phases": [],
        })
        reloaded = opened.load_dict()
        tasks = reloaded["epics"][0]["tasks"]
        assert [t["id"] for t in tasks] == ["e-001", "e-002"]
        assert tasks[1][v3.BODY_KEY].removesuffix("\n") == "## Notes\n\nbody"

    def test_meta_updated_not_written(self, tmp_path):
        bp, _opened = self._adopt(tmp_path, {
            "meta": {"schema_version": 3, "updated": "2026-07-11"},
            "epics": [], "phases": [],
        })
        assert "updated" not in yaml.safe_load(bp.read_text())["meta"]

    def test_private_keys_are_stripped_at_every_level(self, tmp_path):
        """`_v4_strip_private_fields` is the surviving helper the exporter uses;
        a `_private` key must not reach the projection from any depth."""
        stripped = v3._v4_strip_private_fields({
            "keep": True,
            "_private": "root",
            "settings": {
                "keep": True,
                "_private": "nested",
                "items": [{"keep": True, "_private": "in-list"}],
            },
            v3.BODY_KEY: "body",
        })

        def assert_no_private_keys(value):
            if isinstance(value, dict):
                assert all(not key.startswith("_") for key in value)
                for child in value.values():
                    assert_no_private_keys(child)
            elif isinstance(value, list):
                for child in value:
                    assert_no_private_keys(child)

        assert_no_private_keys(stripped)
        assert stripped["settings"] == {"keep": True, "items": [{"keep": True}]}
        # `preserve_body=True` is how the exporter keeps the body it is about to
        # write while still dropping every other private key.
        kept = v3._v4_strip_private_fields(
            {"keep": True, "_private": "x", v3.BODY_KEY: "body"}, preserve_body=True
        )
        assert kept[v3.BODY_KEY] == "body"
        assert "_private" not in kept

    def test_private_fields_never_reach_the_projection(self, tmp_path):
        bp, _opened = self._adopt(tmp_path, {
            "meta": {"schema_version": 3, "settings": {"keep": True}},
            "epics": [{"id": "e", "name": "E", "tasks": [
                {"id": "e-001", "title": "A", "epic": "e", "order": 1.0,
                 "settings": {"keep": True, "items": [{"keep": True}]},
                 v3.BODY_KEY: "task body"},
            ]}],
            "phases": [{"id": "p", "name": "P", v3.BODY_KEY: "phase body"}],
        })

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
        task_fm, task_body = v3.read_task_file(bp.parent / "tasks" / "e-001.md")
        assert_no_private_keys(task_fm)
        assert task_fm["settings"] == {"keep": True, "items": [{"keep": True}]}
        assert task_body.removesuffix("\n") == "task body"


# `TestDirtyScopedSave` covered `save_v4(snapshot=...)`: writing only the tasks
# that changed and deleting the file of one that went away. The store replaced
# that with per-row export intents and explicit `tx.archive`/`tx.delete`, and
# the successor coverage lives in tests/test_store_projection.py (missing file
# re-exported, archive moves the file, export failure stays dirty and drains)
# and tests/test_store_removals.py.


class TestThreeWayFieldMerge:
    """`_three_way_merge_fields` survived `save_v4` and is what the store's
    dirty-projection merge (`Store._merge_dirty_external_edit`) runs, so the
    concurrent-edit contract is asserted directly on it here. The end-to-end
    path is tests/test_store_merge_and_derived.py."""

    BASE = {"id": "e-001", "title": "A", "status": "todo", "priority": "medium"}

    def test_disjoint_remote_field_preserved(self):
        theirs = {**self.BASE, "assignee": "jdoe"}
        ours = {**self.BASE, "status": "in-progress"}
        merged = v3._three_way_merge_fields(self.BASE, ours, theirs)
        assert merged["status"] == "in-progress"   # our change
        assert merged["assignee"] == "jdoe"        # remote-only change preserved

    def test_same_field_local_wins(self):
        theirs = {**self.BASE, "title": "disk title"}
        ours = {**self.BASE, "title": "memory title"}
        merged = v3._three_way_merge_fields(self.BASE, ours, theirs)
        assert merged["title"] == "memory title"

    def test_remote_change_kept_when_field_untouched(self):
        theirs = {**self.BASE, "title": "disk title"}
        ours = {**self.BASE, "status": "done"}
        merged = v3._three_way_merge_fields(self.BASE, ours, theirs)
        assert merged["title"] == "disk title"   # remote change survives
        assert merged["status"] == "done"

    def test_deletion_on_either_side_is_a_change(self):
        ours = {k: v for k, v in self.BASE.items() if k != "priority"}
        merged = v3._three_way_merge_fields(self.BASE, ours, dict(self.BASE))
        assert "priority" not in merged

    def test_remote_private_fields_are_rejected_before_the_merge(self):
        """The exporter strips private keys off the disk document first, so a
        hand-edited `_private` cannot ride a legitimate remote edit into the
        projection."""
        disk = {**self.BASE, "assignee": "jdoe", "_disk_private": "must not persist",
                "metadata": {"label": "keep", "_nested_private": {"secret": True}}}
        theirs = v3._v4_strip_private_fields(disk)
        ours = {**self.BASE, "status": "done"}
        merged = v3._three_way_merge_fields(self.BASE, ours, theirs)
        assert merged["status"] == "done"
        assert merged["assignee"] == "jdoe"
        assert merged["metadata"] == {"label": "keep"}
        assert "_disk_private" not in merged
