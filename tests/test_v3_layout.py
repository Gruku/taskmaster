"""Tests for v3 layout plumbing: schema_version detection, atomic writes."""
from __future__ import annotations

import sys
from pathlib import Path

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
