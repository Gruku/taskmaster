from pathlib import Path
import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster.taskmaster_v3 import read_entity_anywhere, entity_links


@pytest.fixture
def tm_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / ".taskmaster"
    d.mkdir()
    (d / "backlog.yaml").write_text(yaml.safe_dump({
        "meta": {"schema_version": 3},
        "epics": [{"id": "e1", "title": "E", "tasks": [
            {"id": "T-001", "title": "First", "status": "todo"},
            {"id": "T-002", "title": "Second", "status": "todo"},
            {"id": "T-003", "title": "Third", "status": "todo"},
        ]}],
    }))
    for sub in ("handovers", "issues", "lessons", "ideas", "tasks"):
        (d / sub).mkdir()
    monkeypatch.setattr(bs, "_backlog_path", lambda: d / "backlog.yaml")
    return d


def test_link_create_writes_both_sides(tm_dir):
    out = bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    assert "ok" in out.lower()
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    t2 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-002")
    assert {"type": "depends_on", "target": "T-002"} in entity_links(t1)
    assert {"type": "blocks", "target": "T-001"} in entity_links(t2)


def test_link_create_rejects_unknown_type(tm_dir):
    out = bs.backlog_link_create(source="T-001", target="T-002", type="nope")
    assert "invalid" in out.lower() or "unknown" in out.lower()


def test_link_create_rejects_domain_mismatch(tm_dir):
    # depends_on is task->task; T-001 -> ISS-007 should fail.
    out = bs.backlog_link_create(source="T-001", target="ISS-007", type="depends_on")
    assert "invalid" in out.lower()


def test_link_create_rejects_missing_target(tm_dir):
    out = bs.backlog_link_create(source="T-001", target="T-999", type="depends_on")
    assert "not found" in out.lower() or "missing" in out.lower()


def test_link_create_rejects_self_cycle(tm_dir):
    out = bs.backlog_link_create(source="T-001", target="T-001", type="depends_on")
    assert "cycle" in out.lower()
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    assert entity_links(t1) == []


def test_link_create_rejects_two_node_cycle(tm_dir):
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    out = bs.backlog_link_create(source="T-002", target="T-001", type="depends_on")
    assert "cycle" in out.lower()


def test_link_create_rejects_three_node_cycle(tm_dir):
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    bs.backlog_link_create(source="T-002", target="T-003", type="depends_on")
    out = bs.backlog_link_create(source="T-003", target="T-001", type="depends_on")
    assert "cycle" in out.lower()


def test_link_create_idempotent(tm_dir):
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    count = sum(1 for link in entity_links(t1)
                if link == {"type": "depends_on", "target": "T-002"})
    assert count == 1
