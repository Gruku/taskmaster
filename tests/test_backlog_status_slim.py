# plugins/taskmaster/tests/test_backlog_status_slim.py
"""Task 16: backlog_status slim default + verbose for archived/completed."""
from __future__ import annotations

from taskmaster.backlog_server import (
    backlog_add_epic,
    backlog_add_task,
    backlog_archive_task,
    backlog_status,
    backlog_update_task,
    _load,
    _find_task,
    _mutate_and_save,
)


def _make_done(task_id: str) -> None:
    """Set a task to done so archive_task accepts it.

    Tasks created via backlog_add_task are lane'd, so a direct todo->done would
    be blocked by the Spec A transition table + done-gate. These tests only
    care about the dashboard rendering of a done/archived task, not the status
    mechanics — so set the terminal status via the data layer (sanctioned
    SETUP bypass) rather than routing through gates.
    """
    data = _load()
    task, _ = _find_task(data, task_id)
    task["status"] = "done"
    _mutate_and_save(data)


def test_slim_status_omits_archived_section(tm_epic_phase):
    backlog_add_task(epic="test-epic", title="X", tldr="T.", phase="dev", options={"task_id": "T-1"})
    _make_done("T-1")
    backlog_archive_task("T-1", reason="done")
    out = backlog_status()
    assert "T-1" not in out
    # The "In Progress" section header is always present in the dashboard
    assert "in progress" in out.lower() or "in-progress" in out.lower()


def test_verbose_status_includes_archived(tm_epic_phase):
    backlog_add_task(epic="test-epic", title="X", tldr="T.", phase="dev", options={"task_id": "T-2"})
    _make_done("T-2")
    backlog_archive_task("T-2", reason="done")
    out = backlog_status(verbose=True)
    assert "archived" in out.lower()


def test_slim_status_under_1800_chars(tm_epic_phase):
    # Add a realistic-ish set (5 epics x 5 tasks each)
    for ei in range(5):
        backlog_add_epic(epic_id=f"e{ei}", name=f"E{ei}", done_when="done")
    for ei in range(5):
        for ti in range(5):
            backlog_add_task(epic=f"e{ei}", title=f"task {ei}.{ti}", tldr=f"tldr {ei}.{ti}", phase="dev", options={"task_id": f"T-{ei}-{ti}"})
    out = backlog_status()
    assert len(out) < 1800, f"slim status too large: {len(out)} chars"


def test_slim_status_omits_archived_count_from_stats(tm_epic_phase):
    """In slim mode, 'Archived:' count must not appear in the stats line."""
    backlog_add_task(epic="test-epic", title="Z", tldr="T.", phase="dev", options={"task_id": "T-3"})
    _make_done("T-3")
    backlog_archive_task("T-3", reason="done")
    out = backlog_status()
    assert "Archived:" not in out


def test_verbose_status_archived_count_in_stats(tm_epic_phase):
    """In verbose mode, 'Archived:' count appears in the stats or archived section."""
    backlog_add_task(epic="test-epic", title="Z", tldr="T.", phase="dev", options={"task_id": "T-4"})
    _make_done("T-4")
    backlog_archive_task("T-4", reason="done")
    out = backlog_status(verbose=True)
    assert "Archived:" in out or "archived" in out.lower()


def test_status_table_marks_closeable_epic(tm_epic_phase):
    backlog_add_task(epic="test-epic", title="Z", tldr="T.", phase="dev", options={"task_id": "T-5"})
    _make_done("T-5")
    out = backlog_status()
    assert "[closeable]" in out


def test_status_table_no_marker_with_open_tasks(tm_epic_phase):
    backlog_add_task(epic="test-epic", title="Z", tldr="T.", phase="dev", options={"task_id": "T-6"})
    out = backlog_status()
    assert "[closeable]" not in out
