"""Tests for auto_link_on_save — inline-reference materialization."""
from pathlib import Path
import pytest
import yaml

from taskmaster_v3 import (
    auto_link_on_save,
    entity_links,
    read_entity_anywhere,
    write_entity_anywhere,
    BODY_KEY,
)


@pytest.fixture
def tm_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".taskmaster"
    d.mkdir()
    (d / "backlog.yaml").write_text(yaml.safe_dump({
        "meta": {"schema_version": 3},
        "epics": [{"id": "e1", "title": "E", "tasks": [
            {"id": "T-001", "title": "First", "status": "todo"},
            {"id": "T-005", "title": "Fifth", "status": "todo"},
        ]}],
    }))
    for sub in ("handovers", "issues", "lessons", "ideas", "tasks"):
        (d / sub).mkdir()
    return d


def _seed_handover(tm_dir: Path, hid: str, body: str, *, task_ids=None, auto_link=None,
                    links=None) -> None:
    fm: dict = {
        "id": hid,
        "tldr": "test",
        "status": "open",
        "task_ids": task_ids or [],
        BODY_KEY: body,
    }
    if auto_link is not None:
        fm["auto_link"] = auto_link
    if links is not None:
        fm["links"] = links
    write_entity_anywhere(tm_dir / "backlog.yaml", fm)


def _seed_issue(tm_dir: Path, iid: str, body: str = "Body.") -> None:
    fm = {
        "id": iid,
        "title": "Bug",
        "tldr": "test",
        "severity": "P2",
        "status": "open",
        BODY_KEY: body,
    }
    write_entity_anywhere(tm_dir / "backlog.yaml", fm)


def test_auto_link_adds_references_for_inline_mentions(tm_dir):
    _seed_handover(tm_dir, "HND-001", "T-001 done, next start T-005.", task_ids=["T-001"])
    auto_link_on_save(tm_dir / "backlog.yaml", "HND-001")

    hnd = read_entity_anywhere(tm_dir / "backlog.yaml", "HND-001")
    targets = {link["target"] for link in entity_links(hnd) if link["type"] == "references"}
    assert {"T-001", "T-005"}.issubset(targets)


def test_auto_link_writes_referenced_by_on_targets(tm_dir):
    _seed_handover(tm_dir, "HND-001", "See T-005 for details.")
    auto_link_on_save(tm_dir / "backlog.yaml", "HND-001")

    t5 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-005")
    assert {"type": "referenced_by", "target": "HND-001"} in entity_links(t5)


def test_auto_link_respects_auto_link_false(tm_dir):
    _seed_handover(tm_dir, "HND-001", "Mentions T-005 but should not link.",
                    auto_link=False)
    auto_link_on_save(tm_dir / "backlog.yaml", "HND-001")

    hnd = read_entity_anywhere(tm_dir / "backlog.yaml", "HND-001")
    assert entity_links(hnd) == []
    t5 = read_entity_anywhere(tm_dir / "backlog.yaml", "T-005")
    assert {"type": "referenced_by", "target": "HND-001"} not in entity_links(t5)


def test_auto_link_does_not_overwrite_stronger_explicit_link(tm_dir):
    # Pre-existing explicit relates_to link.
    _seed_issue(tm_dir, "ISS-001")
    _seed_handover(tm_dir, "HND-001",
                   "Discussion of ISS-001 in body.",
                   task_ids=["T-001"],
                   links=[{"type": "relates_to", "target": "ISS-001"}])
    auto_link_on_save(tm_dir / "backlog.yaml", "HND-001")

    hnd = read_entity_anywhere(tm_dir / "backlog.yaml", "HND-001")
    types_to_iss = {link["type"] for link in entity_links(hnd) if link["target"] == "ISS-001"}
    # ISS-001 already has a link of any type; auto-detection skips it entirely.
    assert types_to_iss == {"relates_to"}


