"""Tests for v3 layout plumbing: schema_version detection, atomic writes."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import taskmaster_v3 as v3  # noqa: E402


class TestSchemaVersionDetection:
    def test_legacy_no_version_implies_v2(self):
        data = {"meta": {"project": "p", "updated": "2026-04-26"}, "epics": [], "phases": []}
        assert v3.detect_schema_version(data) == v3.SCHEMA_V2

    def test_explicit_v2(self):
        assert v3.detect_schema_version({"meta": {"schema_version": 2}}) == v3.SCHEMA_V2

    def test_explicit_v3(self):
        assert v3.detect_schema_version({"meta": {"schema_version": 3}}) == v3.SCHEMA_V3

    def test_string_version_coerces(self):
        assert v3.detect_schema_version({"meta": {"schema_version": "3"}}) == v3.SCHEMA_V3

    def test_missing_meta_implies_v2(self):
        assert v3.detect_schema_version({}) == v3.SCHEMA_V2

    def test_default_is_v2(self):
        # New backlogs get v2 unless v3 is opted into. Locks the rollout policy.
        assert v3.SCHEMA_DEFAULT == v3.SCHEMA_V2


class TestAtomicWrite:
    def test_writes_content(self, tmp_path: Path):
        target = tmp_path / "out.yaml"
        v3.atomic_write(target, "hello: world\n")
        assert target.read_text(encoding="utf-8") == "hello: world\n"

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "nested" / "deep" / "out.yaml"
        v3.atomic_write(target, "x: 1\n")
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "x: 1\n"

    def test_overwrites_existing(self, tmp_path: Path):
        target = tmp_path / "out.yaml"
        target.write_text("old\n", encoding="utf-8")
        v3.atomic_write(target, "new\n")
        assert target.read_text(encoding="utf-8") == "new\n"

    def test_no_tmp_left_behind(self, tmp_path: Path):
        target = tmp_path / "out.yaml"
        v3.atomic_write(target, "x\n")
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []


class TestParseFrontmatter:
    def test_basic(self):
        text = "---\nid: T-001\ntitle: Hi\n---\nbody line\n"
        fm, body = v3.parse_frontmatter(text)
        assert fm == {"id": "T-001", "title": "Hi"}
        assert body == "body line\n"

    def test_no_frontmatter(self):
        text = "just a body\nwith two lines\n"
        fm, body = v3.parse_frontmatter(text)
        assert fm == {}
        assert body == "just a body\nwith two lines\n"

    def test_empty_string(self):
        fm, body = v3.parse_frontmatter("")
        assert fm == {} and body == ""

    def test_empty_frontmatter(self):
        fm, body = v3.parse_frontmatter("---\n---\nhello\n")
        assert fm == {} and body == "hello\n"

    def test_body_contains_dashes(self):
        text = "---\na: 1\n---\nintro\n\n---\nnot a fence, just markdown rule\n"
        fm, body = v3.parse_frontmatter(text)
        assert fm == {"a": 1}
        assert "---\nnot a fence" in body

    def test_unclosed_frontmatter_treated_as_body(self):
        text = "---\nbroken\nno closer\n"
        fm, body = v3.parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_crlf_normalized(self):
        text = "---\r\nid: T-1\r\n---\r\nhello\r\n"
        fm, body = v3.parse_frontmatter(text)
        assert fm == {"id": "T-1"}
        assert body == "hello\n"

    def test_non_mapping_frontmatter_rejected(self):
        with pytest.raises(ValueError):
            v3.parse_frontmatter("---\n- a\n- b\n---\nbody")

    def test_frontmatter_with_lists(self):
        text = "---\ntags: [foo, bar]\nrelated: []\n---\nb\n"
        fm, _ = v3.parse_frontmatter(text)
        assert fm == {"tags": ["foo", "bar"], "related": []}


class TestRenderFrontmatter:
    def test_basic(self):
        out = v3.render_frontmatter({"id": "T-1"}, "body")
        assert out.startswith("---\nid: T-1\n---\n")
        assert out.endswith("body\n")

    def test_empty_frontmatter_omits_fences(self):
        out = v3.render_frontmatter({}, "just body")
        assert "---" not in out
        assert out == "just body\n"

    def test_empty_body_with_frontmatter(self):
        out = v3.render_frontmatter({"id": "T-1"}, "")
        assert out == "---\nid: T-1\n---\n"

    def test_roundtrip(self):
        fm = {"id": "T-7", "title": "test", "tags": ["a", "b"]}
        body = "## Section\n\nSome content.\n"
        rendered = v3.render_frontmatter(fm, body)
        fm2, body2 = v3.parse_frontmatter(rendered)
        assert fm2 == fm
        assert body2 == body


class TestTaskFileIO:
    def test_write_then_read(self, tmp_path: Path):
        path = tmp_path / "T-001.md"
        fm = {"id": "T-001", "title": "Build it"}
        body = "## Description\nPlain text.\n"
        v3.write_task_file(path, fm, body)
        fm2, body2 = v3.read_task_file(path)
        assert fm2 == fm
        assert body2 == body

    def test_write_creates_parent(self, tmp_path: Path):
        path = tmp_path / "tasks" / "T-001.md"
        v3.write_task_file(path, {"id": "T-001"}, "x")
        assert path.exists()

    def test_write_atomic_no_tmp(self, tmp_path: Path):
        path = tmp_path / "T-001.md"
        v3.write_task_file(path, {"id": "T-001"}, "x")
        assert list(tmp_path.glob("*.tmp")) == []


class TestV3LoadSave:
    def _v3_backlog(self) -> dict:
        return {
            "meta": {"project": "p", "schema_version": 3, "updated": "2026-04-26"},
            "context": {},
            "epics": [
                {
                    "id": "features",
                    "name": "Features",
                    "tasks": [
                        {
                            "id": "T-001",
                            "title": "Build login",
                            "status": "in-progress",
                            "priority": "high",
                            "description": "Wire up the login form.",
                            "notes": "Watch for cookie scope.",
                            v3.BODY_KEY: "## Decisions\nWent with cookie auth.\n",
                        },
                        {
                            "id": "T-002",
                            "title": "No heavy fields",
                            "status": "todo",
                            "priority": "low",
                        },
                    ],
                }
            ],
            "phases": [],
        }

    def test_save_writes_slim_index_and_task_files(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        data = self._v3_backlog()
        v3.save_v3(bp, data)

        # Slim index: heavy fields stripped from yaml task entries
        import yaml as _y
        loaded_yaml = _y.safe_load(bp.read_text(encoding="utf-8"))
        t1 = loaded_yaml["epics"][0]["tasks"][0]
        assert "description" not in t1
        assert "notes" not in t1
        assert t1["id"] == "T-001"
        assert t1["title"] == "Build login"

        # T-001 has heavy content → file written
        assert (tmp_path / ".taskmaster" / "tasks" / "T-001.md").exists()
        # T-002 has no heavy content → no file
        assert not (tmp_path / ".taskmaster" / "tasks" / "T-002.md").exists()

    def test_load_merges_heavy_fields_back(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        original = self._v3_backlog()
        v3.save_v3(bp, original)
        loaded = v3.load_v3(bp)

        t1 = loaded["epics"][0]["tasks"][0]
        assert t1["description"] == "Wire up the login form."
        assert t1["notes"] == "Watch for cookie scope."
        assert t1[v3.BODY_KEY] == "## Decisions\nWent with cookie auth.\n"

        t2 = loaded["epics"][0]["tasks"][1]
        assert "description" not in t2
        assert v3.BODY_KEY not in t2

    def test_roundtrip_preserves_data(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        original = self._v3_backlog()
        v3.save_v3(bp, original)
        loaded = v3.load_v3(bp)

        # All task fields survive the roundtrip
        t1_orig = original["epics"][0]["tasks"][0]
        t1_loaded = loaded["epics"][0]["tasks"][0]
        for key in ("id", "title", "status", "priority", "description", "notes", v3.BODY_KEY):
            assert t1_loaded[key] == t1_orig[key], f"field {key!r} differs"

    def test_load_tolerates_missing_task_files(self, tmp_path: Path):
        # Hand-craft a v3 backlog with a slim task entry but no per-task file.
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        bp.parent.mkdir(parents=True)
        import yaml as _y
        bp.write_text(
            _y.dump({
                "meta": {"schema_version": 3},
                "epics": [{"id": "e", "tasks": [{"id": "T-99", "title": "Phantom"}]}],
            }),
            encoding="utf-8",
        )
        data = v3.load_v3(bp)
        assert data["epics"][0]["tasks"][0]["id"] == "T-99"
        assert "description" not in data["epics"][0]["tasks"][0]

    def test_task_file_path(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        assert v3.task_file_path(bp, "T-001") == tmp_path / ".taskmaster" / "tasks" / "T-001.md"


class TestMigrateV2ToV3:
    def _v2_backlog(self) -> dict:
        # Note: no schema_version → implies v2 (legacy)
        return {
            "meta": {"project": "p", "updated": "2026-04-26"},
            "context": {},
            "epics": [
                {
                    "id": "e1",
                    "name": "Features",
                    "tasks": [
                        {
                            "id": "T-001",
                            "title": "Has body fields",
                            "status": "todo",
                            "description": "Detailed description.",
                            "notes": "A note.",
                        },
                        {
                            "id": "T-002",
                            "title": "Empty task",
                            "status": "todo",
                        },
                    ],
                }
            ],
            "phases": [],
        }

    def _write_v2(self, tmp_path: Path) -> Path:
        import yaml as _y
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        bp.parent.mkdir(parents=True)
        bp.write_text(_y.dump(self._v2_backlog()), encoding="utf-8")
        return bp

    def test_migrate_writes_schema_version(self, tmp_path: Path):
        bp = self._write_v2(tmp_path)
        summary = v3.migrate_v2_to_v3(bp)
        assert summary["status"] == "migrated"
        assert summary["schema_before"] == v3.SCHEMA_V2
        assert summary["schema_after"] == v3.SCHEMA_V3

        import yaml as _y
        loaded = _y.safe_load(bp.read_text(encoding="utf-8"))
        assert loaded["meta"]["schema_version"] == v3.SCHEMA_V3

    def test_migrate_extracts_heavy_fields(self, tmp_path: Path):
        bp = self._write_v2(tmp_path)
        v3.migrate_v2_to_v3(bp)

        # Heavy fields stripped from yaml
        import yaml as _y
        loaded = _y.safe_load(bp.read_text(encoding="utf-8"))
        t1 = loaded["epics"][0]["tasks"][0]
        assert "description" not in t1
        assert "notes" not in t1
        assert t1["title"] == "Has body fields"

        # Per-task file written for T-001 only
        assert (tmp_path / ".taskmaster" / "tasks" / "T-001.md").exists()
        assert not (tmp_path / ".taskmaster" / "tasks" / "T-002.md").exists()

    def test_migration_is_lossless(self, tmp_path: Path):
        bp = self._write_v2(tmp_path)
        original = self._v2_backlog()
        v3.migrate_v2_to_v3(bp)
        loaded = v3.load_v3(bp)
        t1 = loaded["epics"][0]["tasks"][0]
        orig_t1 = original["epics"][0]["tasks"][0]
        assert t1["description"] == orig_t1["description"]
        assert t1["notes"] == orig_t1["notes"]
        assert t1["title"] == orig_t1["title"]
        assert t1["status"] == orig_t1["status"]

    def test_idempotent(self, tmp_path: Path):
        bp = self._write_v2(tmp_path)
        v3.migrate_v2_to_v3(bp)
        summary2 = v3.migrate_v2_to_v3(bp)
        assert summary2["status"] == "already_v3"
        assert summary2["task_files_written"] == []

    def test_summary_lists_written_files(self, tmp_path: Path):
        bp = self._write_v2(tmp_path)
        summary = v3.migrate_v2_to_v3(bp)
        assert "tasks/T-001.md" in summary["task_files_written"][0].replace("\\", "/")
        assert len(summary["task_files_written"]) == 1


class TestSnapshot:
    def _backlog(self) -> dict:
        return {
            "meta": {"schema_version": 3},
            "epics": [
                {
                    "id": "e1",
                    "tasks": [
                        {"id": "T-001", "title": "A", "status": "todo", "priority": "high", "stage": 1},
                        {"id": "T-002", "title": "B", "status": "in-progress", "priority": "medium"},
                    ],
                }
            ],
            "phases": [
                {"id": "setup", "status": "done"},
                {"id": "dev", "status": "active"},
            ],
        }

    def test_take_snapshot_captures_slim_fields(self):
        snap = v3.take_snapshot(self._backlog())
        assert snap["tasks"]["T-001"]["status"] == "todo"
        assert snap["tasks"]["T-001"]["priority"] == "high"
        assert snap["tasks"]["T-001"]["stage"] == 1
        assert snap["tasks"]["T-001"]["epic"] == "e1"
        # T-002 has no stage → omitted
        assert "stage" not in snap["tasks"]["T-002"]
        assert snap["phase_active"] == "dev"

    def test_take_snapshot_no_active_phase(self):
        data = {"epics": [], "phases": [{"id": "a", "status": "planned"}]}
        snap = v3.take_snapshot(data)
        assert snap["phase_active"] is None

    def test_structural_hash_stable(self):
        snap1 = v3.take_snapshot(self._backlog())
        snap2 = v3.take_snapshot(self._backlog())
        assert snap1["structural_hash"] == snap2["structural_hash"]

    def test_structural_hash_changes_on_status_change(self):
        d1 = self._backlog()
        snap1 = v3.take_snapshot(d1)
        d1["epics"][0]["tasks"][0]["status"] = "done"
        snap2 = v3.take_snapshot(d1)
        assert snap1["structural_hash"] != snap2["structural_hash"]

    def test_take_snapshot_includes_metadata(self):
        snap = v3.take_snapshot(self._backlog())
        assert snap["schema_version"] == v3.SCHEMA_V3
        assert snap["structural_hash"].startswith("sha256:")
        assert "T" in snap["taken_at"]  # ISO 8601 has the 'T'

    def test_write_and_read_roundtrip(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        bp.parent.mkdir(parents=True)
        snap = v3.take_snapshot(self._backlog())
        v3.write_snapshot(bp, snap)

        sp = v3.snapshot_path(bp)
        assert sp.exists()
        loaded = v3.read_snapshot(bp)
        assert loaded == snap

    def test_read_snapshot_missing_returns_none(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        assert v3.read_snapshot(bp) is None

    def test_read_snapshot_corrupt_returns_none(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        sp = v3.snapshot_path(bp)
        sp.parent.mkdir(parents=True)
        sp.write_text("not valid json {", encoding="utf-8")
        assert v3.read_snapshot(bp) is None

    def test_snapshot_path_under_snapshots_dir(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        assert v3.snapshot_path(bp) == tmp_path / ".taskmaster" / "snapshots" / "last.json"


class TestRecapDiff:
    def _backlog(self) -> dict:
        return {
            "epics": [
                {
                    "id": "e1",
                    "tasks": [
                        {"id": "T-001", "title": "A", "status": "todo", "priority": "high"},
                        {"id": "T-002", "title": "B", "status": "in-progress", "priority": "medium"},
                    ],
                }
            ],
            "phases": [{"id": "dev", "status": "active"}],
        }

    def test_no_prior_snapshot(self):
        diff = v3.diff_against_snapshot(self._backlog(), None)
        assert diff["no_prior_snapshot"] is True
        assert diff["tasks_added"] == []

    def test_no_changes(self):
        data = self._backlog()
        snap = v3.take_snapshot(data)
        diff = v3.diff_against_snapshot(data, snap)
        assert diff["no_changes"] is True
        assert diff["tasks_added"] == []
        assert diff["tasks_changed"] == []

    def test_task_added(self):
        d1 = self._backlog()
        snap = v3.take_snapshot(d1)
        d2 = self._backlog()
        d2["epics"][0]["tasks"].append(
            {"id": "T-003", "title": "C", "status": "todo", "priority": "low"}
        )
        diff = v3.diff_against_snapshot(d2, snap)
        assert len(diff["tasks_added"]) == 1
        assert diff["tasks_added"][0]["id"] == "T-003"
        assert diff["tasks_added"][0]["status"] == "todo"

    def test_task_removed(self):
        d1 = self._backlog()
        snap = v3.take_snapshot(d1)
        d2 = self._backlog()
        d2["epics"][0]["tasks"].pop(0)  # remove T-001
        diff = v3.diff_against_snapshot(d2, snap)
        assert len(diff["tasks_removed"]) == 1
        assert diff["tasks_removed"][0]["id"] == "T-001"

    def test_task_status_change(self):
        d1 = self._backlog()
        snap = v3.take_snapshot(d1)
        d2 = self._backlog()
        d2["epics"][0]["tasks"][0]["status"] = "done"
        diff = v3.diff_against_snapshot(d2, snap)
        assert len(diff["tasks_changed"]) == 1
        c = diff["tasks_changed"][0]
        assert c["id"] == "T-001"
        assert c["changes"][0] == {"field": "status", "before": "todo", "after": "done"}

    def test_task_moved_between_epics(self):
        d1 = {
            "epics": [
                {"id": "e1", "tasks": [{"id": "T-1", "title": "A", "status": "todo"}]},
                {"id": "e2", "tasks": []},
            ],
            "phases": [],
        }
        snap = v3.take_snapshot(d1)
        d2 = {
            "epics": [
                {"id": "e1", "tasks": []},
                {"id": "e2", "tasks": [{"id": "T-1", "title": "A", "status": "todo"}]},
            ],
            "phases": [],
        }
        diff = v3.diff_against_snapshot(d2, snap)
        # Reported as a change (epic field), not add+remove
        assert diff["tasks_added"] == []
        assert diff["tasks_removed"] == []
        assert len(diff["tasks_changed"]) == 1
        c = diff["tasks_changed"][0]["changes"][0]
        assert c["field"] == "epic"
        assert c["before"] == "e1"
        assert c["after"] == "e2"

    def test_phase_change(self):
        d1 = {"epics": [], "phases": [{"id": "setup", "status": "active"}]}
        snap = v3.take_snapshot(d1)
        d2 = {
            "epics": [],
            "phases": [
                {"id": "setup", "status": "done"},
                {"id": "dev", "status": "active"},
            ],
        }
        diff = v3.diff_against_snapshot(d2, snap)
        assert diff["phase_changed"] == {"before": "setup", "after": "dev"}

    def test_format_recap_no_prior(self):
        out = v3.format_recap(v3.diff_against_snapshot({"epics": [], "phases": []}, None))
        assert "No prior snapshot" in out

    def test_format_recap_no_changes(self):
        d = self._backlog()
        snap = v3.take_snapshot(d)
        out = v3.format_recap(v3.diff_against_snapshot(d, snap))
        assert "No changes" in out

    def test_format_recap_with_changes(self):
        d1 = self._backlog()
        snap = v3.take_snapshot(d1)
        d2 = self._backlog()
        d2["epics"][0]["tasks"][0]["status"] = "done"
        d2["epics"][0]["tasks"].append(
            {"id": "T-003", "title": "New", "status": "todo", "priority": "low"}
        )
        out = v3.format_recap(v3.diff_against_snapshot(d2, snap))
        assert "+ T T-003" in out
        assert "~ T T-001 status: todo → done" in out


class TestSnapshotHookScript:
    """End-to-end test for the PreCompact hook script."""

    def test_hook_writes_snapshot_for_v2_backlog(self, tmp_path: Path, monkeypatch):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        bp.parent.mkdir(parents=True)
        import yaml as _y
        _y.dump  # touch to ensure import
        bp.write_text(_y.dump({
            "meta": {"project": "p"},
            "epics": [{"id": "e1", "tasks": [{"id": "T-1", "title": "A", "status": "todo"}]}],
            "phases": [],
        }), encoding="utf-8")

        monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
        import subprocess
        hook = Path(__file__).parent.parent / "hooks" / "snapshot.py"
        result = subprocess.run(
            [sys.executable, str(hook)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={**os.environ, "TASKMASTER_ROOT": str(tmp_path)},
        )
        assert result.returncode == 0
        assert (tmp_path / ".taskmaster" / "snapshots" / "last.json").exists()

    def test_hook_no_backlog_no_error(self, tmp_path: Path):
        import subprocess
        hook = Path(__file__).parent.parent / "hooks" / "snapshot.py"
        result = subprocess.run(
            [sys.executable, str(hook)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={**os.environ, "TASKMASTER_ROOT": str(tmp_path)},
        )
        assert result.returncode == 0
        # No snapshot dir created
        assert not (tmp_path / ".taskmaster").exists()
