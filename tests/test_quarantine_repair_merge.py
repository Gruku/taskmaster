"""User intent: a hand repair of a quarantined file must never be silently
reverted by the edits the store held while the file was broken (6.0.3 review of
B-089). Only a field both sides changed is a conflict, and the caller is told
about it in the tool result; a store with no recoverable base keeps both
versions and says so rather than guessing.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from taskmaster import store as store_mod
from taskmaster.taskmaster_v3 import render_frontmatter

from test_store_bug_cluster import _build_projection, _quarantine_task_file


_BASE_DOC = {
    "id": "core-001",
    "title": "Task 1",
    "status": "todo",
    "epic": "core",
    "order": 1.0,
    "priority": "high",
}


def _task_path(backlog_path: Path) -> Path:
    return backlog_path / "tasks" / "core-001.md"


def _read_task(store_obj) -> dict:
    store_obj._last_read_scan_clock = None
    data = store_obj.load_dict()
    return next(
        task
        for epic in data["epics"]
        for task in epic.get("tasks", [])
        if task["id"] == "core-001"
    )


def _store_edit_while_quarantined(tmp_path: Path):
    """Quarantine core-001, then change its title and body in the store only."""
    backlog_path = _build_projection(tmp_path)
    _task_path(backlog_path).write_text(
        render_frontmatter(dict(_BASE_DOC), "## Notes\n\nBase body."), encoding="utf-8"
    )
    store_obj = store_mod.open_store(backlog_path=backlog_path)
    store_obj.load_dict()
    _quarantine_task_file(backlog_path, "core-001")
    store_obj._last_read_scan_clock = None
    store_obj.load_dict()
    with store_obj.transaction(tool="test-write") as tx:
        tx.put(
            "task",
            "core-001",
            dict(_BASE_DOC, title="Store title"),
            body="## Notes\n\nStore body.",
        )
    return backlog_path, store_obj


def _user_repair(backlog_path: Path) -> None:
    """Fix the YAML from the last good copy: new status, new body, no priority."""
    repaired = {k: v for k, v in _BASE_DOC.items() if k != "priority"}
    repaired["status"] = "in-progress"
    _task_path(backlog_path).write_text(
        render_frontmatter(repaired, "## Notes\n\nUser body."), encoding="utf-8"
    )


def _merge_changes(store_obj) -> list[dict]:
    connection = sqlite3.connect(store_obj.db_path)
    try:
        rows = connection.execute(
            "SELECT before FROM changes WHERE op='merge' AND id='core-001' ORDER BY seq"
        ).fetchall()
    finally:
        connection.close()
    return [json.loads(row[0]) for row in rows]


def _drop_base(store_obj) -> None:
    """What a 6.0.2 store holds: the dirty, quarantined row has no base."""
    connection = sqlite3.connect(store_obj.db_path)
    try:
        connection.execute("DELETE FROM projection_base")
        connection.commit()
    finally:
        connection.close()


def _assert_three_way_outcome(backlog_path: Path, store_obj) -> None:
    task = _read_task(store_obj)
    # Only the user changed status: the repair survives.
    assert task["status"] == "in-progress"
    # Only the store changed title: the store's edit survives.
    assert task["title"] == "Store title"
    # Only the user removed priority: it stays removed.
    assert "priority" not in task
    # Both changed the body: a real conflict, recorded with both sides.
    merges = _merge_changes(store_obj)
    assert merges, "the repair was never merged"
    conflicts = merges[-1].get("_conflicts") or {}
    assert set(conflicts) == {store_mod.BODY_KEY}
    sides = conflicts[store_mod.BODY_KEY]
    assert "Store body." in sides["ours"]["value"]
    assert "User body." in sides["theirs"]["value"]
    on_disk = _task_path(backlog_path).read_text(encoding="utf-8")
    assert "status: in-progress" in on_disk
    assert "Store title" in on_disk
    assert "priority" not in on_disk


def test_a_repair_keeps_user_fields_and_store_fields_and_flags_only_real_conflicts(
    tmp_path,
):
    backlog_path, store_obj = _store_edit_while_quarantined(tmp_path)
    _user_repair(backlog_path)
    _assert_three_way_outcome(backlog_path, store_obj)


def test_a_6_0_2_row_with_no_base_row_reconstructs_the_base_from_history(tmp_path):
    backlog_path, store_obj = _store_edit_while_quarantined(tmp_path)
    _drop_base(store_obj)
    _user_repair(backlog_path)
    _assert_three_way_outcome(backlog_path, store_obj)


def test_no_recoverable_base_keeps_both_versions_and_does_not_let_the_store_win(
    tmp_path,
):
    backlog_path, store_obj = _store_edit_while_quarantined(tmp_path)
    _drop_base(store_obj)
    # No history to rebuild the base from either.
    connection = sqlite3.connect(store_obj.db_path)
    try:
        connection.execute(
            "UPDATE projection SET exported_seq=NULL WHERE file='tasks/core-001.md'"
        )
        connection.execute(
            "DELETE FROM changes WHERE id='core-001' AND op IN ('import','create')"
        )
        connection.commit()
    finally:
        connection.close()
    _user_repair(backlog_path)
    task = _read_task(store_obj)

    # The user's repair is what the entity now holds and what stays on disk.
    assert task["status"] == "in-progress"
    assert task["title"] == "Task 1"
    assert "priority" not in task
    on_disk = _task_path(backlog_path).read_text(encoding="utf-8")
    assert "status: in-progress" in on_disk and "User body." in on_disk
    # The store's version is still retrievable, whole, from the merge record.
    merges = _merge_changes(store_obj)
    assert merges
    held = merges[-1]["_conflicts"]
    assert held["title"]["ours"]["value"] == "Store title"
    assert "Store body." in held[store_mod.BODY_KEY]["ours"]["value"]
    # ...and the caller is told.
    notices = store_obj.take_merge_notices()
    assert any("tasks/core-001.md" in notice for notice in notices)


def test_a_real_conflict_is_reported_once_to_the_caller(tmp_path):
    backlog_path, store_obj = _store_edit_while_quarantined(tmp_path)
    _user_repair(backlog_path)
    _read_task(store_obj)
    notices = store_obj.take_merge_notices()
    assert len(notices) == 1
    assert "tasks/core-001.md" in notices[0]
    assert store_mod.BODY_KEY in notices[0] or "body" in notices[0]
    assert store_obj.take_merge_notices() == []


@pytest.mark.allow_projection_bypass
def test_the_tool_result_carries_the_conflict(tm_epic_phase):
    from taskmaster import backlog_server

    backlog_server.backlog_add_task(
        epic="test-epic", title="Hand repaired", notes="Base notes.", phase="dev",
        options={"task_id": "T-CF"},
    )
    path = tm_epic_phase / ".taskmaster" / "tasks" / "T-CF.md"
    good = path.read_text(encoding="utf-8")
    path.write_text("---\n: : not yaml [\n---\nbroken\n", encoding="utf-8")
    backlog_server._store()._last_read_scan_clock = None
    backlog_server.backlog_get_task("T-CF")
    backlog_server.backlog_update_task("T-CF", "notes", "Store notes.")
    path.write_text(good.replace("Base notes.", "User notes."), encoding="utf-8")
    backlog_server._store()._last_read_scan_clock = None

    result = backlog_server.backlog_get_task("T-CF")

    assert "merge conflict" in result.lower()
    assert "T-CF.md" in result
