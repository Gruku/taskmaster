"""End-to-end Plan C smoke test:
- create-link-query-remove across all entity kinds
- cycle prevention
- symmetric sync
- auto-detection on save
- migration from a fully-legacy project
"""
from pathlib import Path
import json
import subprocess
import sys
import pytest
import yaml

import backlog_server as bs
from taskmaster_v3 import (
    read_entity_anywhere, entity_links, BODY_KEY, write_entity_anywhere,
)


@pytest.fixture
def tm_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / ".taskmaster"
    d.mkdir()
    (d / "backlog.yaml").write_text(yaml.safe_dump({
        "meta": {"schema_version": 3},
        "epics": [{"id": "e1", "title": "E", "tasks": [
            {"id": "T-001", "title": "First",  "tldr": "x", "status": "todo"},
            {"id": "T-002", "title": "Second", "tldr": "x", "status": "todo"},
            {"id": "T-003", "title": "Third",  "tldr": "x", "status": "todo"},
        ]}],
    }))
    for sub in ("handovers", "issues", "lessons", "ideas", "tasks"):
        (d / sub).mkdir()
    monkeypatch.setattr(bs, "_backlog_path", lambda: d / "backlog.yaml")
    return d


def test_create_link_query_remove_round_trip(tm_dir):
    # Seed peers of each kind directly via write_entity_anywhere so we don't
    # depend on the date-slug handover ID format or the bump-and-retry idea
    # writer (both of which interact with the v3 writers in subtler ways).
    write_entity_anywhere(tm_dir / "backlog.yaml", {
        "id": "ISS-001", "title": "Bug", "tldr": "x", "severity": "P1",
        "status": "open", BODY_KEY: "body",
    })
    write_entity_anywhere(tm_dir / "backlog.yaml", {
        "id": "L-001", "title": "L", "tldr": "x", "kind": "pattern",
        "tier": "active", BODY_KEY: "body",
    })
    write_entity_anywhere(tm_dir / "backlog.yaml", {
        "id": "HND-001", "tldr": "x", "status": "open", "task_ids": ["T-001"],
        BODY_KEY: "body",
    })
    write_entity_anywhere(tm_dir / "backlog.yaml", {
        "id": "IDEA-001", "title": "I", "tldr": "x", BODY_KEY: "body",
    })

    # All link types we can validate.
    pairs = [
        ("T-001", "T-002",   "depends_on",  "blocks"),
        ("T-001", "ISS-001", "fixes",        "fixed_in_task"),
        ("T-001", "L-001",   "informed_by",  "informs"),
        ("T-001", "ISS-001", "relates_to",   "relates_to"),
        ("T-001", "HND-001", "references",   "referenced_by"),
        ("T-001", "IDEA-001", "relates_to",  "relates_to"),
    ]
    for src, dst, t, inv in pairs:
        out = bs.backlog_link_create(source=src, target=dst, type=t)
        assert "ok" in out.lower(), out
        src_e = read_entity_anywhere(tm_dir / "backlog.yaml", src)
        dst_e = read_entity_anywhere(tm_dir / "backlog.yaml", dst)
        assert {"type": t,   "target": dst} in entity_links(src_e)
        assert {"type": inv, "target": src} in entity_links(dst_e)

    # Query and remove.
    q = json.loads(bs.backlog_link_query(source="T-001"))
    assert len(q) >= len(pairs)
    for src, dst, t, _ in pairs:
        bs.backlog_link_remove(source=src, target=dst, type=t)
        src_e = read_entity_anywhere(tm_dir / "backlog.yaml", src)
        assert {"type": t, "target": dst} not in entity_links(src_e)


def test_cycle_prevention_blocks_3_node(tm_dir):
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    bs.backlog_link_create(source="T-002", target="T-003", type="depends_on")
    out = bs.backlog_link_create(source="T-003", target="T-001", type="depends_on")
    assert "cycle" in out.lower()


def test_auto_detection_e2e(tmp_taskmaster):
    # Seed two extra tasks so they exist for auto-link target resolution.
    backlog_path = tmp_taskmaster / ".taskmaster" / "backlog.yaml"
    data = yaml.safe_load(backlog_path.read_text(encoding="utf-8"))
    data["epics"] = [{
        "id": "e1", "name": "E", "tasks": [
            {"id": "T-001", "title": "First",  "tldr": "x", "status": "todo"},
            {"id": "T-002", "title": "Second", "tldr": "x", "status": "todo"},
            {"id": "T-003", "title": "Third",  "tldr": "x", "status": "todo"},
        ],
    }]
    backlog_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    # Use a handover with date-slug id (real production format).
    bs.backlog_handover_create(task_ids=["T-001"], tldr="some work", next_action="",
                               body="Picked up T-001, next start T-002, also see T-003.")
    # The handover lives somewhere; find the one we just made.
    hids = sorted((backlog_path.parent / "handovers").glob("*.md"))
    assert hids
    from taskmaster_v3 import read_handover
    fm, _ = read_handover(backlog_path, hids[0].stem)
    targets = {l["target"] for l in (fm.get("links") or []) if l["type"] == "references"}
    assert {"T-002", "T-003"}.issubset(targets)


def test_validate_clean_after_create(tm_dir):
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    bs.backlog_link_create(source="T-001", target="T-003", type="depends_on")
    data = json.loads(bs.backlog_link_validate())
    assert data["orphans"] == []
    assert data["asymmetric"] == []
    assert data["cycles"] == []


def test_migration_from_legacy_project(tmp_path):
    # Build a project with ONLY legacy fields.
    d = tmp_path / ".taskmaster"
    d.mkdir()
    (d / "backlog.yaml").write_text(yaml.safe_dump({
        "meta": {"schema_version": 3},
        "epics": [{"id": "e1", "title": "E", "tasks": [
            {"id": "T-001", "title": "A", "status": "todo",
             "depends_on": ["T-002"], "related_issues": ["ISS-001"]},
            {"id": "T-002", "title": "B", "status": "todo"},
        ]}],
    }))
    for sub in ("handovers", "issues", "lessons", "ideas", "tasks"):
        (d / sub).mkdir()
    write_entity_anywhere(d / "backlog.yaml", {
        "id": "ISS-001", "title": "Bug", "tldr": "x", "severity": "P2",
        "status": "open", "fixed_in_task": "T-001",
        BODY_KEY: "body",
    })

    result = subprocess.run(
        [sys.executable, "-m", "plugins.taskmaster.scripts.migrate_links",
         "--root", str(d.parent)],
        capture_output=True, text=True, check=False,
        cwd=str(Path(__file__).resolve().parents[3]),
    )
    assert result.returncode == 0, result.stderr

    t1 = read_entity_anywhere(d / "backlog.yaml", "T-001")
    assert {"type": "depends_on", "target": "T-002"} in entity_links(t1)
    assert {"type": "relates_to", "target": "ISS-001"} in entity_links(t1)
    assert "depends_on" not in t1  # legacy field dropped
    assert "related_issues" not in t1

    # Inverses materialized.
    t2 = read_entity_anywhere(d / "backlog.yaml", "T-002")
    assert {"type": "blocks", "target": "T-001"} in entity_links(t2)

    iss = read_entity_anywhere(d / "backlog.yaml", "ISS-001")
    assert {"type": "fixed_in_task", "target": "T-001"} in entity_links(iss)
    assert {"type": "fixes",         "target": "ISS-001"} in entity_links(t1)
    assert "fixed_in_task" not in iss  # legacy field dropped
