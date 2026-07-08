"""Tests for read/write_entity_anywhere dispatchers and sync_inverse engine.

Uses write_entity_anywhere directly to seed test entities (bypassing the
allocator-based write_handover/write_issue/write_lesson/write_idea which
generate IDs server-side).
"""
from pathlib import Path
import pytest
import yaml

from taskmaster.taskmaster_v3 import (
    read_entity_anywhere,
    write_entity_anywhere,
    sync_inverse,
    entity_links,
    BODY_KEY,
)


@pytest.fixture
def tm_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".taskmaster"
    d.mkdir()
    backlog = d / "backlog.yaml"
    backlog.write_text(yaml.safe_dump({
        "meta": {"schema_version": 3},
        "epics": [{"id": "e1", "title": "E", "tasks": [
            {"id": "T-001", "title": "First", "status": "todo"},
        ]}],
    }), encoding="utf-8")
    (d / "handovers").mkdir()
    (d / "issues").mkdir()
    (d / "lessons").mkdir()
    (d / "ideas").mkdir()
    (d / "tasks").mkdir()
    return d


def _seed_handover(tm_dir: Path, hid: str = "HND-001") -> None:
    entity = {
        "id": hid,
        "tldr": "test",
        "status": "open",
        "task_ids": ["T-001"],
        BODY_KEY: "body content",
    }
    write_entity_anywhere(tm_dir / "backlog.yaml", entity)


def _seed_issue(tm_dir: Path, iid: str = "ISS-001") -> None:
    entity = {
        "id": iid,
        "title": "Bug",
        "tldr": "test",
        "severity": "P2",
        "status": "open",
        BODY_KEY: "body",
    }
    write_entity_anywhere(tm_dir / "backlog.yaml", entity)


def _seed_lesson(tm_dir: Path, lid: str = "L-001") -> None:
    entity = {
        "id": lid,
        "title": "Lesson",
        "tldr": "test",
        "kind": "pattern",
        "tier": "active",
        BODY_KEY: "body",
    }
    write_entity_anywhere(tm_dir / "backlog.yaml", entity)


def _seed_task(tm_dir: Path, tid: str) -> None:
    """Append a task to backlog.yaml directly."""
    data = yaml.safe_load((tm_dir / "backlog.yaml").read_text(encoding="utf-8"))
    data["epics"][0]["tasks"].append({"id": tid, "title": tid, "status": "todo"})
    (tm_dir / "backlog.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_read_entity_anywhere_task(tm_dir):
    entity = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    assert entity["id"] == "T-001"
    assert entity["title"] == "First"


def test_read_entity_anywhere_handover(tm_dir):
    _seed_handover(tm_dir, "HND-001")
    entity = read_entity_anywhere(tm_dir / "backlog.yaml", "HND-001")
    assert entity["id"] == "HND-001"


def test_read_entity_anywhere_issue(tm_dir):
    _seed_issue(tm_dir, "ISS-001")
    entity = read_entity_anywhere(tm_dir / "backlog.yaml", "ISS-001")
    assert entity["id"] == "ISS-001"


def test_read_entity_anywhere_unknown_returns_none(tm_dir):
    assert read_entity_anywhere(tm_dir / "backlog.yaml", "ZZZ-999") is None


def test_write_entity_anywhere_task_roundtrip(tm_dir):
    entity = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    entity["links"] = [{"type": "depends_on", "target": "T-002"}]
    write_entity_anywhere(tm_dir / "backlog.yaml", entity)
    again = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    assert again["links"] == [{"type": "depends_on", "target": "T-002"}]


def test_sync_inverse_writes_inverse_on_target(tm_dir):
    _seed_task(tm_dir, "T-002")
    sync_inverse(tm_dir / "backlog.yaml", source="T-001", target="T-002", type="depends_on")
    t2 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-002")
    assert {"type": "blocks", "target": "T-001"} in entity_links(t2)


def test_sync_inverse_idempotent(tm_dir):
    _seed_task(tm_dir, "T-002")
    sync_inverse(tm_dir / "backlog.yaml", "T-001", "T-002", "depends_on")
    sync_inverse(tm_dir / "backlog.yaml", "T-001", "T-002", "depends_on")
    t2 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-002")
    assert entity_links(t2).count({"type": "blocks", "target": "T-001"}) == 1


def test_sync_inverse_relates_to_is_symmetric(tm_dir):
    _seed_issue(tm_dir, "ISS-001")
    sync_inverse(tm_dir / "backlog.yaml", "T-001", "ISS-001", "relates_to")
    iss = read_entity_anywhere(tm_dir / "backlog.yaml", "ISS-001")
    assert {"type": "relates_to", "target": "T-001"} in entity_links(iss)


def test_sync_inverse_remove_drops_inverse(tm_dir):
    _seed_issue(tm_dir, "ISS-001")
    sync_inverse(tm_dir / "backlog.yaml", "T-001", "ISS-001", "fixes")
    iss = read_entity_anywhere(tm_dir / "backlog.yaml", "ISS-001")
    assert {"type": "fixed_in_task", "target": "T-001"} in entity_links(iss)

    sync_inverse(tm_dir / "backlog.yaml", "T-001", "ISS-001", "fixes", remove=True)
    iss = read_entity_anywhere(tm_dir / "backlog.yaml", "ISS-001")
    assert {"type": "fixed_in_task", "target": "T-001"} not in entity_links(iss)


def test_sync_inverse_missing_target_raises(tm_dir):
    with pytest.raises(KeyError):
        sync_inverse(tm_dir / "backlog.yaml", "T-001", "T-999", "depends_on")
