"""User intent: a file repaired while the store holds an unwritten edit to it must
never be merged automatically (B-089). Both versions are kept, the file is
flagged and never written over, every tool result names it until someone
resolves it, and only an explicit resolution picks a side. Each reviewer probe
from the rejected merge designs is pinned here.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
import yaml

from taskmaster import store as store_mod
from taskmaster.taskmaster_v3 import render_frontmatter

from test_store_bug_cluster import _build_projection, _quarantine_task_file


DOC = {
    "id": "core-001",
    "title": "Task 1",
    "status": "todo",
    "epic": "core",
    "order": 1.0,
    "priority": "high",
}
BROKEN = "---\n: : not yaml [\n---\nbroken\n"


# ── helpers ──────────────────────────────────────────────────────────────────


def _fresh(tmp_path: Path):
    backlog_path = _build_projection(tmp_path)
    store_obj = store_mod.open_store(backlog_path=backlog_path)
    store_obj.load_dict()
    return backlog_path, store_obj


def _scan(store_obj):
    store_obj._last_read_scan_clock = None
    return store_obj.load_dict()


def _sql(store_obj, query, args=()):
    connection = sqlite3.connect(store_obj.db_path)
    try:
        rows = connection.execute(query, args).fetchall()
        connection.commit()
        return rows
    finally:
        connection.close()


def _entity(store_obj, kind="task", ident="core-001"):
    doc, body = _sql(
        store_obj, "SELECT doc,body FROM entities WHERE kind=? AND id=?", (kind, ident)
    )[0]
    return json.loads(doc), body


def _task_path(backlog_path: Path) -> Path:
    return backlog_path / "tasks" / "core-001.md"


def _put(store_obj, kind, ident, doc, body=None):
    with store_obj.transaction(tool="test-write") as tx:
        tx.put(kind, ident, dict(doc), body=body)
        return list(tx.warnings)


def _write_task(backlog_path: Path, doc, body="## Notes\n\nB") -> bytes:
    content = render_frontmatter(dict(doc), body).encode("utf-8")
    _task_path(backlog_path).write_bytes(content)
    return content


def _drop_bases(store_obj) -> None:
    """What a 6.0.2-era store holds for a quarantined row: no base at all."""
    _sql(store_obj, "DELETE FROM projection_base")


def _flagged(store_obj) -> list[str]:
    return [conflict["file"] for conflict in store_obj.projection_conflicts()]


def _assert_flag_holds(store_obj, path: Path, rel: str, repaired: bytes) -> None:
    """Flagged, untouched, and still untouched after an unrelated store write."""
    assert rel in _flagged(store_obj)
    assert path.read_bytes() == repaired
    with store_obj.transaction(tool="unrelated-write") as tx:
        other = tx.get("task", "core-002")
        other["title"] = f"Unrelated {time.monotonic_ns()}"
        tx.put("task", "core-002", other)
    assert path.read_bytes() == repaired, "a later write exported over the flagged file"
    assert rel in _flagged(store_obj)


# ── p1 / p2 / p3: tasks ──────────────────────────────────────────────────────


@pytest.mark.parametrize("drop_base", [False, True], ids=["based", "no-base"])
def test_p1_a_repair_does_not_revert_a_store_edit(tmp_path, drop_base):
    backlog_path, store_obj = _fresh(tmp_path)
    _put(store_obj, "task", "core-001", DOC, "## Notes\n\nB")
    _write_task(backlog_path, dict(DOC, status="in-progress"))
    _scan(store_obj)
    _quarantine_task_file(backlog_path, "core-001")
    _scan(store_obj)
    # The store puts status back to todo while the file is broken.
    _put(store_obj, "task", "core-001", DOC, "## Notes\n\nB")
    if drop_base:
        _drop_bases(store_obj)
    repaired = _write_task(backlog_path, dict(DOC, status="in-progress"))
    _scan(store_obj)

    assert _entity(store_obj)[0]["status"] == "todo"
    _assert_flag_holds(store_obj, _task_path(backlog_path), "tasks/core-001.md", repaired)
    assert not _sql(
        store_obj, "SELECT 1 FROM changes WHERE id='core-001' AND op='merge'"
    )


@pytest.mark.parametrize("drop_base", [False, True], ids=["based", "no-base"])
def test_p2_a_user_revert_is_kept_and_the_store_edit_is_kept(tmp_path, drop_base):
    backlog_path, store_obj = _fresh(tmp_path)
    _put(store_obj, "task", "core-001", DOC, "## Notes\n\nB")
    _write_task(backlog_path, dict(DOC, title="Hand title"))
    _scan(store_obj)
    _quarantine_task_file(backlog_path, "core-001")
    _scan(store_obj)
    _put(store_obj, "task", "core-001", dict(DOC, title="Hand title", priority="low"), "## Notes\n\nB")
    if drop_base:
        _drop_bases(store_obj)
    repaired = _write_task(backlog_path, dict(DOC, title="Task 1"))
    _scan(store_obj)

    doc, _body = _entity(store_obj)
    assert (doc["title"], doc["priority"]) == ("Hand title", "low")
    _assert_flag_holds(store_obj, _task_path(backlog_path), "tasks/core-001.md", repaired)

    # Taking the file is an explicit choice: the file's values win whole.
    result = store_obj.resolve_projection_conflict("tasks/core-001.md", "file")
    assert result["took"] == "file"
    doc, _body = _entity(store_obj)
    assert (doc["title"], doc["priority"]) == ("Task 1", "high")
    assert _task_path(backlog_path).read_bytes() == repaired
    assert _flagged(store_obj) == []
    # The store side it replaced is still in the change log.
    befores = [json.loads(row[0]) for row in _sql(
        store_obj,
        "SELECT before FROM changes WHERE id='core-001' AND seq>? ORDER BY seq",
        (result["seq"] - 2,),
    )]
    assert any(before.get("priority") == "low" for before in befores)


@pytest.mark.parametrize("drop_base", [False, True], ids=["based", "no-base"])
def test_p3_a_user_re_edit_is_kept_and_taking_the_store_writes_it(tmp_path, drop_base):
    backlog_path, store_obj = _fresh(tmp_path)
    _put(store_obj, "task", "core-001", DOC, "## Notes\n\nB")
    _write_task(backlog_path, dict(DOC, title="Hand title"))
    _scan(store_obj)
    _quarantine_task_file(backlog_path, "core-001")
    _scan(store_obj)
    _put(store_obj, "task", "core-001", dict(DOC, title="Hand title", priority="low"), "## Notes\n\nB")
    if drop_base:
        _drop_bases(store_obj)
    repaired = _write_task(backlog_path, dict(DOC, title="Second hand title"))
    _scan(store_obj)

    assert _entity(store_obj)[0]["title"] == "Hand title"
    _assert_flag_holds(store_obj, _task_path(backlog_path), "tasks/core-001.md", repaired)
    detail = store_obj.projection_conflict_detail("tasks/core-001.md")
    assert "Second hand title" in detail["file_observed"]
    assert "Second hand title" in detail["file_on_disk"]
    assert "priority: low" in detail["store_version"]

    result = store_obj.resolve_projection_conflict("tasks/core-001.md", "store")
    assert result["took"] == "store"
    on_disk = _task_path(backlog_path).read_text(encoding="utf-8")
    assert "title: Hand title" in on_disk and "priority: low" in on_disk
    assert _flagged(store_obj) == []
    # The file side it replaced is recorded with the resolution.
    before = json.loads(_sql(
        store_obj, "SELECT before FROM changes WHERE seq=?", (result["seq"],)
    )[0][0])
    assert "Second hand title" in before["file"]


# ── p4: backlog.yaml ─────────────────────────────────────────────────────────


def test_p4_a_repaired_backlog_yaml_is_flagged_not_silently_stranded(tmp_path):
    backlog_path, store_obj = _fresh(tmp_path)
    backlog = backlog_path / "backlog.yaml"
    good = backlog.read_text(encoding="utf-8")
    backlog.write_text(
        good + "<<<<<<< HEAD\nfoo: [\n=======\n>>>>>>> other\n", encoding="utf-8"
    )
    _scan(store_obj)
    epic = _entity(store_obj, "epic", "core")[0]
    _put(store_obj, "epic", "core", dict(epic, status="done"))
    doc = yaml.safe_load(good)
    doc["phases"][0]["name"] = "Build (renamed by user)"
    repaired = yaml.safe_dump(doc, sort_keys=False).encode("utf-8")
    backlog.write_bytes(repaired)
    _scan(store_obj)

    assert _entity(store_obj, "epic", "core")[0]["status"] == "done"
    assert _entity(store_obj, "phase", "build")[0]["name"] == "Build"
    assert "backlog.yaml" in _flagged(store_obj)
    assert backlog.read_bytes() == repaired
    phase = _entity(store_obj, "phase", "build")[0]
    warnings = _put(store_obj, "phase", "build", dict(phase, status="done"))
    assert backlog.read_bytes() == repaired
    assert any("backlog.yaml" in warning for warning in warnings)

    store_obj.resolve_projection_conflict("backlog.yaml", "file")
    assert _entity(store_obj, "phase", "build")[0]["name"] == "Build (renamed by user)"
    assert _entity(store_obj, "epic", "core")[0]["status"] == "in-progress"
    assert _flagged(store_obj) == []


def test_p4_taking_the_store_for_backlog_yaml_writes_the_store_index(tmp_path):
    backlog_path, store_obj = _fresh(tmp_path)
    backlog = backlog_path / "backlog.yaml"
    good = backlog.read_text(encoding="utf-8")
    backlog.write_text(good + "<<<<<<< HEAD\n", encoding="utf-8")
    _scan(store_obj)
    epic = _entity(store_obj, "epic", "core")[0]
    _put(store_obj, "epic", "core", dict(epic, status="done"))
    doc = yaml.safe_load(good)
    doc["phases"][0]["name"] = "Renamed"
    backlog.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    _scan(store_obj)
    assert "backlog.yaml" in _flagged(store_obj)

    store_obj.resolve_projection_conflict("backlog.yaml", "store")
    on_disk = yaml.safe_load(backlog.read_text(encoding="utf-8"))
    assert on_disk["epics"][0]["status"] == "done"
    assert on_disk["phases"][0]["name"] == "Build"
    assert _flagged(store_obj) == []


# ── p5: archive never deletes a quarantined file ─────────────────────────────


def test_p5_archiving_a_quarantined_task_keeps_the_broken_file(tmp_path):
    backlog_path, store_obj = _fresh(tmp_path)
    user_work = (
        "---\nid: core-001\ntitle: [unclosed\n---\n"
        "USER WORK IN PROGRESS: long notes the store never imported\n"
    ).encode("utf-8")
    _task_path(backlog_path).write_bytes(user_work)
    _scan(store_obj)
    with store_obj.transaction(tool="archive") as tx:
        tx.archive("task", "core-001")

    assert _task_path(backlog_path).read_bytes() == user_work
    assert not (backlog_path / "tasks" / "archive" / "core-001.md").exists()
    assert _sql(
        store_obj,
        "SELECT file,dirty,quarantined FROM projection WHERE id='core-001'",
    ) == [("tasks/core-001.md", 1, 1)]

    # The repair now meets a store that archived the task: both kept, flagged.
    repaired = _write_task(
        backlog_path, dict(DOC, title="Unclosed fixed"), "USER WORK IN PROGRESS"
    )
    _scan(store_obj)
    _assert_flag_holds(store_obj, _task_path(backlog_path), "tasks/core-001.md", repaired)
    assert not (backlog_path / "tasks" / "archive" / "core-001.md").exists()

    store_obj.resolve_projection_conflict("tasks/core-001.md", "store")
    assert not _task_path(backlog_path).exists()
    assert (backlog_path / "tasks" / "archive" / "core-001.md").exists()


def test_p5_taking_the_file_after_an_archive_keeps_the_users_notes(tmp_path):
    backlog_path, store_obj = _fresh(tmp_path)
    _quarantine_task_file(backlog_path, "core-001")
    _scan(store_obj)
    with store_obj.transaction(tool="archive") as tx:
        tx.archive("task", "core-001")
    _write_task(backlog_path, dict(DOC, title="Kept live"), "Notes only the file has")
    _scan(store_obj)
    assert "tasks/core-001.md" in _flagged(store_obj)

    store_obj.resolve_projection_conflict("tasks/core-001.md", "file")
    doc, body = _entity(store_obj)
    assert doc["title"] == "Kept live" and "Notes only the file has" in body
    assert _task_path(backlog_path).exists()


def test_a_deleted_entity_never_deletes_its_quarantined_file(tmp_path):
    backlog_path, store_obj = _fresh(tmp_path)
    _task_path(backlog_path).write_bytes(BROKEN.encode("utf-8"))
    _scan(store_obj)
    with store_obj.transaction(tool="delete") as tx:
        tx.delete("task", "core-001")
    assert _task_path(backlog_path).read_bytes() == BROKEN.encode("utf-8")


# ── p8 / p8b / p9: epic files ────────────────────────────────────────────────


def _epic_setup(tmp_path):
    backlog_path, store_obj = _fresh(tmp_path)
    epic = _entity(store_obj, "epic", "core")[0]
    _put(store_obj, "epic", "core", dict(epic, description="D0"), "Epic body 0")
    epic_path = backlog_path / "epics" / "core.md"
    return backlog_path, store_obj, epic_path, epic_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("drop_base", [False, True], ids=["based", "no-base"])
def test_p8_an_epic_repair_neither_merges_nor_reverts(tmp_path, drop_base):
    backlog_path, store_obj, epic_path, good = _epic_setup(tmp_path)
    epic_path.write_text(BROKEN, encoding="utf-8")
    _scan(store_obj)
    epic = _entity(store_obj, "epic", "core")[0]
    _put(store_obj, "epic", "core", dict(epic, status="done", description="D-store"), "Epic body 0")
    if drop_base:
        _drop_bases(store_obj)
    repaired = good.replace("Epic body 0", "Epic body USER").replace(
        "---\n", "---\nstatus: todo\n", 1
    ).encode("utf-8")
    epic_path.write_bytes(repaired)
    _scan(store_obj)

    doc, body = _entity(store_obj, "epic", "core")
    assert (doc["status"], doc["description"], body) == ("done", "D-store", "Epic body 0")
    _assert_flag_holds(store_obj, epic_path, "epics/core.md", repaired)

    store_obj.resolve_projection_conflict("epics/core.md", "file")
    doc, body = _entity(store_obj, "epic", "core")
    # The epic file owns its heavy fields and body, not status.
    assert (doc["status"], doc["description"], body) == ("done", "D0", "Epic body USER")


@pytest.mark.parametrize("drop_base", [False, True], ids=["based", "no-base"])
def test_p8b_a_store_title_edit_survives_an_epic_file_repair(tmp_path, drop_base):
    backlog_path, store_obj, epic_path, good = _epic_setup(tmp_path)
    epic_path.write_text(BROKEN, encoding="utf-8")
    _scan(store_obj)
    epic = _entity(store_obj, "epic", "core")[0]
    _put(store_obj, "epic", "core", dict(epic, title="Store epic title"), "Epic body 0")
    if drop_base:
        _drop_bases(store_obj)
    repaired = good.replace("Epic body 0", "Epic body USER").encode("utf-8")
    epic_path.write_bytes(repaired)
    _scan(store_obj)

    doc, body = _entity(store_obj, "epic", "core")
    assert (doc["title"], doc["status"], body) == ("Store epic title", "in-progress", "Epic body 0")
    _assert_flag_holds(store_obj, epic_path, "epics/core.md", repaired)

    store_obj.resolve_projection_conflict("epics/core.md", "file")
    doc, body = _entity(store_obj, "epic", "core")
    assert (doc["title"], body) == ("Store epic title", "Epic body USER")


def test_p9_a_stale_epic_file_does_not_revert_a_backlog_rename(tmp_path):
    backlog_path, store_obj, epic_path, good = _epic_setup(tmp_path)
    backlog = backlog_path / "backlog.yaml"
    doc = yaml.safe_load(backlog.read_text(encoding="utf-8"))
    doc["epics"][0]["name"] = "Core renamed"
    doc["epics"][0]["title"] = "Core renamed"
    backlog.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    _scan(store_obj)
    assert _entity(store_obj, "epic", "core")[0]["title"] == "Core renamed"
    epic_path.write_text(BROKEN, encoding="utf-8")
    _scan(store_obj)
    epic = _entity(store_obj, "epic", "core")[0]
    _put(store_obj, "epic", "core", dict(epic, description="D-store"), "Epic body 0")
    repaired = good.replace("Epic body 0", "Epic body USER").encode("utf-8")
    epic_path.write_bytes(repaired)
    _scan(store_obj)

    doc, body = _entity(store_obj, "epic", "core")
    assert (doc["title"], doc["description"], body) == ("Core renamed", "D-store", "Epic body 0")
    assert yaml.safe_load(backlog.read_text(encoding="utf-8"))["epics"][0]["title"] == "Core renamed"
    _assert_flag_holds(store_obj, epic_path, "epics/core.md", repaired)

    store_obj.resolve_projection_conflict("epics/core.md", "file")
    doc, body = _entity(store_obj, "epic", "core")
    assert (doc["title"], doc["description"], body) == ("Core renamed", "D0", "Epic body USER")


# ── project.yaml ─────────────────────────────────────────────────────────────


def test_project_yaml_repair_is_flagged_and_resolvable(tmp_path):
    backlog_path, store_obj = _fresh(tmp_path)
    project = backlog_path / "project.yaml"
    project.write_text(yaml.safe_dump({"name": "Demo", "conventions": {"a": 1}}), encoding="utf-8")
    _scan(store_obj)
    assert _entity(store_obj, "project", "__project__")[0]["name"] == "Demo"
    project.write_text("name: [broken\n", encoding="utf-8")
    _scan(store_obj)
    _put(store_obj, "project", "__project__", {"name": "Store name", "conventions": {"a": 1}})
    repaired = yaml.safe_dump({"name": "Demo", "conventions": {"a": 2}}).encode("utf-8")
    project.write_bytes(repaired)
    _scan(store_obj)

    assert _entity(store_obj, "project", "__project__")[0] == {
        "name": "Store name", "conventions": {"a": 1}
    }
    _assert_flag_holds(store_obj, project, "project.yaml", repaired)

    store_obj.resolve_projection_conflict("project.yaml", "store")
    assert yaml.safe_load(project.read_text(encoding="utf-8"))["name"] == "Store name"
    assert _flagged(store_obj) == []


# ── lifecycle of a flag ──────────────────────────────────────────────────────


def _flag_core_001(tmp_path):
    backlog_path, store_obj = _fresh(tmp_path)
    _quarantine_task_file(backlog_path, "core-001")
    _scan(store_obj)
    _put(store_obj, "task", "core-001", dict(DOC, title="Store title"), "## Notes\n\nB")
    repaired = _write_task(backlog_path, dict(DOC, title="File title"))
    _scan(store_obj)
    assert _flagged(store_obj) == ["tasks/core-001.md"]
    return backlog_path, store_obj, repaired


def test_a_repair_that_matches_the_store_is_not_flagged(tmp_path):
    backlog_path, store_obj = _fresh(tmp_path)
    _quarantine_task_file(backlog_path, "core-001")
    _scan(store_obj)
    _put(store_obj, "task", "core-001", dict(DOC, title="Store title"), "## Notes\n\nB")
    _write_task(backlog_path, dict(DOC, title="Store title"))
    _scan(store_obj)
    assert _flagged(store_obj) == []
    assert _sql(store_obj, "SELECT dirty,quarantined FROM projection WHERE file='tasks/core-001.md'") == [(0, 0)]


def test_a_repair_with_nothing_pending_imports_as_before(tmp_path):
    backlog_path, store_obj = _fresh(tmp_path)
    _quarantine_task_file(backlog_path, "core-001")
    _scan(store_obj)
    repaired = _write_task(backlog_path, dict(DOC, title="Only the file changed"))
    _scan(store_obj)
    assert _flagged(store_obj) == []
    assert _entity(store_obj)[0]["title"] == "Only the file changed"
    assert _task_path(backlog_path).read_bytes() == repaired


def test_editing_a_flagged_file_to_match_the_store_clears_the_flag(tmp_path):
    backlog_path, store_obj, _repaired = _flag_core_001(tmp_path)
    _write_task(backlog_path, dict(DOC, title="Store title"))
    _scan(store_obj)
    assert _flagged(store_obj) == []


def test_editing_a_flagged_file_again_keeps_it_flagged_and_unimported(tmp_path):
    backlog_path, store_obj, _repaired = _flag_core_001(tmp_path)
    again = _write_task(backlog_path, dict(DOC, title="File title, second go"))
    _scan(store_obj)
    assert _entity(store_obj)[0]["title"] == "Store title"
    _assert_flag_holds(store_obj, _task_path(backlog_path), "tasks/core-001.md", again)
    assert "second go" in store_obj.projection_conflict_detail("tasks/core-001.md")["file_observed"]


def test_breaking_a_flagged_file_again_keeps_the_flag_and_the_bytes(tmp_path):
    backlog_path, store_obj, repaired = _flag_core_001(tmp_path)
    _task_path(backlog_path).write_text(BROKEN, encoding="utf-8")
    _scan(store_obj)
    _put(store_obj, "task", "core-001", dict(DOC, title="Store title 2"), "## Notes\n\nB")
    assert _task_path(backlog_path).read_text(encoding="utf-8") == BROKEN
    assert "tasks/core-001.md" in _flagged(store_obj)
    assert "File title" in store_obj.projection_conflict_detail("tasks/core-001.md")["file_observed"]
    with pytest.raises(ValueError):
        store_obj.resolve_projection_conflict("tasks/core-001.md", "file")


def test_a_deleted_flagged_file_is_not_recreated_until_resolved(tmp_path):
    backlog_path, store_obj, _repaired = _flag_core_001(tmp_path)
    _task_path(backlog_path).unlink()
    _scan(store_obj)
    _put(store_obj, "task", "core-001", dict(DOC, title="Store title 3"), "## Notes\n\nB")
    assert not _task_path(backlog_path).exists()
    assert "File title" in store_obj.projection_conflict_detail("tasks/core-001.md")["file_observed"]
    store_obj.resolve_projection_conflict("tasks/core-001.md", "store")
    assert "Store title 3" in _task_path(backlog_path).read_text(encoding="utf-8")


def test_a_flag_survives_a_restart_and_is_seen_by_another_process(tmp_path):
    _backlog_path, store_obj, _repaired = _flag_core_001(tmp_path)
    other = store_mod.Store(store_obj.resolution, session="another-process")
    assert [c["file"] for c in other.projection_conflicts()] == ["tasks/core-001.md"]
    status = store_mod.read_only_status(_backlog_path)
    assert status.flagged_files == ("tasks/core-001.md",)


def test_resolving_an_unflagged_file_is_refused(tmp_path):
    _backlog_path, store_obj = _fresh(tmp_path)
    with pytest.raises(ValueError):
        store_obj.resolve_projection_conflict("tasks/core-001.md", "file")
    _backlog_path, store_obj, _repaired = _flag_core_001(tmp_path / "second")
    with pytest.raises(ValueError):
        store_obj.resolve_projection_conflict("tasks/core-001.md", "both")


# ── notices ──────────────────────────────────────────────────────────────────


def test_the_notice_lookup_is_one_bounded_query_that_never_reads_history(tmp_path):
    _backlog_path, store_obj, _repaired = _flag_core_001(tmp_path)
    statements: list[str] = []
    connection = store_obj.connection
    connection.set_trace_callback(statements.append)
    try:
        conflicts = store_obj.projection_conflicts()
    finally:
        connection.set_trace_callback(None)
    assert [c["file"] for c in conflicts] == ["tasks/core-001.md"]
    assert len(statements) == 1, statements
    assert "changes" not in statements[0]
    assert "projection_conflict" in statements[0]


def test_the_notice_names_the_entity_the_file_and_how_to_resolve(tmp_path):
    _backlog_path, store_obj, _repaired = _flag_core_001(tmp_path)
    (conflict,) = store_obj.projection_conflicts()
    notice = store_mod.projection_conflict_notice(conflict)
    assert "tasks/core-001.md" in notice
    assert "task core-001" in notice
    assert "backlog_resolve_conflict" in notice
    assert 'take="file"' in notice and 'take="store"' in notice


@pytest.mark.allow_projection_bypass
def test_every_tool_result_names_the_flag_until_it_is_resolved(tm_epic_phase):
    from taskmaster import backlog_server

    backlog_server.backlog_add_task(
        epic="test-epic", title="Hand repaired", notes="Base notes.", phase="dev",
        options={"task_id": "T-CF"},
    )
    path = tm_epic_phase / ".taskmaster" / "tasks" / "T-CF.md"
    good = path.read_text(encoding="utf-8")
    path.write_text(BROKEN, encoding="utf-8")
    backlog_server._store()._last_read_scan_clock = None
    backlog_server.backlog_get_task("T-CF")
    backlog_server.backlog_update_task("T-CF", "notes", "Store notes.")
    repaired = good.replace("Base notes.", "User notes.")
    path.write_text(repaired, encoding="utf-8")
    backlog_server._store()._last_read_scan_clock = None

    first = backlog_server.backlog_get_task("T-CF")
    second = backlog_server.backlog_get_task("T-CF")
    for result in (first, second):
        assert "tasks/T-CF.md" in result
        assert "backlog_resolve_conflict" in result
    assert path.read_text(encoding="utf-8") == repaired

    shown = backlog_server.backlog_resolve_conflict(file="tasks/T-CF.md")
    assert "User notes." in shown and "Store notes." in shown

    resolved = backlog_server.backlog_resolve_conflict(file="tasks/T-CF.md", take="file")
    assert "[seq" in resolved
    after = backlog_server.backlog_get_task("T-CF")
    assert "backlog_resolve_conflict" not in after
    assert "User notes." in after
