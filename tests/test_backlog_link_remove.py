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
        ]}],
    }))
    for sub in ("handovers", "issues", "ideas", "tasks"):
        (d / sub).mkdir()
    monkeypatch.setattr(bs, "_backlog_path", lambda: d / "backlog.yaml")
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    bs.backlog_link_create(source="T-001", target="T-002", type="relates_to")
    return d


def test_link_remove_drops_both_sides(tm_dir):
    out = bs.backlog_link_remove(source="T-001", target="T-002", type="depends_on")
    assert "ok" in out.lower()
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    t2 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-002")
    assert {"type": "depends_on", "target": "T-002"} not in entity_links(t1)
    assert {"type": "blocks",     "target": "T-001"} not in entity_links(t2)
    # The other link (relates_to) survives.
    assert {"type": "relates_to", "target": "T-002"} in entity_links(t1)


def test_link_remove_without_type_drops_all_between_pair(tm_dir):
    bs.backlog_link_remove(source="T-001", target="T-002")
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    t2 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-002")
    assert all(link["target"] != "T-002" for link in entity_links(t1))
    assert all(link["target"] != "T-001" for link in entity_links(t2))


def test_link_remove_missing_link_is_noop(tm_dir):
    bs.backlog_link_remove(source="T-001", target="T-002", type="depends_on")
    out = bs.backlog_link_remove(source="T-001", target="T-002", type="depends_on")
    assert "no-op" in out.lower() or "not present" in out.lower() or "ok" in out.lower()
