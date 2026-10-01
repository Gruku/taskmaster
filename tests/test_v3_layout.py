"""Tests for v3 layout plumbing: schema_version detection, atomic writes."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from taskmaster import taskmaster_v3 as v3  # noqa: E402
import tests.entity_helpers as entity_helpers  # noqa: E402


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

    def test_default_is_v4(self):
        # New backlogs use sharded, merge-aware storage by default.
        assert v3.SCHEMA_DEFAULT == v3.SCHEMA_V4


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

    def _write_v3_projection(self, tmp_path: Path) -> Path:
        """Lay out the v3 files `load_v3` reads: slim index plus body files.

        `save_v3` used to produce this shape. It is gone — the store owns every
        write under `.taskmaster/` — so the fixture writes the same layout with
        the pure primitives that remain, and `load_v3` stays under test.
        """
        import yaml as _y

        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        bp.parent.mkdir(parents=True, exist_ok=True)
        data = self._v3_backlog()
        slim = {**data, "epics": []}
        for epic in data["epics"]:
            slim_tasks = []
            for task in epic["tasks"]:
                lean, heavy, body = v3._split_task_for_v3(task)
                if any(field in heavy for field in v3.HEAVY_FIELDS) or body:
                    v3.write_task_file(v3.task_file_path(bp, task["id"]), heavy, body)
                slim_tasks.append(lean)
            slim["epics"].append({**{k: v for k, v in epic.items() if k != "tasks"},
                                  "tasks": slim_tasks})
        bp.write_text(_y.dump(slim), encoding="utf-8")
        return bp

    def test_v3_projection_splits_heavy_fields_out_of_the_index(self, tmp_path: Path):
        bp = self._write_v3_projection(tmp_path)

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
        bp = self._write_v3_projection(tmp_path)
        loaded = v3.load_v3(bp)

        t1 = loaded["epics"][0]["tasks"][0]
        assert t1["description"] == "Wire up the login form."
        assert t1["notes"] == "Watch for cookie scope."
        assert t1[v3.BODY_KEY] == "## Decisions\nWent with cookie auth.\n"

        t2 = loaded["epics"][0]["tasks"][1]
        assert "description" not in t2
        assert v3.BODY_KEY not in t2

    def test_roundtrip_preserves_data(self, tmp_path: Path):
        bp = self._write_v3_projection(tmp_path)
        original = self._v3_backlog()
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


# `taskmaster_v3.migrate_v2_to_v3` is gone: the store adopts whatever schema it
# finds on first open (design spec decision 9), so there is no separate file
# rewrite to test. What that class covered — a v2 backlog gaining a schema
# marker, heavy fields moving into per-entity files, losslessness and
# idempotence — is covered against the real path in
# tests/test_migrate_tools_through_store.py and
# tests/test_epic_phase_bodies.py::test_adoption_writes_epic_phase_and_task_files_from_a_v2_backlog.


class TestHandoverHelpers:
    def test_handover_kind_to_viewer_kind_maps_all_kinds(self):
        # Canonical storage kinds map to viewer kinds.
        assert v3.HANDOVER_KIND_TO_VIEWER_KIND["continuity"]    == "wrap"
        assert v3.HANDOVER_KIND_TO_VIEWER_KIND["deep-context"]  == "mid-task"
        assert v3.HANDOVER_KIND_TO_VIEWER_KIND["milestone"]     == "checkpoint"
        assert v3.HANDOVER_KIND_TO_VIEWER_KIND["auto-stage"]    == "standalone"
        assert v3.HANDOVER_KIND_TO_VIEWER_KIND["task-complete"] == "wrap"  # Plan B addition
        # Mapping covers every storage kind:
        assert set(v3.HANDOVER_KIND_TO_VIEWER_KIND.keys()) == set(v3.HANDOVER_KINDS)
        # All viewer kinds are valid:
        assert set(v3.HANDOVER_KIND_TO_VIEWER_KIND.values()) <= {
            "mid-task", "checkpoint", "wrap", "standalone"
        }

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
        hid, target = entity_helpers.write_handover(
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
            entity_helpers.write_handover(bp, tldr="")

    def test_id_collision_gets_suffix(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        hid1, _ = entity_helpers.write_handover(bp, tldr="Same", when="2026-04-26", body="first")
        hid2, _ = entity_helpers.write_handover(bp, tldr="Same", when="2026-04-26", body="second")
        assert hid1 != hid2
        assert hid1 == "2026-04-26-same"
        assert hid2 == "2026-04-26-same-2"

    def test_context_size_optional_field(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        hid, _ = entity_helpers.write_handover(bp, tldr="Big session", when="2026-04-26", context_size_at_write="320k")
        fm, _ = v3.read_handover(bp, hid)
        assert fm["context_size_at_write"] == "320k"

    def test_omit_context_size_when_unset(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        hid, _ = entity_helpers.write_handover(bp, tldr="Small", when="2026-04-26")
        fm, _ = v3.read_handover(bp, hid)
        assert "context_size_at_write" not in fm


class TestListHandovers:
    def test_empty_when_no_dir(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        assert v3.list_handover_ids(bp) == []
        assert v3.latest_handover_id(bp) is None

    def test_sorted_newest_first(self, tmp_path: Path):
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        entity_helpers.write_handover(bp, tldr="A", when="2026-04-25")
        entity_helpers.write_handover(bp, tldr="B", when="2026-04-26")
        entity_helpers.write_handover(bp, tldr="C", when="2026-04-24")
        ids = v3.list_handover_ids(bp)
        assert ids[0].startswith("2026-04-26")
        assert ids[-1].startswith("2026-04-24")
        assert v3.latest_handover_id(bp) == ids[0]


class TestHandoverIndex:
    def _bp(self, tmp_path: Path) -> Path:
        return tmp_path / ".taskmaster" / "backlog.yaml"

    def test_sync_populates_index(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        entity_helpers.write_handover(bp, tldr="First", when="2026-04-25", task_ids=["T-1"])
        entity_helpers.write_handover(bp, tldr="Second", when="2026-04-26", task_ids=["T-2"])
        data: dict = {}
        entity_helpers.sync_handover_index(data, bp)
        assert len(data["handovers"]) == 2
        assert data["handovers"][0]["id"].startswith("2026-04-26")
        assert data["handovers"][0]["task_ids"] == ["T-2"]

    def test_sync_archives_overflow(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        # Write 5 handovers, cap at 3 → 2 archived.
        for i in range(5):
            entity_helpers.write_handover(bp, tldr=f"h{i}", when=f"2026-04-{20 + i}")
        data: dict = {}
        entity_helpers.sync_handover_index(data, bp, cap=3)
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
        entity_helpers.write_handover(
            bp,
            tldr="Day end",
            next_action="Resume",
            task_ids=["T-1"],
            session_kind="end-of-day",
            when="2026-04-26",
        )
        data: dict = {}
        entity_helpers.sync_handover_index(data, bp)
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
        entity_helpers.write_handover(bp, tldr="x", when="2025-12-31")
        entity_helpers.write_handover(bp, tldr="y", when="2026-01-01")
        entity_helpers.write_handover(bp, tldr="z", when="2026-04-26")
        data: dict = {}
        entity_helpers.sync_handover_index(data, bp, cap=1)
        # 2 archived, split across years
        assert (bp.parent / "handovers" / "_archive" / "2025").exists()
        assert (bp.parent / "handovers" / "_archive" / "2026").exists()


class TestIssues:
    def _bp(self, tmp_path: Path) -> Path:
        return tmp_path / ".taskmaster" / "backlog.yaml"

    def test_next_id_allocates_sequentially(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        first, _ = entity_helpers.write_issue(
            bp, title="A", severity="P1", impact="fixture evidence."
        )
        second, _ = entity_helpers.write_issue(
            bp, title="B", severity="P0", impact="fixture evidence."
        )
        assert [first, second] == ["ISS-001", "ISS-002"]

    def test_create_and_read_roundtrip(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        iid, target = entity_helpers.write_issue(
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
            entity_helpers.write_issue(bp, title="x", severity="urgent")

    def test_invalid_status_rejected(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        with pytest.raises(ValueError):
            entity_helpers.write_issue(bp, title="x", severity="P1", status="bogus")

    def test_fixed_requires_fixed_in_task(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        iid, _ = entity_helpers.write_issue(bp, title="x", severity="P1", impact="fixture evidence.")
        with pytest.raises(ValueError):
            entity_helpers.update_issue(bp, iid, status="fixed")

    def test_fixed_with_task_sets_resolved(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        iid, _ = entity_helpers.write_issue(bp, title="x", severity="P1", impact="fixture evidence.")
        fm, _ = entity_helpers.update_issue(bp, iid, status="fixed", fixed_in_task="features-007")
        assert fm["status"] == "fixed"
        assert fm["resolved"]  # ISO date populated

    def test_duplicate_requires_target(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        iid, _ = entity_helpers.write_issue(bp, title="x", severity="P1", impact="fixture evidence.")
        with pytest.raises(ValueError):
            entity_helpers.update_issue(bp, iid, status="duplicate")
        entity_helpers.update_issue(bp, iid, status="duplicate", duplicate_of="ISS-002")  # ok

    def test_index_sorted_by_severity(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        entity_helpers.write_issue(bp, title="low", severity="P3", impact="fixture evidence.")
        entity_helpers.write_issue(bp, title="critical", severity="P0", impact="fixture evidence.")
        entity_helpers.write_issue(bp, title="high", severity="P1", impact="fixture evidence.")
        data: dict = {}
        entity_helpers.sync_issue_index(data, bp)
        sevs = [e["severity"] for e in data["issues"]]
        assert sevs == ["P0", "P1", "P3"]

    def test_index_entry_is_slim(self, tmp_path: Path):
        bp = self._bp(tmp_path)
        entity_helpers.write_issue(
            bp,
            title="x",
            severity="P2",
            impact="long impact text" * 100,
            body="long body" * 1000,
        )
        data: dict = {}
        entity_helpers.sync_issue_index(data, bp)
        entry = data["issues"][0]
        # impact, body etc not in index
        assert set(entry.keys()) <= {
            "id", "title", "status", "severity", "components", "related_tasks"
        }


class TestV3EndToEndRoundtrip:
    """End-to-end roundtrip: heavy fields survive multiple save/load cycles
    interleaved with non-task mutations (handovers, issues).
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
        }
        # Seed the v3 projection; the store adopts it on the first open below.
        import yaml as _y

        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(_y.dump(original), encoding="utf-8")

        # Mutate via the layered helpers: add handover, issue
        entity_helpers.write_handover(bp, tldr="day end", task_ids=["T-001"], when="2026-04-26")
        entity_helpers.write_issue(bp, title="bug", severity="P1", impact="fixture evidence.", related_tasks=["T-001"])

        # Sync indexes (what the MCP tools do after each create). The store
        # adopted the v3 projection on the first write above, so the read-back
        # comes from it rather than from a second parse of backlog.yaml.
        from taskmaster import store as _store

        roundtripped = _store.open_store(bp).load_dict()
        entity_helpers.sync_handover_index(roundtripped, bp)
        entity_helpers.sync_issue_index(roundtripped, bp)
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

    def test_adoption_preserves_unrelated_top_level_keys(self, tmp_path: Path):
        # The store's export must not strip top-level keys it doesn't know
        # about; `context` is the one deliberate exception (runtime-derived, so
        # it is dropped from the projection by design).
        import yaml as _y
        from taskmaster import store as _store

        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        bp.parent.mkdir(parents=True, exist_ok=True)
        (bp.parent / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
        data = {
            "meta": {"schema_version": 3},
            "context": {"active_epic": "auth"},
            "epics": [],
            "phases": [],
            "custom_field": {"foo": "bar"},
            "another": [1, 2, 3],
        }
        bp.write_text(_y.dump(data), encoding="utf-8")
        _store.reset_for_tests()
        _store.open_store(backlog_path=bp, session="top-level-keys-test")
        projected = _y.safe_load(bp.read_text(encoding="utf-8"))
        assert projected["custom_field"] == {"foo": "bar"}
        assert projected["another"] == [1, 2, 3]
        assert "context" not in projected

    def test_v2_backlog_without_v3_indexes_loads_clean(self, tmp_path: Path):
        # A pristine v2 file (no schema_version, no handovers/issues) should
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

    def test_per_task_file_persists_across_n_writes(self, tmp_path: Path):
        import yaml as _y
        from taskmaster import store as _store

        bp = tmp_path / ".taskmaster" / "backlog.yaml"
        bp.parent.mkdir(parents=True, exist_ok=True)
        (bp.parent / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
        data = {
            "meta": {"schema_version": 3},
            "epics": [
                {"id": "e1", "tasks": [
                    {"id": "T-1", "title": "x", "description": "initial"},
                ]},
            ],
            "phases": [],
        }
        bp.write_text(_y.dump(data), encoding="utf-8")
        _store.reset_for_tests()
        opened = _store.open_store(backlog_path=bp, session="n-writes-test")
        for i in range(5):
            with _store.transaction(backlog_path=bp, tool="n-writes") as tx:
                doc = tx.get("task", "T-1")
                doc["description"] = f"iteration {i}"
                tx.put("task", "T-1", doc)
        final = opened.load_dict()
        assert final["epics"][0]["tasks"][0]["description"] == "iteration 4"
        assert "iteration 4" in v3.task_file_path(bp, "T-1").read_text(encoding="utf-8")


def test_viewer_prefs_defaults_have_all_expected_keys():
    from taskmaster.taskmaster_v3 import VIEWER_PREFS_DEFAULTS
    expected_top_keys = {
        "schema_version",
        "theme",
        "card_density",
        "zoom",
        "screens",
        "dashboard",
        "ui",
        "kanban",
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


# Viewer prefs take the resolved backlog path now instead of re-deriving a root
# from the current working directory. The CWD flavour diverged from the writer on
# `.claude/` and root-layout projects (ISS-004), so every reader in the package
# takes the path its caller already resolved.
def _prefs_bp(tmp_path):
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True, exist_ok=True)
    return bp


def test_viewer_prefs_round_trip(tmp_path, monkeypatch):
    from taskmaster.taskmaster_v3 import (
        load_viewer_prefs, save_viewer_prefs, VIEWER_PREFS_DEFAULTS,
    )
    bp = _prefs_bp(tmp_path)

    # Empty first read returns defaults (and creates the file)
    p1 = load_viewer_prefs(bp)
    assert p1 == VIEWER_PREFS_DEFAULTS
    assert (tmp_path / ".taskmaster" / "viewer.json").exists()

    # Mutate, save, re-read
    p1["theme"] = "light"
    p1["kanban"]["filters"]["search"] = "auth"
    save_viewer_prefs(bp, p1)

    p2 = load_viewer_prefs(bp)
    assert p2["theme"] == "light"
    assert p2["kanban"]["filters"]["search"] == "auth"

def test_viewer_prefs_tolerates_stale_use_v3(tmp_path, monkeypatch):
    """The retired use_v3 pref, if left on disk from an older version, must load
    without error and never appear in the defaults."""
    import json
    from taskmaster.taskmaster_v3 import load_viewer_prefs, VIEWER_PREFS_DEFAULTS
    bp = _prefs_bp(tmp_path)
    (tmp_path / ".taskmaster" / "viewer.json").write_text(
        json.dumps({"schema_version": 1, "use_v3": True, "theme": "dark"})
    )
    assert "use_v3" not in VIEWER_PREFS_DEFAULTS
    prefs = load_viewer_prefs(bp)  # must not raise
    assert prefs["theme"] == "dark"
    assert prefs["card_density"] == "full"  # defaults still fill in


def test_viewer_prefs_unknown_keys_preserved_on_save(tmp_path, monkeypatch):
    """Forward-compat: don't strip keys we don't know about."""
    import json
    from taskmaster.taskmaster_v3 import load_viewer_prefs, save_viewer_prefs
    bp = _prefs_bp(tmp_path)
    (tmp_path / ".taskmaster" / "viewer.json").write_text(
        json.dumps({"schema_version": 1, "future_field": "preserve_me", "theme": "dark"})
    )
    prefs = load_viewer_prefs(bp)
    save_viewer_prefs(bp, prefs)
    saved = json.loads((tmp_path / ".taskmaster" / "viewer.json").read_text())
    assert saved["future_field"] == "preserve_me"


def test_viewer_prefs_set_merges_patch(tmp_path, monkeypatch):
    """viewer_prefs_set accepts a partial patch; unspecified keys retain prior values."""
    import json
    import sys
    from unittest.mock import MagicMock
    from taskmaster.taskmaster_v3 import save_viewer_prefs, load_viewer_prefs, VIEWER_PREFS_DEFAULTS
    from copy import deepcopy
    monkeypatch.chdir(tmp_path)
    bp = _prefs_bp(tmp_path)
    bp.write_text("meta:\n  project: t\nepics: []\nphases: []\n", encoding="utf-8")
    save_viewer_prefs(bp, deepcopy(VIEWER_PREFS_DEFAULTS))

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
    from taskmaster import backlog_server as _bs  # type: ignore
    from taskmaster.backlog_server import viewer_prefs_set  # type: ignore

    # The prefs tools resolve from ROOT like every other path now.
    monkeypatch.setattr(_bs, "ROOT", tmp_path)
    monkeypatch.setattr(_bs, "CONFIG_PATH", tmp_path / ".taskmaster" / "taskmaster.json")
    monkeypatch.setattr(_bs, "LEGACY_CONFIG_PATH", tmp_path / ".claude" / "taskmaster.json")

    msg = viewer_prefs_set('{"theme": "light", "kanban": {"filters": {"search": "auth"}}}')
    assert "ok" in msg.lower()

    prefs = load_viewer_prefs(bp)
    assert prefs["theme"] == "light"
    assert prefs["kanban"]["filters"]["search"] == "auth"
    # unspecified key retains default
    assert prefs["card_density"] == "full"


def test_load_viewer_prefs_corrupt_file_resets_to_defaults(tmp_path, monkeypatch):
    """A corrupt viewer.json must never take the viewer down — it is
    quarantined to viewer.json.corrupt and replaced with defaults."""
    from taskmaster import taskmaster_v3 as v3
    p = tmp_path / "viewer.json"
    p.write_text('{"theme": "dark"}   }\n  }\n}', encoding="utf-8")
    monkeypatch.setattr(v3, "viewer_prefs_path", lambda _bp: p)
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    prefs = v3.load_viewer_prefs(bp)
    assert prefs["schema_version"] == v3.VIEWER_PREFS_DEFAULTS["schema_version"]
    assert (tmp_path / "viewer.json.corrupt").exists()
    # The rewritten file parses cleanly on the next load.
    assert v3.load_viewer_prefs(bp)["theme"] == v3.VIEWER_PREFS_DEFAULTS["theme"]