def test_auto_link_excludes_self_reference(tm_dir):
    _seed_handover(tm_dir, "HND-001",
                   "This handover is HND-001 — should not self-link.")
    auto_link_on_save(tm_dir / "backlog.yaml", "HND-001")

    hnd = read_entity_anywhere(tm_dir / "backlog.yaml", "HND-001")
    assert entity_links(hnd) == []


def test_auto_link_skips_missing_targets(tm_dir):
    _seed_handover(tm_dir, "HND-001", "Mentions T-999 which doesn't exist.")
    auto_link_on_save(tm_dir / "backlog.yaml", "HND-001")
    hnd = read_entity_anywhere(tm_dir / "backlog.yaml", "HND-001")
    # Missing target → skipped, not a hard error.
    assert all(link["target"] != "T-999" for link in entity_links(hnd))


# B-033 test: task BODY_KEY must be included in the auto-link scan body
def test_auto_link_scans_task_body_key(tm_dir):
    """T-002's markdown body mentions T-001; auto_link_on_save must detect it."""
    bp = tm_dir / "backlog.yaml"
    # Add T-002 to the backlog.
    data = yaml.safe_load(bp.read_text(encoding="utf-8"))
    data["epics"][0]["tasks"].append(
        {"id": "T-002", "title": "Second", "status": "todo"}
    )
    bp.write_text(yaml.safe_dump(data), encoding="utf-8")

    # Write T-002 with a body that references T-001.
    t2 = read_entity_anywhere(bp, "T-002")
    t2[BODY_KEY] = "This task depends on T-001 being done first."
    write_entity_anywhere(bp, t2)

    added = auto_link_on_save(bp, "T-002")

    # The return value must include T-001.
    assert "T-001" in added

    # A references link to T-001 must be persisted on T-002.
    t2_after = read_entity_anywhere(bp, "T-002")
    ref_targets = {link["target"] for link in entity_links(t2_after)
                   if link["type"] == "references"}
    assert "T-001" in ref_targets


def _seed_two_tasks(tmp_taskmaster) -> None:
    backlog_path = tmp_taskmaster / ".taskmaster" / "backlog.yaml"
    data = yaml.safe_load(backlog_path.read_text(encoding="utf-8"))
    data["epics"] = [{
        "id": "e1", "name": "E", "tasks": [
            {"id": "T-001", "title": "First", "tldr": "x", "status": "todo"},
            {"id": "T-005", "title": "Fifth", "tldr": "x", "status": "todo"},
        ],
    }]
    backlog_path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_handover_create_auto_links_on_save(tmp_taskmaster):
    """backlog_handover_create wires auto_link_on_save."""
    import backlog_server as bs
    _seed_two_tasks(tmp_taskmaster)

    bs.backlog_handover_create(task_ids=["T-001"], tldr="x", next_action="",
                               body="Next: pick up T-005.")

    bp = tmp_taskmaster / ".taskmaster" / "backlog.yaml"
    hids = sorted((bp.parent / "handovers").glob("*.md"))
    assert hids
    # Handover IDs are not the HND- prefixed format — they're date-slug based.
    # entity_kind_of returns None for those, so we read the file directly.
    from taskmaster_v3 import read_handover
    fm, body = read_handover(bp, hids[0].stem)
    hid = fm["id"]
    targets = {link["target"] for link in fm.get("links", [])
               if link["type"] == "references"}
    assert "T-005" in targets


def test_issue_create_auto_links_on_save(tmp_taskmaster):
    """backlog_issue_create wires auto_link_on_save."""
    import backlog_server as bs
    _seed_two_tasks(tmp_taskmaster)

    bs.backlog_issue_create(title="Bug", severity="P1", tldr="x",
                            impact="fixture evidence.",
                            body="Related: T-001 and T-005.")
    bp = tmp_taskmaster / ".taskmaster" / "backlog.yaml"
    iss = read_entity_anywhere(bp, "ISS-001")
    targets = {link["target"] for link in entity_links(iss)
               if link["type"] == "references"}
    assert {"T-001", "T-005"}.issubset(targets)
