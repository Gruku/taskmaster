from pathlib import Path
import taskmaster_v3 as v3


def test_entity_constants_present():
    assert v3.EPIC_HEAVY_FIELDS == ("description", "docs", "components")
    assert v3.PHASE_HEAVY_FIELDS == ("description", "docs")
    assert "epic" in v3.SLIM_FIELDS and "phase" in v3.SLIM_FIELDS
    assert "epic" in v3.CANONICAL_SECTIONS and "phase" in v3.CANONICAL_SECTIONS


def test_entity_file_paths():
    bp = Path("/proj/.taskmaster/backlog.yaml")
    assert v3.epic_file_path(bp, "asset-engine") == Path("/proj/.taskmaster/epics/asset-engine.md")
    assert v3.phase_file_path(bp, "ship-v3") == Path("/proj/.taskmaster/phases/ship-v3.md")


def test_split_merge_epic_roundtrip():
    epic = {
        "id": "asset-engine", "name": "Asset Engine", "status": "active",
        "design_status": "locked", "created": "2026-05-27",
        "description": "Ingest + thumbnail + CDN.",
        "docs": {"design": "specs/asset-engine.md"},
        "components": {"ingest": {"title": "Ingest", "after": []}},
        "_body": "# Asset Engine\n\nWhat we are building.\n",
    }
    slim, heavy, body = v3._split_entity_for_v3(epic, v3.EPIC_HEAVY_FIELDS)
    assert slim["id"] == "asset-engine" and slim["design_status"] == "locked"
    assert "description" not in slim and "components" not in slim
    assert heavy["description"].startswith("Ingest")
    assert heavy["components"]["ingest"]["title"] == "Ingest"
    assert body.startswith("# Asset Engine")
    assert heavy["id"] == "asset-engine" and heavy["title"] == "Asset Engine"
    merged = v3._merge_entity_from_v3(slim, heavy, body, v3.EPIC_HEAVY_FIELDS)
    assert merged["description"].startswith("Ingest")
    assert merged["components"]["ingest"]["title"] == "Ingest"
    assert merged["_body"].startswith("# Asset Engine")
    assert "title" not in merged  # epics use `name`, not `title`
