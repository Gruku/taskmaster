"""User intent: a batch is one transaction, so a later line in it must never
undo an earlier line's work. Archiving an epic cascades onto its tasks, and
those cascaded changes have to reach the rows before the next operation reads
them back.
"""
from __future__ import annotations

from pathlib import Path

from taskmaster import backlog_server as bs
from taskmaster import store


def _bp(root: Path) -> Path:
    return root / ".taskmaster" / "backlog.yaml"


def _committed_task(root: Path, task_id: str) -> dict:
    store.reset_for_tests()
    with store.transaction(backlog_path=_bp(root), tool="test-read") as tx:
        return dict(tx.get("task", task_id))


def test_a_later_batch_line_does_not_undo_an_epic_cascade(tm_epic_phase):
    """`_tx_task` refreshes the dict node from the row, so a cascade that only
    archived the row left the next operation reloading the pre-cascade
    document: archived with its original status and lock, and no reason."""
    root = tm_epic_phase
    added = bs.backlog_add_task(title="Child", epic="test-epic", phase="dev", tldr="c")
    assert "Error" not in added, added
    bs.backlog_update_task(task_id="test-epic-001", field="locked_by", value="agent-a")

    result = bs.backlog_batch_update(
        operations="update_epic test-epic status archived\nupdate test-epic-001 title New"
    )
    assert "Error" not in result, result

    task = _committed_task(root, "test-epic-001")
    assert task["title"] == "New"
    assert task["status"] == "archived"
    assert task.get("archive_reason")
    assert "locked_by" not in task
