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
    for sub in ("handovers", "issues", "ideas", "tasks"):
        (d / sub).mkdir()
    monkeypatch.setattr(bs, "_backlog_path", lambda: d / "backlog.yaml")
    return d


def test_link_create_writes_both_sides(tm_dir):
    out = bs.backlog_link_create(source="T-001", target="T-002", type="references")
    assert "ok" in out.lower()
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    t2 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-002")
    assert {"type": "references", "target": "T-002"} in entity_links(t1)
    assert {"type": "referenced_by", "target": "T-001"} in entity_links(t2)


def test_link_create_rejects_unknown_type(tm_dir):
    out = bs.backlog_link_create(source="T-001", target="T-002", type="nope")
    assert "invalid" in out.lower() or "unknown" in out.lower()


def test_link_create_rejects_domain_mismatch(tm_dir):
    # depends_on is task->task; a task -> an existing issue should fail.
    from tests.entity_helpers import write_issue
    issue, _path = write_issue(tm_dir / "backlog.yaml", title="Known", severity="P2", evidence="seen")
    out = bs.backlog_link_create(source="T-001", target=issue, type="depends_on")
    assert "invalid" in out.lower() and "issue" in out


def test_link_create_rejects_missing_target(tm_dir):
    out = bs.backlog_link_create(source="T-001", target="T-999", type="depends_on")
    assert "not found" in out.lower() or "missing" in out.lower()


@pytest.mark.parametrize("link_type", ["depends_on", "blocks"])
@pytest.mark.parametrize("target", ["T-002", "T-001"])
def test_link_create_refuses_a_dependency_between_tasks(tm_dir, link_type, target):
    """Every gate reads `depends_on`, never `links`: a dependency link between tasks
    would be reported as made and gate nothing, so it is refused with the call that
    sets the field (whose +id form refuses self-dependencies and cycles)."""
    out = bs.backlog_link_create(source="T-001", target=target, type=link_type)
    assert out.startswith("Error:") and "backlog_update_task" in out
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    assert entity_links(t1) == []


def test_link_create_idempotent(tm_dir):
    bs.backlog_link_create(source="T-001", target="T-002", type="references")
    bs.backlog_link_create(source="T-001", target="T-002", type="references")
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    count = sum(1 for link in entity_links(t1)
                if link == {"type": "references", "target": "T-002"})
    assert count == 1
