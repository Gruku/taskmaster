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
