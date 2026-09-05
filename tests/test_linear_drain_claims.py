"""User intent: a Linear drain must not lose a push. Marking a row done has to
mean "the state that row asked for reached Linear", so an edit landing during
the HTTP round-trip needs a queue request of its own, two drains must never
push the same row, and recording the outcome must not overwrite the tracker's
body or the fields the push did not touch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster import store as _store  # noqa: E402
from taskmaster.integrations.linear import worker as _worker  # noqa: E402
from taskmaster.integrations.linear.worker import drain, enqueue, push_task  # noqa: E402
from tests.entity_helpers import taskmaster_backlog, write_tracker  # noqa: E402
from tests.test_linear_worker import (  # noqa: E402
    _backlog_data,
    _make_backlog,
    _make_client,
    _make_config,
)


def _enqueue(bp: Path, **kwargs) -> int:
    with _store.open_store(bp).transaction(tool="test-enqueue") as tx:
        return enqueue(tx, **kwargs)


def _tracker_row(bp: Path, tracker_id: str) -> tuple[dict, str | None]:
    with _store.open_store(bp).transaction(tool="test-read") as tx:
        for tid, doc, body in tx.list("tracker"):
            if tid == tracker_id:
                return dict(doc), body
    raise AssertionError(f"tracker {tracker_id} not found")


def _seed_tracker(bp: Path, *, body: str = "", **extra) -> str:
    tid, _path = write_tracker(
        bp,
        external_system="linear",
        instance_alias="cm",
        external_key="ENG-1",
        title="old title",
        status="Todo",
        body=body,
        **extra,
    )
    return tid


def _ok_handler(request):
    return httpx.Response(
        200,
        json={"data": {"issueUpdate": {"issue": {"id": "iss-uuid", "identifier": "ENG-1"}}}},
    )


# -- C4: recording a push result preserves the rest of the tracker ----------


def test_a_successful_push_keeps_the_tracker_body(tmp_path):
    """The worker read the tracker, stripped its body and submitted the whole
    stale document, so every successful push wiped the tracker's narrative."""
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    tid = _seed_tracker(bp, body="## Why this issue exists\n\nContext worth keeping.\n")

    result = push_task(bp, "linear-001", _make_client(_ok_handler), _make_config(),
                       backlog_data=_backlog_data(bp))

    assert result["status"] == "ok", result
    doc, body = _tracker_row(bp, tid)
    assert doc["push_hash"], "the push result was not recorded at all"
    assert body == "## Why this issue exists\n\nContext worth keeping.\n"


def test_a_successful_push_keeps_a_field_edited_during_the_round_trip(tmp_path):
    """The tracker was read before the HTTP call and written back whole after
    it, so an edit committed while the request was in flight was silently
    reverted. Only the push-result fields may move."""
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    tid = _seed_tracker(bp)

    def handler(request):
        # Someone else edits the tracker while the push is in flight.
        with _store.open_store(bp).transaction(tool="concurrent-edit") as tx:
            doc = tx.get("tracker", tid)
            doc.pop("_body", None)
            doc["notes"] = "edited mid-flight"
            tx.put("tracker", tid, doc)
        return _ok_handler(request)

    result = push_task(bp, "linear-001", _make_client(handler), _make_config(),
                       backlog_data=_backlog_data(bp))

    assert result["status"] == "ok", result
    doc, _body = _tracker_row(bp, tid)
    assert doc.get("notes") == "edited mid-flight"
    assert doc.get("push_hash")


# -- C3: the drain claims its rows -----------------------------------------


def test_an_edit_during_the_drain_gets_its_own_queue_request(tmp_path):
    """The row the drain is pushing is no longer `pending`, so an enqueue that
    lands mid-drain cannot be folded into it and then marked done with the new
    state never sent."""
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    _seed_tracker(bp)
    _enqueue(bp, op="task_upsert", target_id="linear-001", tracker_id="linear-cm-eng-1")

    def handler(request):
        _enqueue(bp, op="task_upsert", target_id="linear-001",
                 tracker_id="linear-cm-eng-1")
        return _ok_handler(request)

    counts = drain(_store.open_store(bp), _make_client(handler), _make_config(),
                   backlog_data=_backlog_data(bp))

    assert counts["ok"] == 1
    still_owed = _store.open_store(bp).linear_pending(10)
    assert [row["target_id"] for row in still_owed] == ["linear-001"], (
        "the edit that landed mid-drain left no pending request"
    )


def test_two_drains_do_not_push_the_same_row(tmp_path):
    """A row claimed by one drain is invisible to the next, so a second drain
    running while the first is in HTTP cannot issue the same push."""
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    _seed_tracker(bp)
    _enqueue(bp, op="task_upsert", target_id="linear-001", tracker_id="linear-cm-eng-1")

    seen: list[str] = []

    def handler(request):
        seen.append("push")
        # A second drain starts while this one is mid-request.
        second = drain(_store.open_store(bp), _make_client(lambda r: _ok_handler(r)),
                       _make_config(), backlog_data=_backlog_data(bp))
        assert second == {"ok": 0, "skipped": 0, "transient": 0, "permanent": 0,
                          "unknown": 0}, second
        return _ok_handler(request)

    drain(_store.open_store(bp), _make_client(handler), _make_config(),
          backlog_data=_backlog_data(bp))

    assert seen == ["push"], "the row was pushed more than once"


def test_a_claim_abandoned_by_a_crashed_drain_is_recoverable(tmp_path):
    """A drain that dies mid-push leaves its row claimed. The lease has to
    expire, or that push is stranded forever with no pending request."""
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    _seed_tracker(bp)
    seq = _enqueue(bp, op="task_upsert", target_id="linear-001",
                   tracker_id="linear-cm-eng-1")

    opened = _store.open_store(bp)
    claimed = opened.linear_claim(10, owner="dead-drain", lease_seconds=0.0)
    assert [row["seq"] for row in claimed] == [seq]
    assert opened.linear_pending(10) == []

    # A later drain, past the lease, may take it over.
    retaken = opened.linear_claim(10, owner="live-drain", lease_seconds=0.0)
    assert [row["seq"] for row in retaken] == [seq]

    # The dead drain's mark must not settle a row it no longer owns.
    assert opened.linear_mark(seq, state="done", owner="dead-drain") is False
    assert opened.linear_mark(seq, state="done", owner="live-drain") is True
