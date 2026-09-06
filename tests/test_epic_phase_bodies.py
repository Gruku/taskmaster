from pathlib import Path
from taskmaster import taskmaster_v3 as v3
from taskmaster.backlog_server import (
    backlog_add_phase, backlog_update_phase,
    backlog_add_epic, backlog_update_epic,
    _load as _load_srv,
)


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
    (bp.parent / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    return bp


def _adopt(bp):
    """Open the store on `bp`, which imports the projection and re-exports it.

    `save_v3` used to be the writer under test here. The store owns every write
    under `.taskmaster/` now, so the heavy-field split these tests describe is
    the store exporter's, and opening the store is how it runs.
    """
    from taskmaster import store

    store.reset_for_tests()
    return store.open_store(backlog_path=bp, session="epic-phase-bodies-test")


def test_store_export_writes_epic_and_phase_bodies(tmp_path):
    bp = _seed_backlog(tmp_path)
    _adopt(bp)
    epic_md = v3.epic_file_path(bp, "asset-engine")
    phase_md = v3.phase_file_path(bp, "ship-v3")
    assert epic_md.exists() and "Ingest + thumbnail." in epic_md.read_text(encoding="utf-8")
    assert phase_md.exists() and "Wrap up." in phase_md.read_text(encoding="utf-8")
    slim = yaml.safe_load(bp.read_text(encoding="utf-8"))
    assert "description" not in slim["epics"][0]
    assert slim["epics"][0]["id"] == "asset-engine"
    assert "description" not in slim["phases"][0]
    # The task left the index for its own file — that is the v4 shape the store
    # adopts, and the task is still there.
    assert v3.task_file_path(bp, "ae-1").exists()


def test_store_load_merges_epic_and_phase_bodies(tmp_path):
    bp = _seed_backlog(tmp_path)
    opened = _adopt(bp)
    data = opened.load_dict()
    epic = data["epics"][0]
    assert epic["description"].startswith("Ingest + thumbnail.")
    assert [t["id"] for t in epic["tasks"]] == ["ae-1"]
    phase = data["phases"][0]
    assert phase["description"].startswith("Wrap up.")

def test_load_v3_backward_compat_inline_description(tmp_path):
    bp = _seed_backlog(tmp_path)
    data = v3.load_v3(bp)
    assert data["epics"][0]["description"].startswith("Ingest + thumbnail.")
    assert not v3.epic_file_path(bp, "asset-engine").exists()


def test_existing_backlog_migrates_on_first_open(tmp_path):
    bp = _seed_backlog(tmp_path)
    assert not v3.epic_file_path(bp, "asset-engine").exists()
    from taskmaster import store

    opened = _adopt(bp)
    with store.transaction(backlog_path=bp, tool="epic-status") as tx:
        doc = tx.get("epic", "asset-engine")
        doc["status"] = "done"
        tx.put("epic", "asset-engine", doc)
    assert v3.epic_file_path(bp, "asset-engine").exists()
    reloaded = opened.load_dict()
    assert reloaded["epics"][0]["status"] == "done"
    assert reloaded["epics"][0]["description"].startswith("Ingest")


def test_store_names_an_epic_or_phase_with_no_id(tmp_path):
    """Absence never deletes data (design spec decision 4).

    An id-less epic or phase used to be dropped by `_flatten_backlog_dict`,
    taking its description, body and every task under it with it. Adoption now
    synthesizes an id from the entity's name and keeps the entity, so the
    projection carries a real `epics/<id>.md` and `phases/<id>.md`.
    """
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{
            "name": "No-Id Epic", "status": "active",
            "description": "Epic without an id.", "tasks": [],
        }],
        "phases": [{
            "name": "No-Id Phase", "status": "active", "order": 1,
            "description": "Phase without an id.",
        }],
        "context": {},
    }
    bp.write_text(yaml.safe_dump(data), encoding="utf-8")
    (bp.parent / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    opened = _adopt(bp)

    loaded = opened.load_dict()
    assert [e["id"] for e in loaded["epics"]] == ["no-id-epic"]
    assert [p["id"] for p in loaded["phases"]] == ["no-id-phase"]
    assert loaded["epics"][0]["description"] == "Epic without an id."
    assert loaded["phases"][0]["description"] == "Phase without an id."

    # The synthesized id reaches both halves of the projection.
    slim = yaml.safe_load(bp.read_text(encoding="utf-8"))
    assert [e["id"] for e in slim["epics"]] == ["no-id-epic"]
    assert [p["id"] for p in slim["phases"]] == ["no-id-phase"]
    epic_md = v3.epic_file_path(bp, "no-id-epic")
    phase_md = v3.phase_file_path(bp, "no-id-phase")
    assert epic_md.exists() and "Epic without an id." in epic_md.read_text(encoding="utf-8")
    assert phase_md.exists() and "Phase without an id." in phase_md.read_text(encoding="utf-8")


def test_a_synthesized_epic_or_phase_id_skips_an_existing_file(tmp_path):
    """`_known_ids` has to include `epics/*.md` and `phases/*.md`.

    A leftover `epics/<kebab>.md` from an earlier run holds a different entity.
    Reusing its id would make the per-file import upsert that file's heavy
    fields onto the entity just named, merging two epics into one row.
    """
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text(yaml.safe_dump({
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"name": "Asset Engine", "status": "active",
                   "description": "the id-less one", "tasks": []}],
        "phases": [{"name": "Ship V3", "status": "active", "order": 1,
                    "description": "the id-less phase"}],
        "context": {},
    }), encoding="utf-8")
    (bp.parent / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    # Leftover files sitting on exactly the kebab-cased ids naming would pick.
    v3.write_task_file(
        v3.epic_file_path(bp, "asset-engine"),
        {"id": "asset-engine", "title": "Asset Engine",
         "description": "a leftover epic"},
        "leftover epic body",
    )
    v3.write_task_file(
        v3.phase_file_path(bp, "ship-v3"),
        {"id": "ship-v3", "title": "Ship V3", "description": "a leftover phase"},
        "leftover phase body",
    )

    opened = _adopt(bp)
    loaded = opened.load_dict()

    epics = {e["id"]: e for e in loaded["epics"]}
    phases = {p["id"]: p for p in loaded["phases"]}
    assert "asset-engine-2" in epics, sorted(epics)
    assert epics["asset-engine-2"]["description"] == "the id-less one"
    assert "ship-v3-2" in phases, sorted(phases)
    assert phases["ship-v3-2"]["description"] == "the id-less phase"


def test_an_orphan_epic_file_imports_under_its_stem_as_id(tmp_path):
    """`epics/<id>.md` that backlog.yaml never mentions has no slim half.

    The merge that follows started from an empty document, so the row landed
    with no `id` key at all and every reader that keys on `epic["id"]` raised a
    KeyError on the whole listing. The file's stem is the id.
    """
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text(yaml.safe_dump({
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [], "phases": [], "context": {},
    }), encoding="utf-8")
    (bp.parent / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    v3.write_task_file(
        v3.epic_file_path(bp, "orphan-epic"),
        {"id": "orphan-epic", "title": "Orphan", "description": "nobody indexed me"},
        "orphan body",
    )
    v3.write_task_file(
        v3.phase_file_path(bp, "orphan-phase"),
        {"id": "orphan-phase", "title": "Orphan Phase", "description": "nor me"},
        "orphan phase body",
    )

    loaded = _adopt(bp).load_dict()

    epics = {e["id"]: e for e in loaded["epics"]}
    phases = {p["id"]: p for p in loaded["phases"]}
    assert epics["orphan-epic"]["description"] == "nobody indexed me"
    assert phases["orphan-phase"]["description"] == "nor me"


def test_adoption_writes_epic_phase_and_task_files_from_a_v2_backlog(tmp_path):
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 2, "project": "t",
        "meta": {"updated": "", "schema_version": 2},
        "epics": [{
            "id": "asset-engine", "name": "Asset Engine", "status": "active",
            "description": "Ingest + thumbnail.", "created": "2026-05-27",
            "tasks": [{
                "id": "ae-1", "title": "Ingest task", "status": "todo",
                "description": "Do the ingest.",
            }],
        }],
        "phases": [{
            "id": "ship-v3", "name": "Ship V3", "status": "active", "order": 1,
            "description": "Wrap up.", "created": "2026-05-27",
        }],
        "context": {},
    }
    bp.write_text(yaml.safe_dump(data), encoding="utf-8")
    (bp.parent / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    _adopt(bp)
    # Adoption exports body files for epics and phases, not only for tasks.
    assert v3.epic_file_path(bp, "asset-engine").exists()
    assert v3.phase_file_path(bp, "ship-v3").exists()
    assert v3.task_file_path(bp, "ae-1").exists()
    assert "Ingest + thumbnail." in v3.epic_file_path(bp, "asset-engine").read_text(encoding="utf-8")
    assert "Wrap up." in v3.phase_file_path(bp, "ship-v3").read_text(encoding="utf-8")
    assert "Do the ingest." in v3.task_file_path(bp, "ae-1").read_text(encoding="utf-8")


def test_phase_docs_field(tmp_taskmaster):
    backlog_add_phase("ship", "Ship")
    out = backlog_update_phase("ship", "docs", "design:docs/design/ship.md")
    assert "Error" not in out
    ph = next(p for p in _load_srv()["phases"] if p["id"] == "ship")
    assert ph["docs"]["design"] == "docs/design/ship.md"


def test_phase_docs_clear_on_empty_path(tmp_taskmaster):
    backlog_add_phase("ship2", "Ship2")
    backlog_update_phase("ship2", "docs", "design:docs/design/ship.md")
    out = backlog_update_phase("ship2", "docs", "design:")   # empty path clears the key
    assert "Error" not in out
    ph = next(p for p in _load_srv()["phases"] if p["id"] == "ship2")
    assert "docs" not in ph or "design" not in ph.get("docs", {})


def test_phase_docs_invalid_key_rejected(tmp_taskmaster):
    backlog_add_phase("ship3", "Ship3")
    out = backlog_update_phase("ship3", "docs", "bogus:docs/x.md")
    assert "Error" in out and "bogus" in out


def test_phase_docs_path_with_colons(tmp_taskmaster):
    backlog_add_phase("ship4", "Ship4")
    out = backlog_update_phase("ship4", "docs", "design:docs/a:b.md")  # split on first colon only
    assert "Error" not in out
    ph = next(p for p in _load_srv()["phases"] if p["id"] == "ship4")
    assert ph["docs"]["design"] == "docs/a:b.md"


def test_epic_docs_clear_removes_stale_body_file(tmp_taskmaster):
    # Epic whose only heavy field is `docs`: setting it writes epics/<id>.md;
    # clearing it must delete that file so docs does not resurrect on reload.
    backlog_add_epic(epic_id="cleanup", name="Cleanup", done_when="stale files removed")
    backlog_update_epic("cleanup", "docs", "design:docs/design/cleanup.md")
    bp = tmp_taskmaster / ".taskmaster" / "backlog.yaml"
    epic_md = v3.epic_file_path(bp, "cleanup")
    assert epic_md.exists()  # written on first save
    out = backlog_update_epic("cleanup", "docs", "design:")  # clear the only heavy field
    assert "Error" not in out
    ep = next(e for e in _load_srv()["epics"] if e["id"] == "cleanup")
    assert "docs" not in ep or "design" not in ep.get("docs", {})
    assert not epic_md.exists()  # stale body file removed


def test_store_clears_a_heavy_field_from_the_task_file(tmp_path):
    # Storage-layer: a task's heavy field (`description`) lands in tasks/<id>.md;
    # clearing it must take the field out of that file rather than leave it to
    # resurrect the description on reload. Under v4 every task keeps a file, so
    # the assertion is on the field, not on the file's existence.
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{
            "id": "asset-engine", "name": "Asset Engine", "status": "active",
            "created": "2026-05-27",
            "tasks": [{
                "id": "ae-1", "title": "Ingest task", "status": "todo",
                "description": "Do the ingest.",
            }],
        }],
        "phases": [],
        "context": {},
    }
    bp.write_text(yaml.safe_dump(data), encoding="utf-8")
    (bp.parent / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    from taskmaster import store

    opened = _adopt(bp)
    task_md = v3.task_file_path(bp, "ae-1")
    assert task_md.exists()
    assert "Do the ingest." in task_md.read_text(encoding="utf-8")
    with store.transaction(backlog_path=bp, tool="clear-description") as tx:
        doc = tx.get("task", "ae-1")
        doc.pop("description", None)
        tx.put("task", "ae-1", doc)
    # (a) cleared content stays cleared after a fresh load
    final = opened.load_dict()
    assert "description" not in final["epics"][0]["tasks"][0]
    # (b) and it is gone from the exported file, not merely from the row
    assert "Do the ingest." not in task_md.read_text(encoding="utf-8")
