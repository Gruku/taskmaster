"""User intent: absence never deletes data (design spec decision 4). A
hand-edited backlog can hold an epic or phase with no `id`; the store keys rows
by id, so adoption used to drop the entity and everything written on it without
a word. Adoption now names it from its `name` and keeps it.
"""
from __future__ import annotations

import yaml

import pytest

from taskmaster import store
from taskmaster import taskmaster_v3 as v3


def _seed(tmp_path, backlog: dict):
    tm = tmp_path / ".taskmaster"
    tm.mkdir(parents=True, exist_ok=True)
    (tm / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    bp = tm / "backlog.yaml"
    bp.write_text(yaml.safe_dump(backlog, sort_keys=False), encoding="utf-8")
    store.reset_for_tests()
    return bp


@pytest.fixture()
def idless(tmp_path):
    bp = _seed(tmp_path, {
        "meta": {"project": "hand-edited", "schema_version": 3},
        "epics": [
            {"id": "kept", "name": "Kept", "status": "active", "tasks": []},
            {"name": "Asset Engine", "status": "active",
             "description": "Ingest + thumbnail.", "tasks": []},
        ],
        "phases": [
            {"id": "ship", "name": "Ship", "order": 1},
            {"name": "Ship V3", "order": 2, "description": "Wrap up."},
        ],
    })
    opened = store.open_store(backlog_path=bp, session="idless-adoption-test")
    yield bp, opened
    store.reset_for_tests()


def test_idless_epic_survives_adoption_under_a_name_derived_id(idless):
    bp, opened = idless
    epics = {e["id"]: e for e in opened.load_dict()["epics"]}
    assert "kept" in epics
    assert "asset-engine" in epics, sorted(epics)
    assert epics["asset-engine"]["name"] == "Asset Engine"
    assert epics["asset-engine"]["description"] == "Ingest + thumbnail."


def test_idless_phase_survives_adoption_under_a_name_derived_id(idless):
    bp, opened = idless
    phases = {p["id"]: p for p in opened.load_dict()["phases"]}
    assert "ship" in phases
    assert "ship-v3" in phases, sorted(phases)
    assert phases["ship-v3"]["description"] == "Wrap up."


def test_the_synthesized_id_reaches_the_projection(idless):
    bp, _opened = idless
    projected = yaml.safe_load(bp.read_text(encoding="utf-8"))
    assert {e["id"] for e in projected["epics"]} == {"kept", "asset-engine"}
    assert {p["id"] for p in projected["phases"]} == {"ship", "ship-v3"}


def test_a_derived_id_that_collides_is_suffixed(tmp_path):
    bp = _seed(tmp_path, {
        "meta": {"project": "hand-edited", "schema_version": 3},
        "epics": [
            {"id": "asset-engine", "name": "Asset Engine", "status": "active", "tasks": []},
            {"name": "Asset Engine", "status": "active",
             "description": "the second one", "tasks": []},
        ],
        "phases": [],
    })
    opened = store.open_store(backlog_path=bp, session="idless-collision-test")
    epics = {e["id"]: e for e in opened.load_dict()["epics"]}
    assert "asset-engine" in epics
    assert "asset-engine-2" in epics, sorted(epics)
    assert epics["asset-engine-2"]["description"] == "the second one"
    store.reset_for_tests()


def test_an_epic_with_neither_id_nor_name_still_survives(tmp_path):
    bp = _seed(tmp_path, {
        "meta": {"project": "hand-edited", "schema_version": 3},
        "epics": [{"status": "active", "description": "nameless", "tasks": []}],
        "phases": [],
    })
    opened = store.open_store(backlog_path=bp, session="idless-nameless-test")
    epics = opened.load_dict()["epics"]
    assert len(epics) == 1, epics
    assert epics[0]["id"], "an entity with no name still needs an id to be a row"
    assert epics[0]["description"] == "nameless"
    store.reset_for_tests()


def test_an_idless_task_survives_under_its_epics_numbering(tmp_path):
    bp = _seed(tmp_path, {
        "meta": {"project": "hand-edited", "schema_version": 3},
        "epics": [{"id": "e", "name": "E", "status": "active", "tasks": [
            {"id": "e-001", "title": "Numbered", "status": "todo"},
            {"title": "Hand added", "status": "todo", "notes": "keep me"},
        ]}],
        "phases": [],
    })
    opened = store.open_store(backlog_path=bp, session="idless-task-test")
    tasks = {t["id"]: t for e in opened.load_dict()["epics"] for t in e["tasks"]}
    assert "e-001" in tasks
    assert len(tasks) == 2, sorted(tasks)
    added = next(t for tid, t in tasks.items() if tid != "e-001")
    assert added["title"] == "Hand added"
    assert added["notes"] == "keep me"
    store.reset_for_tests()


# ── the synthesized id must not collide with what the store already knows ────


def test_idless_task_skips_a_number_a_leftover_file_already_uses(tmp_path):
    """A leftover `tasks/<epic>-NNN.md` one past the highest visible id must not
    be reused: `_import_projection` upserts every task file afterwards, so a
    reused number silently merges the stray file's fields into the new task."""
    bp = _seed(tmp_path, {
        "meta": {"project": "hand-edited", "schema_version": 3},
        "epics": [{"id": "e", "name": "E", "status": "active", "tasks": [
            {"id": "e-001", "title": "Numbered", "status": "todo"},
            {"title": "Hand added", "status": "todo"},
        ]}],
        "phases": [],
    })
    # A stray file at the number naive `highest + 1` numbering would pick.
    v3.write_task_file(
        v3.task_file_path(bp, "e-002"),
        {"id": "e-002", "title": "Leftover from an earlier run", "epic": "e",
         "status": "done", "order": 2.0},
        "leftover body",
    )

    opened = store.open_store(backlog_path=bp, session="idless-task-collision")
    tasks = {t["id"]: t for e in opened.load_dict()["epics"] for t in e["tasks"]}

    assert tasks["e-002"]["title"] == "Leftover from an earlier run", tasks
    added = next(t for tid, t in tasks.items() if tid not in {"e-001", "e-002"})
    assert added["title"] == "Hand added", tasks
    assert added["status"] == "todo"
    store.reset_for_tests()


def test_a_task_under_an_idless_epic_gets_that_epics_synthesized_id(tmp_path):
    """The legacy `epic` backfill must run after the epic has been named, or the
    task lands with `epic: None` and belongs to nothing."""
    bp = _seed(tmp_path, {
        "meta": {"project": "hand-edited", "schema_version": 3},
        "epics": [{"name": "Asset Engine", "status": "active", "tasks": [
            {"id": "ae-1", "title": "Ingest", "status": "todo"},
        ]}],
        "phases": [],
    })
    opened = store.open_store(backlog_path=bp, session="idless-epic-task")

    data = opened.load_dict()
    epic = data["epics"][0]
    assert epic["id"] == "asset-engine"
    task = epic["tasks"][0]
    assert task["epic"] == "asset-engine", task
    exported = v3.task_file_path(bp, "ae-1")
    assert exported.exists()
    fm, _ = v3.read_task_file(exported)
    assert fm["epic"] == "asset-engine", fm
    store.reset_for_tests()


def test_an_idless_task_under_an_idless_epic_is_numbered_from_that_epic(tmp_path):
    bp = _seed(tmp_path, {
        "meta": {"project": "hand-edited", "schema_version": 3},
        "epics": [{"name": "Asset Engine", "status": "active", "tasks": [
            {"title": "Ingest", "status": "todo", "notes": "keep me"},
        ]}],
        "phases": [],
    })
    opened = store.open_store(backlog_path=bp, session="idless-both")

    epic = opened.load_dict()["epics"][0]
    assert epic["id"] == "asset-engine"
    task = epic["tasks"][0]
    assert task["id"] == "asset-engine-001", task
    assert task["epic"] == "asset-engine", task
    assert task["notes"] == "keep me"
    store.reset_for_tests()
