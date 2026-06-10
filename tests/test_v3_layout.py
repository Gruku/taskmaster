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

    def test_roundtrip_idempotent_with_leading_blank_line(self):
        """render->parse->render must be stable when the body has a leading
        blank line (B-007). parse_frontmatter drops one leading newline after
        the closing fence, so render must strip leading newlines too — otherwise
        a file written, read, and re-written changes on disk every cycle.
        """
        fm = {"id": "T-9"}
        body = "\nLeading blank line.\n"
        once = v3.render_frontmatter(fm, body)
        fm2, body2 = v3.parse_frontmatter(once)
        twice = v3.render_frontmatter(fm2, body2)
        assert once == twice
        assert not body2.startswith("\n")


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


class TestHandoverHelpers:
    def test_slugify_basic(self):
        assert v3.slugify("Login impl, OAuth pending") == "login-impl-oauth-pending"

    def test_slugify_empty(self):
        assert v3.slugify("") == "untitled"

    def test_slugify_punctuation_only(self):
        assert v3.slugify("!!!") == "untitled"

    def test_slugify_caps_length(self):
        s = v3.slugify("a" * 100)
        assert len(s) <= 40

    def test_make_handover_id(self):
        assert v3.make_handover_id("2026-04-26", "Login impl") == "2026-04-26-login-impl"

    def test_handover_path(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        p = v3.handover_path(bp, "2026-04-26-x")
        assert p == tmp_path / ".taskmaster" / "handovers" / "2026-04-26-x.md"


class TestWriteHandover:
    def test_write_basic(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        hid, target = v3.write_handover(
            bp,
            tldr="Login impl, OAuth pending",
            next_action="Resume IMPLEMENT once legal confirms.",
            body="## Decisions\nWent stateful.\n",
            task_ids=["features-001"],
            session_kind="end-of-day",
            when="2026-04-26",
        )
        assert hid == "2026-04-26-login-impl-oauth-pending"
        assert target.exists()
        fm, body = v3.read_handover(bp, hid)
        assert fm["tldr"] == "Login impl, OAuth pending"
        assert fm["next_action"] == "Resume IMPLEMENT once legal confirms."
        assert fm["task_ids"] == ["features-001"]
        assert fm["session_kind"] == "continuity"  # "end-of-day" normalized to "continuity" on write
        assert fm["date"] == "2026-04-26"
        assert "Decisions" in body

    def test_empty_tldr_rejected(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        with pytest.raises(ValueError):
            v3.write_handover(bp, tldr="")

    def test_id_collision_gets_suffix(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        hid1, _ = v3.write_handover(bp, tldr="Same", when="2026-04-26", body="first")
        hid2, _ = v3.write_handover(bp, tldr="Same", when="2026-04-26", body="second")
        assert hid1 != hid2
        assert hid1 == "2026-04-26-same"
        assert hid2 == "2026-04-26-same-2"

    def test_context_size_optional_field(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        hid, _ = v3.write_handover(bp, tldr="Big session", when="2026-04-26", context_size_at_write="320k")
        fm, _ = v3.read_handover(bp, hid)
        assert fm["context_size_at_write"] == "320k"

    def test_omit_context_size_when_unset(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        hid, _ = v3.write_handover(bp, tldr="Small", when="2026-04-26")
        fm, _ = v3.read_handover(bp, hid)
        assert "context_size_at_write" not in fm


class TestListHandovers:
    def test_empty_when_no_dir(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        assert v3.list_handover_ids(bp) == []
        assert v3.latest_handover_id(bp) is None

    def test_sorted_newest_first(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        v3.write_handover(bp, tldr="A", when="2026-04-25")
        v3.write_handover(bp, tldr="B", when="2026-04-26")
        v3.write_handover(bp, tldr="C", when="2026-04-24")
        ids = v3.list_handover_ids(bp)
        assert ids[0].startswith("2026-04-26")
        assert ids[-1].startswith("2026-04-24")
        assert v3.latest_handover_id(bp) == ids[0]


class TestHandoverIndex:
    def _bp(self, tmp_path: Path) -> Path:
        return tmp_path / ".taskmaster" / "backlog.yaml"

    def test_sync_populates_index(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        v3.write_handover(bp, tldr="First", when="2026-04-25", task_ids=["T-1"])
        v3.write_handover(bp, tldr="Second", when="2026-04-26", task_ids=["T-2"])
        data: dict = {}
        v3.sync_handover_index(data, bp)
        assert len(data["handovers"]) == 2
        assert data["handovers"][0]["id"].startswith("2026-04-26")
        assert data["handovers"][0]["task_ids"] == ["T-2"]

    def test_sync_archives_overflow(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        # Write 5 handovers, cap at 3 → 2 archived.
        for i in range(5):
            v3.write_handover(bp, tldr=f"h{i}", when=f"2026-04-{20 + i}")
        data: dict = {}
        v3.sync_handover_index(data, bp, cap=3)
        assert len(data["handovers"]) == 3
        archive = bp.parent / "handovers" / "_archive" / "2026"
        assert archive.exists()
        archived = sorted(archive.glob("*.md"))
        assert len(archived) == 2
        # Oldest two got archived
        assert any("2026-04-20" in p.name for p in archived)
        assert any("2026-04-21" in p.name for p in archived)
        # Newest ones still in handovers/
        live = sorted((bp.parent / "handovers").glob("*.md"))
        assert len(live) == 3

    def test_index_entry_shape(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        v3.write_handover(
            bp,
            tldr="Day end",
            next_action="Resume",
            task_ids=["T-1"],
            session_kind="end-of-day",
            when="2026-04-26",
        )
        data: dict = {}
        v3.sync_handover_index(data, bp)
        entry = data["handovers"][0]
        # id, date, tldr, next_action, task_ids, session_kind, status, created, flag_reason
        # status and created were added by the handover-status feature (Tasks 1-12)
        # flag_reason was added by Plan B parallel-handovers (optional, present when flagged)
        assert set(entry.keys()) <= {
            "id", "date", "tldr", "next_action", "task_ids", "session_kind",
            "status", "created", "flag_reason",
        }
        assert entry["session_kind"] == "continuity"  # "end-of-day" normalized to "continuity" on write
        assert entry["status"] in {"open", "closed", "superseded"}

    def test_archive_year_inferred(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        v3.write_handover(bp, tldr="x", when="2025-12-31")
        v3.write_handover(bp, tldr="y", when="2026-01-01")
        v3.write_handover(bp, tldr="z", when="2026-04-26")
        data: dict = {}
        v3.sync_handover_index(data, bp, cap=1)
        # 2 archived, split across years
        assert (bp.parent / "handovers" / "_archive" / "2025").exists()
        assert (bp.parent / "handovers" / "_archive" / "2026").exists()


class TestIssues:
    def _bp(self, tmp_path: Path) -> Path:
        return tmp_path / ".taskmaster" / "backlog.yaml"

    def test_next_id_allocates_sequentially(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        assert v3.next_issue_id(bp) == "ISS-001"
        v3.write_issue(bp, title="A", severity="P1", impact="fixture evidence.")
        assert v3.next_issue_id(bp) == "ISS-002"
        v3.write_issue(bp, title="B", severity="P0", impact="fixture evidence.")
        assert v3.next_issue_id(bp) == "ISS-003"

    def test_create_and_read_roundtrip(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        iid, target = v3.write_issue(
            bp,
            title="Login accepts whitespace password",
            severity="P1",
            impact="Effectively no password.",
            components=["auth"],
            location=["src/auth/validate.ts:42"],
            related_tasks=["features-007"],
            discovered="2026-04-15",
            body="## Repro\n1. Submit empty password\n",
        )
        assert iid == "ISS-001"
        assert target.exists()
        fm, body = v3.read_issue(bp, iid)
        assert fm["title"] == "Login accepts whitespace password"
        assert fm["severity"] == "P1"
        assert fm["status"] == "open"
        assert fm["components"] == ["auth"]
        assert fm["location"] == ["src/auth/validate.ts:42"]
        assert fm["related_tasks"] == ["features-007"]
        assert "Repro" in body

    def test_invalid_severity_rejected(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        with pytest.raises(ValueError):
            v3.write_issue(bp, title="x", severity="urgent")

    def test_invalid_status_rejected(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        with pytest.raises(ValueError):
            v3.write_issue(bp, title="x", severity="P1", status="bogus")

    def test_fixed_requires_fixed_in_task(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        iid, _ = v3.write_issue(bp, title="x", severity="P1", impact="fixture evidence.")
        with pytest.raises(ValueError):
            v3.update_issue(bp, iid, status="fixed")

    def test_fixed_with_task_sets_resolved(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        iid, _ = v3.write_issue(bp, title="x", severity="P1", impact="fixture evidence.")
        fm, _ = v3.update_issue(bp, iid, status="fixed", fixed_in_task="features-007")
        assert fm["status"] == "fixed"
        assert fm["resolved"]  # ISO date populated

    def test_duplicate_requires_target(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        iid, _ = v3.write_issue(bp, title="x", severity="P1", impact="fixture evidence.")
        with pytest.raises(ValueError):
            v3.update_issue(bp, iid, status="duplicate")
        v3.update_issue(bp, iid, status="duplicate", duplicate_of="ISS-002")  # ok

    def test_index_sorted_by_severity(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        v3.write_issue(bp, title="low", severity="P3", impact="fixture evidence.")
        v3.write_issue(bp, title="critical", severity="P0", impact="fixture evidence.")
        v3.write_issue(bp, title="high", severity="P1", impact="fixture evidence.")
        data: dict = {}
        v3.sync_issue_index(data, bp)
        sevs = [e["severity"] for e in data["issues"]]
        assert sevs == ["P0", "P1", "P3"]

    def test_index_entry_is_slim(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        v3.write_issue(
            bp,
            title="x",
            severity="P2",
            impact="long impact text" * 100,
            body="long body" * 1000,
        )
        data: dict = {}
        v3.sync_issue_index(data, bp)
        entry = data["issues"][0]
        # impact, body etc not in index
        assert set(entry.keys()) <= {
            "id", "title", "status", "severity", "components", "related_tasks"
        }


class TestLessons:
    def _bp(self, tmp_path: Path) -> Path:
        return tmp_path / ".taskmaster" / "backlog.yaml"

    def test_create_and_read(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        lid, target = v3.write_lesson(
            bp,
            title="Always read auth/session.ts before editing auth flow",
            kind="gotcha",
            triggers={"files": ["src/auth/**"], "task_titles_match": ["auth", "login"]},
            body="## Why\nNon-obvious refresh interaction.\n",
        )
        assert lid == "L-001"
        assert target.exists()
        fm, body = v3.read_lesson(bp, lid)
        assert fm["kind"] == "gotcha"
        assert fm["tier"] == "active"
        assert fm["reinforce_count"] == 0
        assert fm["last_reinforced"] is None
        assert "Why" in body

    def test_invalid_kind(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        with pytest.raises(ValueError):
            v3.write_lesson(bp, title="x", kind="tip")

    def test_reinforce(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        lid, _ = v3.write_lesson(bp, title="x", kind="gotcha")
        fm = v3.reinforce_lesson(bp, lid)
        assert fm["reinforce_count"] == 1
        assert fm["last_reinforced"] is not None
        v3.reinforce_lesson(bp, lid)
        v3.reinforce_lesson(bp, lid)
        fm, _ = v3.read_lesson(bp, lid)
        assert fm["reinforce_count"] == 3

    def test_promotion_eligibility(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        lid, _ = v3.write_lesson(bp, title="x", kind="gotcha")
        for _ in range(v3.LESSON_PROMOTE_REINFORCE):
            v3.reinforce_lesson(bp, lid)
        fm, _ = v3.read_lesson(bp, lid)
        assert v3.lesson_eligible_for_promotion(fm) is True

        # patterns aren't auto-promoted
        lid2, _ = v3.write_lesson(bp, title="y", kind="pattern")
        for _ in range(v3.LESSON_PROMOTE_REINFORCE):
            v3.reinforce_lesson(bp, lid2)
        fm2, _ = v3.read_lesson(bp, lid2)
        assert v3.lesson_eligible_for_promotion(fm2) is False

    def test_decay_eligibility(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        lid, _ = v3.write_lesson(bp, title="x", kind="gotcha")
        # Force last_reinforced way back
        from datetime import date as _date, timedelta
        old = (_date.today() - timedelta(days=v3.LESSON_DECAY_DAYS + 1)).isoformat()
        v3.update_lesson(bp, lid, last_reinforced=old, reinforce_count=0)
        fm, _ = v3.read_lesson(bp, lid)
        assert v3.lesson_eligible_for_decay(fm) is True

        # but well-reinforced lessons don't decay
        v3.update_lesson(bp, lid, reinforce_count=v3.LESSON_DECAY_REINFORCE + 1)
        fm, _ = v3.read_lesson(bp, lid)
        assert v3.lesson_eligible_for_decay(fm) is False

    def test_match_by_title_substring(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        v3.write_lesson(
            bp,
            title="auth lesson",
            kind="gotcha",
            triggers={"files": [], "task_titles_match": ["auth", "login"]},
        )
        matches = v3.match_lessons_for_task(bp, {"title": "Build login page"})
        assert len(matches) == 1
        assert matches[0][0]["title"] == "auth lesson"

    def test_match_by_file_glob(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        v3.write_lesson(
            bp,
            title="auth lesson",
            kind="gotcha",
            triggers={"files": ["src/auth/**"]},
        )
        matches = v3.match_lessons_for_task(
            bp, {"title": "Unrelated"}, touched_files=["src/auth/session.ts"]
        )
        assert len(matches) == 1

    def test_match_excludes_retired(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        v3.write_lesson(
            bp,
            title="retired one",
            kind="gotcha",
            triggers={"task_titles_match": ["foo"]},
            tier="retired",
        )
        matches = v3.match_lessons_for_task(bp, {"title": "foo bar"})
        assert matches == []

    def test_match_caps_at_three(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        for i in range(5):
            v3.write_lesson(
                bp,
                title=f"l{i}",
                kind="gotcha",
                triggers={"task_titles_match": ["foo"]},
            )
        matches = v3.match_lessons_for_task(bp, {"title": "foo bar"})
        assert len(matches) == 3

    def test_match_sorted_by_reinforce_desc(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        l1, _ = v3.write_lesson(bp, title="weak", kind="gotcha", triggers={"task_titles_match": ["x"]})
        l2, _ = v3.write_lesson(bp, title="strong", kind="gotcha", triggers={"task_titles_match": ["x"]})
        for _ in range(3):
            v3.reinforce_lesson(bp, l2)
        matches = v3.match_lessons_for_task(bp, {"title": "do x"})
        assert matches[0][0]["title"] == "strong"

    def test_digest_excludes_core_and_retired(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        v3.write_lesson(bp, title="active", kind="gotcha")
        v3.write_lesson(bp, title="core", kind="gotcha", tier="core")
        v3.write_lesson(bp, title="retired", kind="gotcha", tier="retired")
        digest = v3.lesson_digest(bp)
        titles = [d["title"] for d in digest]
        assert "active" in titles
        assert "core" not in titles
        assert "retired" not in titles

    def test_core_lessons_returns_full_body(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        v3.write_lesson(bp, title="core1", kind="gotcha", body="## Why\nbecause\n", tier="core")
        cores = v3.core_lessons(bp)
        assert len(cores) == 1
        assert "because" in cores[1][1] if False else "because" in cores[0][1]

    def test_sync_index(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        v3.write_lesson(bp, title="a", kind="pattern")
        v3.write_lesson(bp, title="b", kind="gotcha")
        data: dict = {}
        v3.sync_lesson_index(data, bp)
        assert len(data["lessons_meta"]) == 2
        assert all("id" in e and "title" in e for e in data["lessons_meta"])


class TestAutoState:
    def _bp(self, tmp_path: Path) -> Path:
        return tmp_path / ".taskmaster" / "backlog.yaml"

    def test_init_writes_state(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state = v3.init_auto_run(bp, mode="task", target="T-001", pending_task_ids=["T-001"])
        assert state["mode"] == "task"
        assert state["target"] == "T-001"
        assert state["cursor"]["task_id"] == "T-001"
        assert state["cursor"]["stage"] == "PICK"
        assert state["cursor"]["model"] == "sonnet"
        assert state["pending"] == []
        # Plan 6 layout: file lives under sessions/<sid>.json, not the legacy
        # auto/state.json. auto_state_path() resolves to the active session.
        assert "session_id" in state
        assert v3.auto_state_path(bp).exists()
        assert v3.auto_session_path_bp(bp, state["session_id"]).exists()

    def test_init_invalid_mode(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        with pytest.raises(ValueError):
            v3.init_auto_run(bp, mode="bogus", target="x", pending_task_ids=["T-1"])

    def test_init_empty_tasks_rejected(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        with pytest.raises(ValueError):
            v3.init_auto_run(bp, mode="task", target="x", pending_task_ids=[])

    def test_per_task_model(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state = v3.init_auto_run(
            bp,
            mode="epic",
            target="features",
            pending_task_ids=["T-1", "T-2", "T-3"],
            model_for_task={"T-1": "opus", "T-3": "opus"},
        )
        assert state["cursor"]["model"] == "opus"
        # Advance through T-1, T-2 should default to sonnet, T-3 should be opus
        v3.complete_current_task(state, status="done", summary="ok")
        assert state["cursor"]["task_id"] == "T-2"
        assert state["cursor"]["model"] == "sonnet"
        v3.complete_current_task(state, status="done", summary="ok")
        assert state["cursor"]["task_id"] == "T-3"
        assert state["cursor"]["model"] == "opus"

    def test_advance_stage(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state = v3.init_auto_run(bp, mode="task", target="T-1", pending_task_ids=["T-1"])
        v3.advance_stage(state, "SPEC_REVIEW")
        assert state["cursor"]["stage"] == "SPEC_REVIEW"
        v3.advance_stage(state, "IMPLEMENT")
        assert state["cursor"]["stage"] == "IMPLEMENT"

    def test_advance_invalid_stage(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state = v3.init_auto_run(bp, mode="task", target="T-1", pending_task_ids=["T-1"])
        with pytest.raises(ValueError):
            v3.advance_stage(state, "BOGUS")

    def test_complete_done_advances(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state = v3.init_auto_run(
            bp, mode="epic", target="e", pending_task_ids=["T-1", "T-2"]
        )
        v3.complete_current_task(state, status="done", summary="ok", commits=["abc"])
        assert state["completed"][0]["task_id"] == "T-1"
        assert state["completed"][0]["commits"] == ["abc"]
        assert state["cursor"]["task_id"] == "T-2"
        assert state["cursor"]["stage"] == "PICK"

    def test_complete_done_with_no_pending_clears_cursor(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state = v3.init_auto_run(bp, mode="task", target="T-1", pending_task_ids=["T-1"])
        v3.complete_current_task(state, status="done")
        assert state["cursor"] is None
        assert len(state["completed"]) == 1

    def test_failure_halts_by_default(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state = v3.init_auto_run(
            bp, mode="epic", target="e", pending_task_ids=["T-1", "T-2"]
        )
        v3.complete_current_task(state, status="failed", fail_reason="tests-failed", summary="x")
        assert state["failed"][0]["task_id"] == "T-1"
        # Cursor stays on failed task at HANDOVER_STUB so user can recover
        assert state["cursor"]["task_id"] == "T-1"
        assert state["cursor"]["stage"] == "HANDOVER_STUB"
        assert state["pending"] == ["T-2"]

    def test_failure_continues_with_continue_on_fail(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state = v3.init_auto_run(
            bp,
            mode="epic",
            target="e",
            pending_task_ids=["T-1", "T-2"],
            config={"continue_on_fail": True},
        )
        v3.complete_current_task(state, status="failed", fail_reason="tests-failed")
        assert state["cursor"]["task_id"] == "T-2"

    def test_invalid_fail_reason(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state = v3.init_auto_run(bp, mode="task", target="T-1", pending_task_ids=["T-1"])
        with pytest.raises(ValueError):
            v3.complete_current_task(state, status="failed", fail_reason="weird")

    def test_read_write_roundtrip(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state = v3.init_auto_run(bp, mode="task", target="T-1", pending_task_ids=["T-1"])
        v3.advance_stage(state, "IMPLEMENT")
        v3.write_auto_state(bp, state)
        loaded = v3.read_auto_state(bp)
        assert loaded == state

    def test_read_state_missing(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        assert v3.read_auto_state(bp) is None

    def test_clear_state(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        v3.init_auto_run(bp, mode="task", target="T-1", pending_task_ids=["T-1"])
        assert v3.clear_auto_state(bp) is True
        assert v3.read_auto_state(bp) is None
        # Clearing nothing is a no-op (False)
        assert v3.clear_auto_state(bp) is False

    def test_summary_string(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state = v3.init_auto_run(
            bp, mode="epic", target="features", pending_task_ids=["T-1", "T-2"]
        )
        out = v3.auto_run_summary(state)
        assert "epic" in out
        assert "T-1" in out
        assert "PICK" in out

    # B-046: two runs for the same target in the same second must not collide
    def test_init_same_target_same_minute_unique_sid(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state1 = v3.init_auto_run(bp, mode="task", target="t-1", pending_task_ids=["t-1"])
        state2 = v3.init_auto_run(bp, mode="task", target="t-1", pending_task_ids=["t-1"])
        sid1 = state1["session_id"]
        sid2 = state2["session_id"]
        assert sid1 != sid2, "Two rapid init_auto_run calls must produce distinct session_ids"
        assert v3.auto_session_path_bp(bp, sid1).exists(), f"Session file for {sid1} must exist"
        assert v3.auto_session_path_bp(bp, sid2).exists(), f"Session file for {sid2} must exist"

    # B-047: failed task then re-completed as done must not appear in both lists
    def test_failed_then_done_not_double_recorded(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        state = v3.init_auto_run(
            bp, mode="epic", target="e", pending_task_ids=["t-1", "t-2"]
        )
        # First completion: fail
        v3.complete_current_task(state, status="failed", fail_reason="tests-failed")
        failed_ids = [r["task_id"] for r in state["failed"]]
        assert failed_ids == ["t-1"], f"Expected ['t-1'] in failed, got {failed_ids}"
        # Second completion: done (simulates a fix-and-retry)
        v3.complete_current_task(state, status="done", summary="fixed")
        completed_ids = [r["task_id"] for r in state["completed"]]
        failed_ids_after = [r["task_id"] for r in state["failed"]]
        assert completed_ids == ["t-1"], (
            f"Expected t-1 in completed only, got {completed_ids}"
        )
        assert failed_ids_after == [], (
            f"Expected empty failed list after recovery, got {failed_ids_after}"
        )
        assert state["cursor"]["task_id"] == "t-2", (
            f"Cursor should advance to t-2, got {state['cursor']}"
        )


class TestV3EndToEndRoundtrip:
    """End-to-end roundtrip: heavy fields survive multiple save/load cycles
    interleaved with non-task mutations (handovers, issues, lessons).
    Locks the invariant that v3 preserves all state across normal use.
    """

    def test_full_lifecycle_preserves_state(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"

        # Initial v3 backlog with a task carrying heavy content
        original = {
            "meta": {"schema_version": 3, "project": "p"},
            "context": {},
            "epics": [
                {
                    "id": "e1",
                    "name": "Features",
                    "tasks": [
                        {
                            "id": "T-001",
                            "title": "Login",
                            "status": "in-progress",
                            "priority": "high",
                            "description": "Wire login form",
                            "notes": "cookie scope concern",
                            v3.BODY_KEY: "## Decisions\nStateful sessions chosen.\n",
                        },
                    ],
                }
            ],
            "phases": [],
            "handovers": [],
            "issues": [],
            "lessons_meta": [],
        }
        v3.save_v3(bp, original)

        # Mutate via the layered helpers: add handover, issue, lesson
        v3.write_handover(bp, tldr="day end", task_ids=["T-001"], when="2026-04-26")
        v3.write_issue(bp, title="bug", severity="P1", impact="fixture evidence.", related_tasks=["T-001"])
        v3.write_lesson(bp, title="auth gotcha", kind="gotcha")

        # Sync indexes (what the MCP tools do after each create)
        loaded = v3.load_v3(bp)
        v3.sync_handover_index(loaded, bp)
        v3.sync_issue_index(loaded, bp)
        v3.sync_lesson_index(loaded, bp)
        v3.save_v3(bp, loaded)

        # Read everything back and assert nothing got lost
        roundtripped = v3.load_v3(bp)
        t1 = roundtripped["epics"][0]["tasks"][0]
        assert t1["description"] == "Wire login form"
        assert t1["notes"] == "cookie scope concern"
        assert "Stateful sessions chosen" in t1[v3.BODY_KEY]
        assert t1["status"] == "in-progress"
        assert t1["priority"] == "high"

        assert len(roundtripped["handovers"]) == 1
        assert roundtripped["handovers"][0]["task_ids"] == ["T-001"]
        assert len(roundtripped["issues"]) == 1
        assert roundtripped["issues"][0]["severity"] == "P1"
        assert len(roundtripped["lessons_meta"]) == 1
        assert roundtripped["lessons_meta"][0]["kind"] == "gotcha"

    def test_save_preserves_unrelated_top_level_keys(self, tmp_path: Path):
        # save_v3 must not strip top-level keys it doesn't know about
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        data = {
            "meta": {"schema_version": 3},
            "context": {"active_epic": "auth"},
            "epics": [],
            "phases": [],
            "custom_field": {"foo": "bar"},
            "another": [1, 2, 3],
        }
        v3.save_v3(bp, data)
        loaded = v3.load_v3(bp)
        assert loaded["custom_field"] == {"foo": "bar"}
        assert loaded["another"] == [1, 2, 3]
        assert loaded["context"] == {"active_epic": "auth"}

    def test_v2_backlog_without_v3_indexes_loads_clean(self, tmp_path: Path):
        # A pristine v2 file (no schema_version, no handovers/issues/lessons) should
        # not gain phantom v3 keys when read.
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        bp.parent.mkdir(parents=True)
        import yaml as _y
        bp.write_text(
            _y.dump({
                "meta": {"project": "p"},
                "epics": [{"id": "e1", "tasks": [{"id": "T-1", "title": "x", "status": "todo"}]}],
                "phases": [],
            }),
            encoding="utf-8",
        )
        # Force a v2-path read (the way backlog_server._load() dispatches).
        raw = _y.safe_load(bp.read_text(encoding="utf-8"))
        assert v3.detect_schema_version(raw) == v3.SCHEMA_V2
        assert "handovers" not in raw
        assert "lessons_meta" not in raw

    def test_per_task_file_persists_across_n_saves(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        data = {
            "meta": {"schema_version": 3},
            "epics": [
                {"id": "e1", "tasks": [
                    {"id": "T-1", "title": "x", "description": "initial"},
                ]},
            ],
            "phases": [],
        }
        v3.save_v3(bp, data)
        for i in range(5):
            loaded = v3.load_v3(bp)
            loaded["epics"][0]["tasks"][0]["description"] = f"iteration {i}"
            v3.save_v3(bp, loaded)
        final = v3.load_v3(bp)
        assert final["epics"][0]["tasks"][0]["description"] == "iteration 4"


def test_viewer_prefs_defaults_have_all_expected_keys():
    from taskmaster_v3 import VIEWER_PREFS_DEFAULTS
    expected_top_keys = {
        "schema_version",
        "use_v3",
        "theme",
        "card_density",
        "zoom",
        "screens",
        "dashboard",
        "ui",
        "kanban",
        "lessons",
        "issues",
    }
    assert set(VIEWER_PREFS_DEFAULTS.keys()) == expected_top_keys
    assert VIEWER_PREFS_DEFAULTS["schema_version"] == 1
    assert VIEWER_PREFS_DEFAULTS["theme"] == "dark"
    assert VIEWER_PREFS_DEFAULTS["card_density"] == "full"
    assert VIEWER_PREFS_DEFAULTS["zoom"] == 1.0
    # screens.<name>.view holds A/B toggle per screen
    assert "task_detail" in VIEWER_PREFS_DEFAULTS["screens"]
    assert VIEWER_PREFS_DEFAULTS["screens"]["task_detail"]["view"] == "A"


def test_viewer_prefs_round_trip(tmp_path, monkeypatch):
    from taskmaster_v3 import (
        load_viewer_prefs, save_viewer_prefs, VIEWER_PREFS_DEFAULTS,
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()

    # Empty first read returns defaults (and creates the file)
    p1 = load_viewer_prefs()
    assert p1 == VIEWER_PREFS_DEFAULTS
    assert (tmp_path / ".taskmaster" / "viewer.json").exists()

    # Mutate, save, re-read
    p1["theme"] = "light"
    p1["kanban"]["filters"]["search"] = "auth"
    save_viewer_prefs(p1)

    p2 = load_viewer_prefs()
    assert p2["theme"] == "light"
    assert p2["kanban"]["filters"]["search"] == "auth"

def test_viewer_prefs_unknown_keys_preserved_on_save(tmp_path, monkeypatch):
    """Forward-compat: don't strip keys we don't know about."""
    import json
    from taskmaster_v3 import load_viewer_prefs, save_viewer_prefs
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    (tmp_path / ".taskmaster" / "viewer.json").write_text(
        json.dumps({"schema_version": 1, "future_field": "preserve_me", "theme": "dark"})
    )
    prefs = load_viewer_prefs()
    save_viewer_prefs(prefs)
    saved = json.loads((tmp_path / ".taskmaster" / "viewer.json").read_text())
    assert saved["future_field"] == "preserve_me"


def test_viewer_prefs_set_merges_patch(tmp_path, monkeypatch):
    """viewer_prefs_set accepts a partial patch; unspecified keys retain prior values."""
    import json
    import sys
    from unittest.mock import MagicMock
    from taskmaster_v3 import save_viewer_prefs, load_viewer_prefs, VIEWER_PREFS_DEFAULTS
    from copy import deepcopy
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    save_viewer_prefs(deepcopy(VIEWER_PREFS_DEFAULTS))

    # backlog_server imports fastmcp which has a known mcp version mismatch in this
    # environment (Icon not exported). Mock fastmcp with a passthrough decorator so the
    # actual tool functions remain callable after the module loads.
    if "backlog_server" not in sys.modules:
        def _passthrough_tool():
            def decorator(fn):
                return fn
            return decorator
        fake_fastmcp = MagicMock()
        fake_fastmcp.FastMCP.return_value.tool = _passthrough_tool
        monkeypatch.setitem(sys.modules, "fastmcp", fake_fastmcp)
    from backlog_server import viewer_prefs_set  # type: ignore

    msg = viewer_prefs_set('{"theme": "light", "kanban": {"filters": {"search": "auth"}}}')
    assert "ok" in msg.lower()

    prefs = load_viewer_prefs()
    assert prefs["theme"] == "light"
    assert prefs["kanban"]["filters"]["search"] == "auth"
    # unspecified key retains default
    assert prefs["card_density"] == "full"


def test_auto_storage_constants():
    from taskmaster_v3 import (
        AUTO_DIR, AUTO_SESSIONS_DIR, AUTO_HOOKS_LOG, auto_session_path, auto_events_path,
    )
    from pathlib import Path
    assert AUTO_DIR == Path(".taskmaster") / "auto"
    assert AUTO_SESSIONS_DIR == Path(".taskmaster") / "auto" / "sessions"
    assert AUTO_HOOKS_LOG == Path(".taskmaster") / "auto" / "hooks.jsonl"
    assert auto_session_path("v3-014") == AUTO_SESSIONS_DIR / "v3-014.json"
    assert auto_events_path("v3-014") == AUTO_SESSIONS_DIR / "v3-014.events.jsonl"


def test_auto_session_round_trip(tmp_path, monkeypatch):
    import json
    from taskmaster_v3 import (
        save_auto_session, load_auto_session, list_auto_sessions, AUTO_SESSIONS_DIR,
    )
    monkeypatch.chdir(tmp_path)

    state = {
        "session_id": "v3-014",
        "task_id": "v3-014",
        "title": "Auto-mode status indicator",
        "mode": "walk",
        "started_at": "2026-04-26T18:42:09Z",
        "cursor": {"task_id": "v3-014", "stage": "IMPLEMENT", "model": "sonnet"},
        "completed": ["PICK"],
        "pending": ["REVIEW", "HANDOVER_STUB", "COMPLETE"],
        "failed": [],
        "models": {},
        "config": {},
    }
    save_auto_session("v3-014", state)
    assert (tmp_path / AUTO_SESSIONS_DIR / "v3-014.json").exists()

    got = load_auto_session("v3-014")
    assert got["cursor"]["stage"] == "IMPLEMENT"

    save_auto_session("v3-022", {**state, "session_id": "v3-022", "task_id": "v3-022"})
    sessions = list_auto_sessions()
    assert sorted(s["session_id"] for s in sessions) == ["v3-014", "v3-022"]


def test_load_auto_session_missing_returns_none(tmp_path, monkeypatch):
    from taskmaster_v3 import load_auto_session
    monkeypatch.chdir(tmp_path)
    assert load_auto_session("nope") is None


def test_migrate_legacy_state_to_sessions(tmp_path, monkeypatch):
    import json
    from taskmaster_v3 import (
        migrate_auto_state_to_sessions, AUTO_DIR, AUTO_LEGACY_STATE,
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / AUTO_DIR).mkdir(parents=True)
    legacy = {
        "task_id": "v3-014",
        "mode": "walk",
        "started_at": "2026-04-26T18:42:09Z",
        "cursor": {"task_id": "v3-014", "stage": "IMPLEMENT"},
    }
    (tmp_path / AUTO_LEGACY_STATE).write_text(json.dumps(legacy))

    moved = migrate_auto_state_to_sessions()
    assert moved is True
    sess = json.loads((tmp_path / AUTO_DIR / "sessions" / "v3-014.json").read_text())
    assert sess["session_id"] == "v3-014"
    assert sess["task_id"] == "v3-014"
    # legacy file renamed, not deleted, so we keep an audit trail
    assert (tmp_path / AUTO_DIR / "state.legacy.json").exists()
    assert not (tmp_path / AUTO_LEGACY_STATE).exists()


def test_migrate_idempotent(tmp_path, monkeypatch):
    from taskmaster_v3 import migrate_auto_state_to_sessions
    monkeypatch.chdir(tmp_path)
    # No legacy file → no-op, returns False
    assert migrate_auto_state_to_sessions() is False


def test_server_init_runs_auto_migration(tmp_path, monkeypatch):
    import json
    from taskmaster_v3 import AUTO_DIR, AUTO_LEGACY_STATE
    monkeypatch.chdir(tmp_path)
    (tmp_path / AUTO_DIR).mkdir(parents=True)
    (tmp_path / AUTO_LEGACY_STATE).write_text(json.dumps({"task_id": "v3-014"}))

    from backlog_server import _init_storage  # added in this task
    _init_storage()

    assert (tmp_path / AUTO_DIR / "sessions" / "v3-014.json").exists()
    assert not (tmp_path / AUTO_LEGACY_STATE).exists()


def test_auto_events_append_and_read(tmp_path, monkeypatch):
    from taskmaster_v3 import append_auto_event, read_auto_events
    monkeypatch.chdir(tmp_path)
    append_auto_event("v3-014", {
        "ts": "2026-04-26T18:42:09Z", "stage": "PICK",
        "kind": "stage_enter", "msg": "picked v3-014",
    })
    append_auto_event("v3-014", {
        "ts": "2026-04-26T18:43:11Z", "stage": "PICK",
        "kind": "stage_exit", "msg": "PICK done",
    })
    append_auto_event("v3-014", {
        "ts": "2026-04-26T18:43:12Z", "stage": "IMPLEMENT",
        "kind": "stage_enter", "msg": "starting implementation",
    })
    all_events = read_auto_events("v3-014")
    assert len(all_events) == 3
    assert all_events[0]["kind"] == "stage_enter"

    since = read_auto_events("v3-014", since="2026-04-26T18:43:00Z")
    assert len(since) == 2
    assert since[0]["stage"] == "PICK"  # exit
    assert since[1]["stage"] == "IMPLEMENT"


def test_read_auto_events_missing_session_returns_empty(tmp_path, monkeypatch):
    from taskmaster_v3 import read_auto_events
    monkeypatch.chdir(tmp_path)
    assert read_auto_events("nope") == []


def test_read_hook_events_counts_by_hook(tmp_path, monkeypatch):
    from taskmaster_v3 import read_hook_events, AUTO_HOOKS_LOG
    monkeypatch.chdir(tmp_path)
    AUTO_HOOKS_LOG.parent.mkdir(parents=True, exist_ok=True)
    AUTO_HOOKS_LOG.write_text(
        '{"ts":"2026-04-26T18:00:00Z","hook":"PostToolUse","session_id":"v3-014","tool":"Edit","ok":true}\n'
        '{"ts":"2026-04-26T18:00:01Z","hook":"PostToolUse","session_id":"v3-014","tool":"Edit","ok":true}\n'
        '{"ts":"2026-04-26T18:00:02Z","hook":"PreCompact","session_id":"v3-014","ok":true}\n'
    )
    counts = read_hook_events("v3-014")
    assert counts == {"PostToolUse": 2, "PreCompact": 1}


def test_read_hook_events_missing_log_returns_empty(tmp_path, monkeypatch):
    from taskmaster_v3 import read_hook_events
    monkeypatch.chdir(tmp_path)
    assert read_hook_events("v3-014") == {}


def test_load_viewer_prefs_corrupt_file_resets_to_defaults(tmp_path, monkeypatch):
    """A corrupt viewer.json must never take the viewer down — it is
    quarantined to viewer.json.corrupt and replaced with defaults."""
    import taskmaster_v3 as v3
    p = tmp_path / "viewer.json"
    p.write_text('{"theme": "dark"}   }\n  }\n}', encoding="utf-8")
    monkeypatch.setattr(v3, "viewer_prefs_path", lambda: p)
    prefs = v3.load_viewer_prefs()
    assert prefs["schema_version"] == v3.VIEWER_PREFS_DEFAULTS["schema_version"]
    assert (tmp_path / "viewer.json.corrupt").exists()
    # The rewritten file parses cleanly on the next load.
    assert v3.load_viewer_prefs()["theme"] == v3.VIEWER_PREFS_DEFAULTS["theme"]
