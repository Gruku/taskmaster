"""Tests for v3 layout plumbing: schema_version detection, atomic writes."""
from __future__ import annotations

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
