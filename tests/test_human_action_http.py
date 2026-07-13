"""HTTP task-write path enforces the human_action gate (tm 4.5.0 final-review I1).

The viewer edit modal commits status changes via PATCH/PUT /api/tasks/{id},
which flow through validate_task_write + update_task. These must gate
in-review on a non-whitespace human_action and clear it on done, matching
every other write path.
"""
import json

from tests.test_server_task_write import server_with_backlog, _request  # noqa: F401


def _task(base, task_id="e1-001"):
    import urllib.request
    return json.loads(urllib.request.urlopen(f"{base}/api/task/{task_id}").read())


def test_patch_in_review_without_human_action_is_rejected(server_with_backlog):
    base = server_with_backlog
    resp = _request("PATCH", f"{base}/api/tasks/e1-001", {"status": "in-review"})
    assert resp.status == 422
    body = json.loads(resp.read())
    assert body["ok"] is False
    assert "human_action" in body["errors"]
    # Status unchanged on disk.
    assert _task(base)["status"] == "todo"


def test_patch_in_review_with_human_action_in_payload_succeeds(server_with_backlog):
    base = server_with_backlog
    resp = _request("PATCH", f"{base}/api/tasks/e1-001",
                    {"status": "in-review", "human_action": "add OPENAI_API_KEY to .env"})
    assert resp.status == 200
    after = _task(base)
    assert after["status"] == "in-review"
    assert after["human_action"] == "add OPENAI_API_KEY to .env"


def test_patch_in_review_with_preexisting_human_action_succeeds(server_with_backlog):
    base = server_with_backlog
    assert _request("PATCH", f"{base}/api/tasks/e1-001",
                    {"human_action": "grant repo access"}).status == 200
    resp = _request("PATCH", f"{base}/api/tasks/e1-001", {"status": "in-review"})
    assert resp.status == 200
    assert _task(base)["status"] == "in-review"


def test_patch_in_review_with_whitespace_human_action_is_rejected(server_with_backlog):
    base = server_with_backlog
    resp = _request("PATCH", f"{base}/api/tasks/e1-001",
                    {"status": "in-review", "human_action": "   "})
    assert resp.status == 422
    body = json.loads(resp.read())
    assert "human_action" in body["errors"]
    assert _task(base)["status"] == "todo"


def test_patch_done_clears_human_action(server_with_backlog):
    base = server_with_backlog
    assert _request("PATCH", f"{base}/api/tasks/e1-001",
                    {"status": "in-review", "human_action": "add key"}).status == 200
    resp = _request("PATCH", f"{base}/api/tasks/e1-001", {"status": "done"})
    assert resp.status == 200
    after = _task(base)
    assert after["status"] == "done"
    assert not after.get("human_action")
