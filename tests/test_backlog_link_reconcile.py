from pathlib import Path
import json
import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster.taskmaster_v3 import (
    read_entity_anywhere, write_entity_anywhere, set_entity_links, entity_links,
)


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
    for sub in ("handovers", "issues", "lessons", "ideas", "tasks"):
        (d / sub).mkdir()
    monkeypatch.setattr(bs, "_backlog_path", lambda: d / "backlog.yaml")
    return d


def test_reconcile_adds_missing_inverse(tm_dir):
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    set_entity_links(t1, [{"type": "depends_on", "target": "T-002"}])
    write_entity_anywhere(tm_dir / "backlog.yaml", t1)

    out = bs.backlog_link_reconcile()
    data = json.loads(out)
    assert data["fixed"] >= 1

    t2 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-002")
    assert {"type": "blocks", "target": "T-001"} in entity_links(t2)


def test_reconcile_reports_unfixable_orphan(tm_dir):
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    set_entity_links(t1, [{"type": "depends_on", "target": "T-999"}])
    write_entity_anywhere(tm_dir / "backlog.yaml", t1)

    out = bs.backlog_link_reconcile()
    data = json.loads(out)
    assert any(o["target"] == "T-999" for o in data["unfixable"])


def test_reconcile_idempotent(tm_dir):
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    out1 = bs.backlog_link_reconcile()
    out2 = bs.backlog_link_reconcile()
    d1, d2 = json.loads(out1), json.loads(out2)
    assert d1["fixed"] == 0
    assert d2["fixed"] == 0
