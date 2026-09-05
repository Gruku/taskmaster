"""User intent: a handover's narrative body is the whole point of the handover,
so no maintenance pass may erase it. These tests pin the three planners that
rewrite handover frontmatter in bulk — status backfill, thread backfill and
smart auto-close — to carrying the existing body through to `tx.put`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from tests.entity_helpers import transaction


def _bp(root: Path) -> Path:
    return root / ".taskmaster" / "backlog.yaml"


BODY = "## What happened\n\nThe long narrative nobody wants to lose.\n"


def _committed_body(root: Path, handover_id: str) -> str | None:
    """Read a handover body straight out of a freshly opened store."""
    store.reset_for_tests()
    with transaction(_bp(root)) as tx:
        for hid, _doc, body in tx.list("handover", include_archived=True):
            if hid == handover_id:
                return body
    raise AssertionError(f"handover {handover_id} not in committed state")


def _make_legacy_handover(root: Path, hid: str, *, task_ids: list[str], drop: list[str]) -> None:
    """Commit a handover row with `drop`ped frontmatter keys and a real body.

    Written through the store so the projection guard stays satisfied; the
    dropped keys are what makes each maintenance planner want to rewrite it.
    """
    doc = {
        "id": hid,
        "date": hid[:10],
        "created": f"{hid[:10]}T00:00:00+00:00",
        "tldr": "legacy",
        "task_ids": task_ids,
        "session_kind": "task-complete",
        "status": "open",
        "status_changed": f"{hid[:10]}T00:00:00+00:00",
        "status_user_set": False,
        "thread": "some-thread",
    }
    for key in drop:
        doc.pop(key, None)
    with transaction(_bp(root)) as tx:
        tx.create("handover", doc, body=BODY, requested_id=hid)


def test_status_backfill_keeps_the_handover_body(tm_epic_phase):
    """A legacy handover with no `status` keeps its narrative through backfill."""
    root = tm_epic_phase
    hid = "2025-01-01-legacy"
    _make_legacy_handover(
        root, hid, task_ids=[],
        drop=["status", "status_changed", "status_user_set"],
    )
    bs._HANDOVER_STATUS_BACKFILL_RAN = False

    bs._ensure_handover_status_backfilled()

    with transaction(_bp(root)) as tx:
        doc = dict(next(d for h, d, _b in tx.list("handover") if h == hid))
    assert doc["status"] == "open", "backfill did not run; the test proves nothing"
    assert _committed_body(root, hid) == BODY


def test_thread_backfill_keeps_the_handover_body(tm_epic_phase):
    """`backlog_handover_resync` stamps `thread` without dropping the body."""
    root = tm_epic_phase
    hid = "2025-02-02-nothread"
    _make_legacy_handover(root, hid, task_ids=[], drop=["thread"])
    bs._HANDOVER_STATUS_BACKFILL_RAN = True

    result = bs.backlog_handover_resync()

    with transaction(_bp(root)) as tx:
        doc = dict(next(d for h, d, _b in tx.list("handover") if h == hid))
    assert doc.get("thread"), f"thread backfill did not run: {result}"
    assert _committed_body(root, hid) == BODY


def test_smart_close_keeps_the_handover_body(tm_epic_phase):
    """Completing the last task auto-closes the handover, body intact."""
    root = tm_epic_phase
    added = bs.backlog_add_task(title="Only", epic="test-epic", phase="dev", tldr="only")
    assert "Error" not in added, added
    hid = "2025-03-03-closer"
    _make_legacy_handover(root, hid, task_ids=["test-epic-001"], drop=[])
    bs._HANDOVER_STATUS_BACKFILL_RAN = True

    bs.backlog_update_task(task_id="test-epic-001", field="status", value="in-progress")
    for gate in ("design-review", "review-gate"):
        bs.backlog_skip_gate(task_id="test-epic-001", gate=gate, reason="not under test")
    completed = bs.backlog_complete_task(task_id="test-epic-001", done="finished")

    with transaction(_bp(root)) as tx:
        doc = dict(next(d for h, d, _b in tx.list("handover") if h == hid))
    assert doc["status"] == "closed", f"smart close did not run: {completed}"
    assert _committed_body(root, hid) == BODY
