"""User intent: pin `backlog_batch_update`'s per-line partial-apply contract —
a bad line reports an error and the good lines still commit. The native core's
structured batch is all-or-nothing by design, so an adapter that forwards a
whole line-set into it would silently discard the good lines; without this test
that regression is invisible.
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


def test_a_bad_batch_line_is_reported_while_the_good_lines_still_commit(tm_epic_phase):
    root = tm_epic_phase
    added = bs.backlog_add_task(title="Child", epic="test-epic", phase="dev", tldr="c")
    assert "Error" not in added, added

    result = bs.backlog_batch_update(
        operations="update test-epic-001 title Renamed\nupdate test-epic-404 title Ghost"
    )

    assert "1 applied" in result, result
    assert "1 errors" in result, result
    assert "test-epic-404" in result, result
    assert _committed_task(root, "test-epic-001")["title"] == "Renamed", (
        "the good line was discarded along with the bad one"
    )


def test_an_unknown_field_refuses_its_own_line_only(tm_epic_phase):
    root = tm_epic_phase
    assert "Error" not in bs.backlog_add_task(title="Child", epic="test-epic", phase="dev", tldr="c")

    result = bs.backlog_batch_update(
        operations="update test-epic-001 not_a_field x\nupdate test-epic-001 title Kept"
    )

    assert "1 applied" in result, result
    assert "1 errors" in result, result
    assert "not allowed" in result, result
    assert _committed_task(root, "test-epic-001")["title"] == "Kept"


def test_every_line_failing_reports_errors_and_commits_nothing(tm_epic_phase):
    root = tm_epic_phase
    assert "Error" not in bs.backlog_add_task(title="Child", epic="test-epic", phase="dev", tldr="c")
    before = _committed_task(root, "test-epic-001")["title"]

    result = bs.backlog_batch_update(
        operations="update test-epic-404 title Ghost\nupdate test-epic-405 title Ghost"
    )

    assert "0 applied" in result, result
    assert "2 errors" in result, result
    assert _committed_task(root, "test-epic-001")["title"] == before
