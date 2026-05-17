from pathlib import Path
import json
import pytest
import yaml

import backlog_server as bs
from taskmaster_v3 import (
    read_entity_anywhere, write_entity_anywhere, set_entity_links,
    entity_links, BODY_KEY,
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


def test_validate_reports_orphan_target(tm_dir):
    # Hand-edit T-001 to link to a missing target.
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    set_entity_links(t1, [{"type": "depends_on", "target": "T-999"}])
    write_entity_anywhere(tm_dir / "backlog.yaml", t1)

    out = bs.backlog_link_validate()
    data = json.loads(out)
    assert any(o["source"] == "T-001" and o["target"] == "T-999"
               for o in data["orphans"])


def test_validate_reports_asymmetric_pair(tm_dir):
    # Add depends_on on T-001 without inverse on T-002.
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    set_entity_links(t1, [{"type": "depends_on", "target": "T-002"}])
    write_entity_anywhere(tm_dir / "backlog.yaml", t1)

    out = bs.backlog_link_validate()
    data = json.loads(out)
    assert any(a["source"] == "T-001" and a["target"] == "T-002"
               and a["missing_inverse"] == "blocks"
               for a in data["asymmetric"])


def test_validate_reports_cycles(tm_dir):
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    set_entity_links(t1, [{"type": "depends_on", "target": "T-002"}])
    write_entity_anywhere(tm_dir / "backlog.yaml", t1)
    t2 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-002")
    set_entity_links(t2, [{"type": "depends_on", "target": "T-001"}])
    write_entity_anywhere(tm_dir / "backlog.yaml", t2)

    out = bs.backlog_link_validate()
    data = json.loads(out)
    assert len(data["cycles"]) >= 1
    cycle = data["cycles"][0]
    assert set(cycle) >= {"T-001", "T-002"}


def test_validate_clean_returns_empty_arrays(tm_dir):
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    out = bs.backlog_link_validate()
    data = json.loads(out)
    assert data["orphans"] == []
    assert data["asymmetric"] == []
    assert data["cycles"] == []


def test_validate_reports_archived_target_as_warning(tm_dir):
    # Spec §6B: if a target entity is archived/deleted, links to it are flagged
    # in backlog_link_validate but NOT auto-removed.
    iss_entity = {
        "id": "ISS-001", "title": "Bug", "tldr": "x", "severity": "P2",
        "status": "open", BODY_KEY: "body",
    }
    write_entity_anywhere(tm_dir / "backlog.yaml", iss_entity)
    bs.backlog_link_create(source="T-001", target="ISS-001", type="fixes")

    # Archive the issue by mutating its status field in place.
    iss = read_entity_anywhere(tm_dir / "backlog.yaml", "ISS-001")
    iss["status"] = "archived"
    write_entity_anywhere(tm_dir / "backlog.yaml", iss)

    out = bs.backlog_link_validate()
    data = json.loads(out)

    # The fixes link from T-001 to ISS-001 must still exist (not auto-removed).
    t1 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-001")
    assert {"type": "fixes", "target": "ISS-001"} in entity_links(t1)

    # The archived target must be reported as a warning — it must appear in
    # orphans (or a dedicated "archived_targets" list if the implementation adds one).
    flagged_targets = {o["target"] for o in data.get("orphans", [])}
    flagged_targets |= {o["target"] for o in data.get("archived_targets", [])}
    assert "ISS-001" in flagged_targets, (
        "backlog_link_validate must flag links to archived entities as a warning"
    )
