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


import yaml

def _seed_backlog(tmp_path):
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{
            "id": "asset-engine", "name": "Asset Engine", "status": "active",
            "description": "Ingest + thumbnail.", "created": "2026-05-27",
            "tasks": [{"id": "ae-1", "title": "Ingest task", "status": "todo"}],
        }],
        "phases": [{
            "id": "ship-v3", "name": "Ship V3", "status": "active", "order": 1,
            "description": "Wrap up.", "created": "2026-05-27",
        }],
        "context": {},
    }
    bp.write_text(yaml.safe_dump(data), encoding="utf-8")
    return bp

def test_save_v3_writes_epic_and_phase_bodies(tmp_path):
    bp = _seed_backlog(tmp_path)
    data = v3.load_v3(bp)
    v3.save_v3(bp, data)
    epic_md = v3.epic_file_path(bp, "asset-engine")
    phase_md = v3.phase_file_path(bp, "ship-v3")
    assert epic_md.exists() and "Ingest + thumbnail." in epic_md.read_text(encoding="utf-8")
    assert phase_md.exists() and "Wrap up." in phase_md.read_text(encoding="utf-8")
    slim = yaml.safe_load(bp.read_text(encoding="utf-8"))
    assert "description" not in slim["epics"][0]
    assert slim["epics"][0]["id"] == "asset-engine"
    assert slim["epics"][0]["tasks"][0]["id"] == "ae-1"
    assert "description" not in slim["phases"][0]


def test_load_v3_merges_epic_and_phase_bodies(tmp_path):
    bp = _seed_backlog(tmp_path)
    v3.save_v3(bp, v3.load_v3(bp))
    data = v3.load_v3(bp)
    epic = data["epics"][0]
    assert epic["description"].startswith("Ingest + thumbnail.")
    assert epic["tasks"][0]["id"] == "ae-1"
    phase = data["phases"][0]
    assert phase["description"].startswith("Wrap up.")

def test_load_v3_backward_compat_inline_description(tmp_path):
    bp = _seed_backlog(tmp_path)
    data = v3.load_v3(bp)
    assert data["epics"][0]["description"].startswith("Ingest + thumbnail.")
    assert not v3.epic_file_path(bp, "asset-engine").exists()
