"""User intent: one status-transition table, enforced wherever a status is
written. The batch and viewer paths used to check only the `archived` row, so a
lane'd task could jump `todo -> done` through the board or a batch script and
skip every gate the same move is refused for through `backlog_update_task`.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from taskmaster import backlog_server as bs


_HTTP_TIMEOUT = 15


@pytest.fixture()
def laned_task(tm_epic_phase):
    created = bs.backlog_add_task(
        title="Work", epic="test-epic", phase="dev", tldr="w", priority="high"
    )
    assert "Error" not in created, created
    assert bs._find_task(bs._load(), "test-epic-001")[0].get("lane"), (
        "the fixture task has no lane, so the transition table would not apply"
    )
    return tm_epic_phase


def _status_of(task_id: str) -> str:
    return bs._find_task(bs._load(), task_id)[0]["status"]


# ── batch ─────────────────────────────────────────────────────────────────────


def test_batch_update_refuses_todo_to_done(laned_task):
    out = bs.backlog_batch_update(operations="update test-epic-001 status done")
    assert "illegal transition" in out, out
    assert "`todo`" in out and "`done`" in out, out
    assert _status_of("test-epic-001") == "todo"


def test_batch_status_op_refuses_todo_to_done(laned_task):
    """The `status <id> done` shorthand runs the completion guard before the
    transition table, so the refusal names the states a task may be completed
    *from* — the more actionable half of the same rule. Either message is a
    refusal; what matters is that the move does not land."""
    out = bs.backlog_batch_update(operations="status test-epic-001 done")
    assert "cannot complete from `todo`" in out or "illegal transition" in out, out
    assert "0 applied" in out, out
    assert _status_of("test-epic-001") == "todo"


def test_batch_update_allows_a_legal_transition(laned_task):
    out = bs.backlog_batch_update(operations="update test-epic-001 status in-progress")
    assert "illegal transition" not in out, out
    assert _status_of("test-epic-001") == "in-progress"


def test_batch_preview_reports_the_illegal_transition(laned_task):
    out = bs.backlog_batch_preview(operations="status test-epic-001 done")
    assert "illegal transition" in out, out


# ── viewer ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def server(laned_task):
    srv, port = bs._make_server(host="127.0.0.1", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{base}/api/identity", timeout=0.5).read()
            break
        except Exception:
            time.sleep(0.05)
    yield base
    srv.shutdown()
    srv.server_close()


def _patch(base, task_id, payload):
    req = urllib.request.Request(
        f"{base}/api/tasks/{task_id}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT)
    except urllib.error.HTTPError as exc:
        resp = exc
    return resp.status, json.loads(resp.read().decode("utf-8"))


def test_viewer_patch_refuses_todo_to_done(server):
    status, body = _patch(server, "test-epic-001", {"status": "done"})
    assert status == 422, (status, body)
    assert "illegal transition" in json.dumps(body), body
    assert _status_of("test-epic-001") == "todo"


def test_viewer_patch_allows_a_legal_transition(server):
    status, body = _patch(server, "test-epic-001", {"status": "in-progress"})
    assert status == 200, (status, body)
    assert _status_of("test-epic-001") == "in-progress"


def test_viewer_patch_still_refuses_resurrecting_an_archived_task(server):
    bs.backlog_archive_task(task_id="test-epic-001")
    status, body = _patch(server, "test-epic-001", {"status": "done"})
    assert status == 422, (status, body)
    assert "illegal transition" in json.dumps(body), body


def test_laneless_tasks_keep_the_permissive_behaviour(tm_epic_phase):
    """The table is lane-gated in `backlog_update_task`; batch and viewer must
    apply the same rule rather than a stricter one of their own."""
    assert "Error" not in bs.backlog_add_task(
        title="Old", epic="test-epic", phase="dev", tldr="o"
    )
    with bs._transaction(tool="test:strip-lane") as data:
        task = bs._find_task(data, "test-epic-001")[0]
        task.pop("lane", None)
        task.pop("gates", None)
        bs._mutate_and_save(data)
    out = bs.backlog_batch_update(operations="update test-epic-001 status done")
    assert "illegal transition" not in out, out
