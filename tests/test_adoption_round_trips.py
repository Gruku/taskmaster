"""User intent: what adoption writes, adoption must be able to read back.

The real 2.2k-task verification (task 5.3) found the opposite: an epic that
existed only as `epics/<id>.md` lost its id and title on import, the exporter
wrote `- {}` into backlog.yaml, tasks with no `epic:` key fanned into both
id-less epics, and the *second* cold open of the migrated tree died with
"task … appears twice" before any tool ran. The projection was unloadable and
the epic names were gone from every file.
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


def _reopen_cold(bp):
    """Drop the database and open the migrated projection from scratch.

    This is what a fresh clone does: `local/` is gitignored, so the store never
    travels with the repo and every second checkout adopts the exported files.
    """
    store.reset_for_tests()
    local = bp.parent / "local"
    for leftover in local.glob("store.db*"):
        leftover.unlink()
    return store.open_store(backlog_path=bp, session="second-cold-open")


# ── an epic that exists only as a file keeps its identity ────────────────────


def test_an_epic_only_on_disk_keeps_its_id_and_title(tmp_path):
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "listed", "name": "Listed", "status": "active", "tasks": []}],
        "phases": [], "context": {},
    })
    v3.write_task_file(
        v3.epic_file_path(bp, "export-comply"),
        {"id": "export-comply", "title": "Export Compliance", "status": "active",
         "description": "the heavy half"},
        "epic body",
    )

    loaded = store.open_store(
        backlog_path=bp, session="epic-only-on-disk"
    ).load_dict()

    epics = {e.get("id"): e for e in loaded["epics"]}
    assert "export-comply" in epics, sorted(epics)
    recovered = epics["export-comply"]
    assert recovered["title"] == "Export Compliance", recovered
    assert recovered["description"] == "the heavy half"
    store.reset_for_tests()


def test_the_exporter_never_writes_an_id_less_epic_into_backlog_yaml(tmp_path):
    """`- {}` in backlog.yaml is the shape that bricked the second open."""
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [], "phases": [], "context": {},
    })
    v3.write_task_file(
        v3.epic_file_path(bp, "sp-v5-qa-wave2"),
        {"id": "sp-v5-qa-wave2", "title": "QA Wave 2", "description": "heavy"},
        "body",
    )

    store.open_store(backlog_path=bp, session="no-empty-epic-entry")

    exported = bp.read_text(encoding="utf-8")
    assert "- {}" not in exported, exported
    projected = yaml.safe_load(exported)
    assert all(e.get("id") for e in projected["epics"] or []), projected["epics"]
    # And the title survived into the file the user reads.
    epic_md = v3.epic_file_path(bp, "sp-v5-qa-wave2").read_text(encoding="utf-8")
    assert "QA Wave 2" in epic_md, epic_md
    store.reset_for_tests()


def test_a_second_cold_open_of_the_migrated_tree_succeeds(tmp_path):
    """The whole chain, end to end: adopt, throw the database away, reopen.

    A task with no `epic:` key used to land in the `None` bucket, which was
    handed to every id-less epic in turn, so `_flatten_backlog_dict` saw the
    same task twice and refused the import.
    """
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "listed", "name": "Listed", "status": "active", "tasks": [
            {"id": "listed-001", "title": "Kept", "status": "todo"},
        ]}],
        "phases": [], "context": {},
    })
    for stem in ("export-comply", "sp-v5-qa-wave2"):
        v3.write_task_file(
            v3.epic_file_path(bp, stem),
            {"id": stem, "title": stem.upper(), "description": "heavy"},
            "body",
        )
    # Tasks whose file carries no `epic:` at all — the real backlog had four.
    for stem in ("ac-slider-gate-006", "pb-reliability-009"):
        v3.write_task_file(
            v3.task_file_path(bp, stem),
            {"id": stem, "title": "Orphaned", "status": "todo", "order": 1.0},
            "task body",
        )

    store.open_store(backlog_path=bp, session="first-cold-open").load_dict()
    reopened = _reopen_cold(bp)

    loaded = reopened.load_dict()
    epics = {e.get("id"): e for e in loaded["epics"]}
    assert {"listed", "export-comply", "sp-v5-qa-wave2"} <= set(epics), sorted(epics)
    seen = [t.get("id") for e in loaded["epics"] for t in e.get("tasks") or []]
    assert len(seen) == len(set(seen)), seen
    store.reset_for_tests()


def test_a_task_with_no_epic_is_an_orphan_not_a_shared_bucket(tmp_path):
    """`load_v4` keyed its epic buckets by `e.get("id")`, so a `None` id was a
    real key and every task without an `epic:` joined it."""
    bp = _seed(tmp_path, {
        "version": 4, "project": "t",
        "meta": {"updated": "", "schema_version": 4},
        "epics": [{"name": "No id here", "status": "active"}],
        "phases": [],
    })
    v3.write_task_file(
        v3.task_file_path(bp, "loose-001"),
        {"id": "loose-001", "title": "No epic", "status": "todo", "order": 1.0},
        "body",
    )

    data = v3.load_v4(bp)

    assert data["epics"][0].get("tasks") == [], data["epics"][0]
    assert "loose-001" in (data.get("_orphan_tasks") or []), data.get("_orphan_tasks")


# ── the dashboard survives an epic with no name ──────────────────────────────


def test_backlog_status_survives_an_epic_without_a_name(tmp_path, monkeypatch):
    """`backlog_status` is the first call of every session. It indexed
    `epic["name"]` unguarded while every neighbouring access used `.get`, so one
    nameless epic took the whole dashboard down with a KeyError."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "nameless", "status": "active", "tasks": [
            {"id": "nameless-001", "status": "in-progress"},
        ]}],
        "phases": [], "context": {},
    })
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    store.open_store(backlog_path=bp, session="status-nameless-epic")

    out = bs.backlog_status()

    assert "Error" not in out, out
    assert "nameless" in out, out
    store.reset_for_tests()
