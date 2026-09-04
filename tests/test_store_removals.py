"""User intent: every intended removal of a task/epic/phase must be an explicit
store operation. The compatibility write-back ignores entities that vanish from
the dict, so an archive/delete/move that only edits the dict silently stops
working. These tests pin each removal site to committed store state and to the
projection files on disk.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store


def _bp(root: Path) -> Path:
    return root / ".taskmaster" / "backlog.yaml"


def _fresh_row(root: Path, kind: str, ident: str) -> dict:
    """Reopen the store from scratch and read one committed entity row."""
    store.reset_for_tests()
    instance = store.open_store(_bp(root))
    row = instance.connection.execute(
        "SELECT kind,id,archived,deleted,doc FROM entities WHERE kind=? AND id=?",
        (kind, ident),
    ).fetchone()
    assert row is not None, f"{kind} {ident} has no row in committed state"
    return dict(row)


def _ops(root: Path, kind: str, ident: str) -> list[str]:
    """Every change-log op recorded against one entity, oldest first."""
    store.reset_for_tests()
    instance = store.open_store(_bp(root))
    return [
        r[0]
        for r in instance.connection.execute(
            "SELECT op FROM changes WHERE kind=? AND id=? ORDER BY seq", (kind, ident)
        )
    ]


def _committed_dict(root: Path) -> dict:
    store.reset_for_tests()
    return store.load_dict(_bp(root))


def _committed_task(root: Path, task_id: str) -> dict:
    for epic in _committed_dict(root).get("epics", []):
        for task in epic.get("tasks", []):
            if task.get("id") == task_id:
                return task
    raise AssertionError(f"task {task_id} not found in committed state")


def _live_task_file(root: Path, task_id: str) -> Path:
    return root / ".taskmaster" / "tasks" / f"{task_id}.md"


def _archived_task_file(root: Path, task_id: str) -> Path:
    return root / ".taskmaster" / "tasks" / "archive" / f"{task_id}.md"


@pytest.fixture()
def one_task(tm_epic_phase):
    root = tm_epic_phase
    created = bs.backlog_add_task(
        title="Archivable", epic="test-epic", phase="dev", tldr="a task to archive"
    )
    assert "Error" not in created, created
    return root, "test-epic-001"


# ── backlog_archive_task ─────────────────────────────────


def test_archive_task_marks_the_store_row_archived(one_task):
    root, task_id = one_task
    result = bs.backlog_archive_task(task_id, reason="deprecated")
    assert "Error" not in result, result
    row = _fresh_row(root, "task", task_id)
    assert row["archived"] == 1, "archive_task left the store row unarchived"
    assert row["deleted"] == 0, "archive must not tombstone the row"


def test_archive_task_records_an_explicit_archive_operation(one_task):
    """The archive must be `tx.archive`, not a side effect of a field write."""
    root, task_id = one_task
    bs.backlog_archive_task(task_id, reason="deprecated")
    assert "archive" in _ops(root, "task", task_id), (
        "no explicit archive op recorded — the row was archived implicitly"
    )


def test_archive_task_moves_the_projection_file_into_the_archive_dir(one_task):
    root, task_id = one_task
    assert _live_task_file(root, task_id).exists()
    bs.backlog_archive_task(task_id, reason="deprecated")
    assert _archived_task_file(root, task_id).exists(), (
        "archived task file did not land in tasks/archive/"
    )
    assert not _live_task_file(root, task_id).exists(), (
        "archived task file is still in the live tasks/ dir"
    )


def test_archive_task_keeps_the_task_in_committed_state(one_task):
    root, task_id = one_task
    bs.backlog_archive_task(task_id, reason="deprecated")
    task = _committed_task(root, task_id)
    assert task["status"] == "archived"
    assert task["archive_reason"] == "deprecated"


# ── backlog_archive_epic ─────────────────────────────────


def test_archive_epic_archives_the_epic_row(tm_epic_phase):
    root = tm_epic_phase
    bs.backlog_add_task(title="One", epic="test-epic", phase="dev", tldr="one")
    result = bs.backlog_archive_epic("test-epic", reason="deprecated")
    assert "Error" not in result, result
    row = _fresh_row(root, "epic", "test-epic")
    assert row["archived"] == 1, "archive_epic left the epic row unarchived"
    assert "archive" in _ops(root, "epic", "test-epic"), (
        "no explicit archive op recorded for the epic"
    )


def test_archive_epic_cascades_the_archive_to_every_task_row(tm_epic_phase):
    root = tm_epic_phase
    bs.backlog_add_task(title="One", epic="test-epic", phase="dev", tldr="one")
    bs.backlog_add_task(title="Two", epic="test-epic", phase="dev", tldr="two")
    bs.backlog_archive_epic("test-epic", reason="deprecated")
    for task_id in ("test-epic-001", "test-epic-002"):
        row = _fresh_row(root, "task", task_id)
        assert row["archived"] == 1, f"{task_id} was not archived with its epic"
        assert "archive" in _ops(root, "task", task_id), (
            f"{task_id} was archived implicitly, not through tx.archive"
        )
        assert _archived_task_file(root, task_id).exists(), (
            f"{task_id} file did not move into tasks/archive/"
        )
        assert not _live_task_file(root, task_id).exists()


def test_archive_epic_leaves_an_already_archived_task_alone(tm_epic_phase):
    root = tm_epic_phase
    bs.backlog_add_task(title="One", epic="test-epic", phase="dev", tldr="one")
    bs.backlog_archive_task("test-epic-001", reason="duplicate")
    result = bs.backlog_archive_epic("test-epic", reason="deprecated")
    assert "0 tasks cascaded" in result, result
    assert _committed_task(root, "test-epic-001")["archive_reason"] == "duplicate"


# ── status-driven archive on the update paths ────────────


def test_update_task_status_archived_archives_the_store_row(one_task):
    root, task_id = one_task
    result = bs.backlog_update_task(task_id, "status", "archived")
    assert "Error" not in result, result
    row = _fresh_row(root, "task", task_id)
    assert row["archived"] == 1, (
        "update_task(status=archived) left the store row unarchived"
    )
    assert _archived_task_file(root, task_id).exists()
    assert not _live_task_file(root, task_id).exists()


def test_batch_update_archive_op_archives_the_store_row(one_task):
    root, task_id = one_task
    result = bs.backlog_batch_update(f"archive {task_id} deprecated")
    assert "not found" not in result, result
    row = _fresh_row(root, "task", task_id)
    assert row["archived"] == 1, "batch archive left the store row unarchived"
    assert _archived_task_file(root, task_id).exists()


def test_batch_update_status_archived_archives_the_store_row(one_task):
    root, task_id = one_task
    bs.backlog_batch_update(f"status {task_id} archived")
    row = _fresh_row(root, "task", task_id)
    assert row["archived"] == 1, "batch status=archived left the store row unarchived"
    assert _archived_task_file(root, task_id).exists()


def test_unarchiving_a_task_returns_its_file_to_the_live_dir(one_task):
    root, task_id = one_task
    bs.backlog_archive_task(task_id, reason="deprecated")
    assert _archived_task_file(root, task_id).exists()
    result = bs.backlog_update_task(task_id, "status", "todo")
    assert "Error" not in result, result
    row = _fresh_row(root, "task", task_id)
    assert row["archived"] == 0, "un-archiving left the store row archived"
    assert _live_task_file(root, task_id).exists(), (
        "un-archived task file did not return to tasks/"
    )
    assert not _archived_task_file(root, task_id).exists()
    assert _committed_task(root, task_id)["status"] == "todo"


def test_batch_update_unarchiving_a_task_clears_the_archive_flag(one_task):
    root, task_id = one_task
    bs.backlog_archive_task(task_id, reason="deprecated")
    bs.backlog_batch_update(f"status {task_id} todo")
    row = _fresh_row(root, "task", task_id)
    assert row["archived"] == 0, "batch un-archive left the store row archived"
    assert _live_task_file(root, task_id).exists()


def test_advance_phase_archives_the_done_tasks_it_sweeps(tm_epic_phase):
    root = tm_epic_phase
    bs.backlog_add_task(title="Done one", epic="test-epic", phase="dev", tldr="done")
    bs.backlog_add_phase(phase_id="next", name="Next")
    bs.backlog_update_phase("dev", "status", "active")
    # The review gates are not what this test is about.
    for gate in ("design-review", "review-gate"):
        bs.backlog_skip_gate("test-epic-001", gate, reason="not under test")
    bs.backlog_pick_task("test-epic-001")
    done = bs.backlog_complete_task("test-epic-001")
    assert "Error" not in done and "Cannot complete" not in done, done

    result = bs.backlog_advance_phase()
    assert "Error" not in result, result
    row = _fresh_row(root, "task", "test-epic-001")
    assert row["archived"] == 1, "advance_phase archived the task only in the dict"
    assert "archive" in _ops(root, "task", "test-epic-001")
    assert _archived_task_file(root, "test-epic-001").exists()
    assert not _live_task_file(root, "test-epic-001").exists()


def test_batch_pick_of_an_archived_task_returns_it_to_the_live_dir(one_task):
    root, task_id = one_task
    bs.backlog_archive_task(task_id, reason="deprecated")
    bs.backlog_batch_update(f"pick {task_id}")
    row = _fresh_row(root, "task", task_id)
    assert row["archived"] == 0, "batch pick left an archived row behind"
    assert _live_task_file(root, task_id).exists()
    assert _committed_task(root, task_id)["status"] == "in-progress"


# ── moves and dict-level removal ─────────────────────────


def test_moving_a_task_between_epics_never_deletes_it(tm_epic_phase):
    """A move is a `put` with a new epic — never a delete followed by a create."""
    root = tm_epic_phase
    bs.backlog_add_epic(epic_id="other-epic", name="Other", done_when="never")
    bs.backlog_add_task(title="Mover", epic="test-epic", phase="dev", tldr="moves")

    with bs._transaction(tool="test-move") as data:
        source = next(e for e in data["epics"] if e["id"] == "test-epic")
        target = next(e for e in data["epics"] if e["id"] == "other-epic")
        task = next(t for t in source["tasks"] if t["id"] == "test-epic-001")
        source["tasks"].remove(task)
        task["epic"] = "other-epic"
        target.setdefault("tasks", []).append(task)
        bs._mutate_and_save(data)

    row = _fresh_row(root, "task", "test-epic-001")
    assert row["deleted"] == 0, "a move tombstoned the task"
    moved = _committed_task(root, "test-epic-001")
    assert moved["epic"] == "other-epic"
    committed = _committed_dict(root)
    holder = next(
        e for e in committed["epics"] if any(t["id"] == "test-epic-001" for t in e["tasks"])
    )
    assert holder["id"] == "other-epic", "the task did not land under the target epic"


def test_dropping_a_task_from_the_dict_does_not_remove_it(one_task):
    """The compatibility write-back ignores entities missing from the dict.

    This is the contract that forces every real removal to be an explicit store
    operation. If this ever starts deleting, the explicit conversions above are
    no longer the only removal path and the audit has to be redone.
    """
    root, task_id = one_task
    with bs._transaction(tool="test-drop") as data:
        epic = next(e for e in data["epics"] if e["id"] == "test-epic")
        epic["tasks"] = [t for t in epic["tasks"] if t["id"] != task_id]
        bs._mutate_and_save(data)
    row = _fresh_row(root, "task", task_id)
    assert row["deleted"] == 0
    assert row["archived"] == 0
    assert _live_task_file(root, task_id).exists()


def test_a_missing_projection_file_never_deletes_a_row(one_task):
    """Spec constraint: only tx.archive/tx.delete may remove an entity.

    The viewer's `taskmaster_v3.save_v4` still removes task files behind the
    store's back (spec step 3 / task 2.3). Whatever it deletes, the row survives.
    """
    root, task_id = one_task
    _live_task_file(root, task_id).unlink()
    bs.backlog_update_task("test-epic-001", "priority", "high")
    row = _fresh_row(root, "task", task_id)
    assert row["deleted"] == 0, "a missing projection file deleted the store row"
    assert _committed_task(root, task_id)["priority"] == "high"


# ── layout migration tools ───────────────────────────────


def test_canonicalize_layout_is_a_noop_once_the_store_owns_the_projection(one_task):
    root, task_id = one_task
    result = bs.backlog_canonicalize_layout()
    assert "no changes" in result.lower(), result
    assert _fresh_row(root, "task", task_id)["deleted"] == 0


def test_migrate_v4_does_not_drop_store_rows(one_task):
    root, task_id = one_task
    result = bs.backlog_migrate_v4()
    assert "Already on v4" in result, result
    assert _fresh_row(root, "task", task_id)["deleted"] == 0
    assert _committed_task(root, task_id)["title"] == "Archivable"


def test_migrate_v3_does_not_drop_store_rows(one_task):
    root, task_id = one_task
    result = bs.backlog_migrate_v3()
    assert "Already on v3" in result, result
    assert _fresh_row(root, "task", task_id)["deleted"] == 0


# ── phases ───────────────────────────────────────────────


def test_archiving_a_phase_keeps_it_in_committed_state(tm_epic_phase):
    root = tm_epic_phase
    result = bs.backlog_update_phase("dev", "status", "archived")
    assert "Error" not in result, result
    committed = _committed_dict(root)
    phase = next(p for p in committed["phases"] if p["id"] == "dev")
    assert phase["status"] == "archived"
    assert _fresh_row(root, "phase", "dev")["deleted"] == 0
