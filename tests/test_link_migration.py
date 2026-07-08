"""Plan C migration tests — legacy field translation, script, and read fallback."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import yaml

import pytest

from taskmaster.taskmaster_v3 import (
    legacy_links_to_typed,
    read_entity_anywhere,
    entity_links,
    write_entity_anywhere,
    BODY_KEY,
)


# ── Pure unit tests for the translator ─────────────────────────────────


def test_task_legacy_fields_translate():
    task = {
        "id": "T-001",
        "depends_on": ["T-002", "T-003"],
        "related_issues": ["ISS-007"],
        "related_lessons": ["L-003"],
    }
    links = legacy_links_to_typed(task, kind="task")
    assert {"type": "depends_on",  "target": "T-002"} in links
    assert {"type": "depends_on",  "target": "T-003"} in links
    assert {"type": "relates_to",  "target": "ISS-007"} in links
    assert {"type": "informed_by", "target": "L-003"}  in links


def test_issue_legacy_fields_translate():
    issue = {
        "id": "ISS-001",
        "related_tasks": ["T-001"],
        "fixed_in_task": "T-005",
        "duplicate_of": "ISS-002",
    }
    links = legacy_links_to_typed(issue, kind="issue")
    assert {"type": "relates_to",    "target": "T-001"}   in links
    assert {"type": "fixed_in_task", "target": "T-005"}   in links
    assert {"type": "duplicate_of",  "target": "ISS-002"} in links


def test_lesson_legacy_fields_translate():
    lesson = {
        "id": "L-001",
        "related_tasks": ["T-001"],
        "related_issues": ["ISS-007"],
    }
    links = legacy_links_to_typed(lesson, kind="lesson")
    assert {"type": "informs",    "target": "T-001"}   in links
    assert {"type": "relates_to", "target": "ISS-007"} in links


def test_handover_legacy_fields_translate():
    handover = {
        "id": "HND-002",
        "supersedes": ["HND-001"],
        "superseded_by": ["HND-003"],
    }
    links = legacy_links_to_typed(handover, kind="handover")
    assert {"type": "supersedes",    "target": "HND-001"} in links
    assert {"type": "superseded_by", "target": "HND-003"} in links


def test_existing_links_are_preserved():
    task = {
        "id": "T-001",
        "depends_on": ["T-002"],
        "links": [{"type": "fixes", "target": "ISS-007"}],
    }
    links = legacy_links_to_typed(task, kind="task")
    assert {"type": "fixes",      "target": "ISS-007"} in links
    assert {"type": "depends_on", "target": "T-002"}   in links


def test_dedupes_when_legacy_and_links_overlap():
    task = {
        "id": "T-001",
        "depends_on": ["T-002"],
        "links": [{"type": "depends_on", "target": "T-002"}],
    }
    links = legacy_links_to_typed(task, kind="task")
    assert links.count({"type": "depends_on", "target": "T-002"}) == 1


# ── Migration script tests ─────────────────────────────────────────────


def _seed_project(tmp_path: Path) -> Path:
    """Build a .taskmaster/ project with legacy linkage fields only."""
    d = tmp_path / ".taskmaster"
    d.mkdir()
    (d / "backlog.yaml").write_text(yaml.safe_dump({
        "meta": {"schema_version": 3},
        "epics": [{"id": "e1", "title": "E", "tasks": [
            {"id": "T-001", "title": "First", "status": "todo",
             "depends_on": ["T-002"], "related_issues": ["ISS-001"]},
            {"id": "T-002", "title": "Second", "status": "todo"},
        ]}],
    }))
    for sub in ("handovers", "issues", "lessons", "ideas", "tasks"):
        (d / sub).mkdir()
    # Seed an issue with a legacy fixed_in_task field via write_entity_anywhere
    # (bypasses the issue-creator allocator since that builds a fresh frontmatter).
    iss = {
        "id": "ISS-001", "title": "Bug", "tldr": "x", "severity": "P2",
        "status": "open", "fixed_in_task": "T-005",
        BODY_KEY: "body",
    }
    write_entity_anywhere(d / "backlog.yaml", iss)
    return d


def test_migrate_links_script_translates_legacy_fields(tmp_path):
    tm_dir = _seed_project(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "scripts.migrate_links",
         "--root", str(tm_dir.parent)],
        capture_output=True, text=True, check=False,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, result.stderr

    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    assert {"type": "depends_on", "target": "T-002"} in entity_links(t1)
    assert {"type": "relates_to", "target": "ISS-001"} in entity_links(t1)

    iss = read_entity_anywhere(tm_dir / "backlog.yaml", "ISS-001")
    assert {"type": "fixed_in_task", "target": "T-005"} in entity_links(iss)


def test_migrate_links_is_idempotent(tmp_path):
    tm_dir = _seed_project(tmp_path)
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.migrate_links",
             "--root", str(tm_dir.parent)],
            capture_output=True, text=True, check=False,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert result.returncode == 0

    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    # Only one of each link type/target after re-runs.
    assert entity_links(t1).count({"type": "depends_on", "target": "T-002"}) == 1


def test_migrate_links_adds_inverses(tmp_path):
    tm_dir = _seed_project(tmp_path)
    subprocess.run(
        [sys.executable, "-m", "scripts.migrate_links",
         "--root", str(tm_dir.parent)],
        check=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    t2 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-002")
    assert {"type": "blocks", "target": "T-001"} in entity_links(t2)


# ── Read-fallback shim test ─────────────────────────────────────────────


def test_read_fallback_synthesizes_links_when_absent(tmp_path):
    # Project that has only legacy fields, no `links` array yet.
    d = tmp_path / ".taskmaster"
    d.mkdir()
    (d / "backlog.yaml").write_text(yaml.safe_dump({
        "meta": {"schema_version": 3},
        "epics": [{"id": "e1", "title": "E", "tasks": [
            {"id": "T-001", "title": "First", "status": "todo",
             "depends_on": ["T-002"]},
            {"id": "T-002", "title": "Second", "status": "todo"},
        ]}],
    }))
    for sub in ("handovers", "issues", "lessons", "ideas", "tasks"):
        (d / sub).mkdir()

    t1 = read_entity_anywhere(d / "backlog.yaml", "T-001")
    # The link should be visible via the new accessor even without migration.
    assert {"type": "depends_on", "target": "T-002"} in entity_links(t1)
