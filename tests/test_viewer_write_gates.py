"""User intent: the board must not be a way around the gates. The viewer write
path applied the status-transition table but never the completion gate, so a
lane'd task with outstanding blocking reviews reached `done` by drag-and-drop
while the same move was refused through `backlog_update_task` and
`backlog_complete_task`.
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
def gated_task(tm_epic_phase):
    """A lane'd task in progress whose lane still has outstanding gates."""
    created = bs.backlog_add_task(
        title="Work", epic="test-epic", phase="dev", tldr="w", priority="high"
    )
    assert "Error" not in created, created
    task = bs._find_task(bs._load(), "test-epic-001")[0]
    assert task.get("lane"), "the fixture task has no lane, so no gates apply"
    assert bs._outstanding_required_gates(task), (
        "the fixture task has no outstanding gates, so nothing would be blocked"
    )
    moved = bs.backlog_update_task("test-epic-001", "status", "in-progress")
    assert "Error" not in moved, moved
    return tm_epic_phase


@pytest.fixture()
def server(gated_task):
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


def _request(method, url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        resp = urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT)
    except urllib.error.HTTPError as exc:
        resp = exc
    return resp.status, json.loads(resp.read().decode("utf-8"))


def _status_of(task_id: str) -> str:
    return bs._find_task(bs._load(), task_id)[0]["status"]


def test_patch_to_done_is_blocked_by_outstanding_gates(server):
    status, body = _request("PATCH", f"{server}/api/tasks/test-epic-001", {"status": "done"})
    assert status == 409, (status, body)
    assert "outstanding gates" in json.dumps(body), body
    assert _status_of("test-epic-001") == "in-progress"


def test_put_to_done_is_blocked_by_outstanding_gates(server):
    full = bs._load_task_full("test-epic-001")
    full["status"] = "done"
    status, body = _request("PUT", f"{server}/api/tasks/test-epic-001", full)
    assert status == 409, (status, body)
    assert "outstanding gates" in json.dumps(body), body
    assert _status_of("test-epic-001") == "in-progress"


def test_patch_to_done_succeeds_once_the_gates_are_satisfied(server):
    task = bs._find_task(bs._load(), "test-epic-001")[0]
    for gate in bs._outstanding_required_gates(task):
        out = bs.backlog_skip_gate("test-epic-001", gate, reason="not applicable here")
        assert "Error" not in out, out
    status, body = _request("PATCH", f"{server}/api/tasks/test-epic-001", {"status": "done"})
    assert status == 200, (status, body)
    assert _status_of("test-epic-001") == "done"


def test_a_non_done_move_is_unaffected_by_the_gates(server):
    status, body = _request("PATCH", f"{server}/api/tasks/test-epic-001", {"status": "blocked"})
    assert status == 200, (status, body)
    assert _status_of("test-epic-001") == "blocked"
