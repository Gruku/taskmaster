"""User intent: adoption runs on a first *read* now, unattended. Nothing it does
may destroy data the user could still need, and nothing it does may abort the
whole adoption over an id it could have chosen differently. Absence never
deletes data (design spec decision 4).
"""
from __future__ import annotations

import yaml

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


# ── the pre-v4 backup directory is moved, never removed ──────────────────────


def test_adoption_moves_the_legacy_snapshots_directory_instead_of_deleting_it(tmp_path):
    """`.taskmaster/snapshots/` is the pre-v4 backup directory — the one thing
    that could recover a project from a bad adoption. Bootstrap used to
    `rmtree` it, outside the SQL rollback, the first time any tool (a read tool
    or a viewer GET included) opened a pre-v4 project."""
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [], "phases": [], "context": {},
    })
    snapshots = bp.parent / "snapshots"
    snapshots.mkdir()
    (snapshots / "2026-01-01.yaml").write_text("the backup\n", encoding="utf-8")

    store.open_store(backlog_path=bp, session="snapshots-relocation-test")

    assert not snapshots.exists(), "the legacy directory should have moved"
    moved = bp.parent / "local" / "snapshots" / "2026-01-01.yaml"
    assert moved.exists(), sorted(p.name for p in (bp.parent / "local").iterdir())
    assert moved.read_text(encoding="utf-8") == "the backup\n"
    store.reset_for_tests()


def test_relocating_snapshots_never_overwrites_an_existing_destination(tmp_path):
    """A second pre-v4 adoption (a restored backup, a re-created legacy dir)
    must not land on top of the first move's contents."""
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [], "phases": [], "context": {},
    })
    existing = bp.parent / "local" / "snapshots"
    existing.mkdir(parents=True)
    (existing / "keep.yaml").write_text("already here\n", encoding="utf-8")
    snapshots = bp.parent / "snapshots"
    snapshots.mkdir()
    (snapshots / "new.yaml").write_text("the newcomer\n", encoding="utf-8")

    store.open_store(backlog_path=bp, session="snapshots-collision-test")

    assert (existing / "keep.yaml").read_text(encoding="utf-8") == "already here\n"
    assert not snapshots.exists()
    suffixed = [
        p for p in (bp.parent / "local").iterdir()
        if p.name.startswith("snapshots") and p.name != "snapshots"
    ]
    assert suffixed, sorted(p.name for p in (bp.parent / "local").iterdir())
    assert (suffixed[0] / "new.yaml").read_text(encoding="utf-8") == "the newcomer\n"
    store.reset_for_tests()


# ── a synthesized task id never collides with another epic's task ────────────


def test_a_synthesized_task_id_skips_one_another_epic_already_uses(tmp_path):
    """A task moved from epic `a` to epic `b` keeps its `a-001` id. An id-less
    task under `a` must not be handed `a-001` again: `_flatten_backlog_dict`
    refuses the duplicate and the whole adoption aborts, so the project cannot
    be opened at all."""
    bp = _seed(tmp_path, {
        "meta": {"project": "hand-edited", "schema_version": 3},
        "epics": [
            {"id": "a", "name": "A", "status": "active", "tasks": [
                {"title": "Hand added", "status": "todo", "notes": "keep me"},
            ]},
            {"id": "b", "name": "B", "status": "active", "tasks": [
                {"id": "a-001", "title": "Moved here from A", "status": "todo"},
            ]},
        ],
        "phases": [],
    })

    loaded = store.open_store(
        backlog_path=bp, session="cross-epic-task-id-test"
    ).load_dict()

    tasks = {t["id"]: t for e in loaded["epics"] for t in e.get("tasks") or []}
    assert tasks["a-001"]["title"] == "Moved here from A", tasks
    added = next(t for tid, t in tasks.items() if tid != "a-001")
    assert added["title"] == "Hand added"
    assert added["notes"] == "keep me"
    assert added["epic"] == "a", added
    store.reset_for_tests()


def test_two_id_less_tasks_in_different_epics_get_distinct_ids(tmp_path):
    bp = _seed(tmp_path, {
        "meta": {"project": "hand-edited", "schema_version": 3},
        "epics": [
            {"id": "a", "name": "A", "status": "active", "tasks": [
                {"title": "First", "status": "todo"},
            ]},
            {"id": "a-2", "name": "A2", "status": "active", "tasks": [
                {"title": "Second", "status": "todo"},
            ]},
        ],
        "phases": [],
    })

    loaded = store.open_store(
        backlog_path=bp, session="two-idless-tasks-test"
    ).load_dict()

    ids = [t["id"] for e in loaded["epics"] for t in e.get("tasks") or []]
    assert len(ids) == 2 and len(set(ids)) == 2, ids
    titles = {t["id"]: t["title"] for e in loaded["epics"] for t in e.get("tasks") or []}
    assert sorted(titles.values()) == ["First", "Second"]
    store.reset_for_tests()


def test_a_synthesized_task_id_skips_a_file_belonging_to_another_epic(tmp_path):
    """`tasks/a-001.md` on disk under a different epic is the same collision in
    its file form: the per-file import upserts, so reusing the number merges the
    stray file's fields onto the task just named."""
    bp = _seed(tmp_path, {
        "meta": {"project": "hand-edited", "schema_version": 3},
        "epics": [
            {"id": "a", "name": "A", "status": "active", "tasks": [
                {"title": "Hand added", "status": "todo"},
            ]},
            {"id": "b", "name": "B", "status": "active", "tasks": []},
        ],
        "phases": [],
    })
    v3.write_task_file(
        v3.task_file_path(bp, "a-001"),
        {"id": "a-001", "title": "Owned by B", "epic": "b", "status": "done",
         "order": 1.0},
        "moved body",
    )

    loaded = store.open_store(
        backlog_path=bp, session="cross-epic-task-file-test"
    ).load_dict()

    tasks = {t["id"]: t for e in loaded["epics"] for t in e.get("tasks") or []}
    assert tasks["a-001"]["title"] == "Owned by B", tasks
    added = next(t for tid, t in tasks.items() if tid != "a-001")
    assert added["title"] == "Hand added", tasks
    store.reset_for_tests()
