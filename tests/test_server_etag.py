"""HTTP concurrency tests — ETag generation + 409 path."""
import json
import threading
import time
import urllib.request
import urllib.error
import pytest
import yaml


@pytest.fixture
def server_etag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({
        "meta": {"project": "test"},
        "epics": [{"id": "e1", "name": "E1", "status": "active",
                   "tasks": [{"id": "e1-001", "title": "X", "status": "todo",
                              "priority": "medium"}]}],
        "phases": [],
    }))
    from taskmaster.backlog_server import _make_server
    server, port = _make_server(host="127.0.0.1", port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(20):
        try:
            urllib.request.urlopen(f"{base}/api/identity", timeout=0.5).read()
            break
        except Exception:
            time.sleep(0.1)
    yield base
    server.shutdown()


def _request(method, url, body=None, headers=None):
    h = headers or {}
    if body is not None:
        h["Content-Type"] = "application/json"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        return urllib.request.urlopen(req, timeout=2)
    except urllib.error.HTTPError as e:
        return e


def test_get_task_emits_etag(server_etag):
    resp = urllib.request.urlopen(f"{server_etag}/api/task/e1-001", timeout=2)
    etag = resp.headers.get("ETag")
    assert etag, "ETag header missing"
    assert len(etag) >= 8


def test_patch_with_correct_etag_succeeds(server_etag):
    get_resp = urllib.request.urlopen(f"{server_etag}/api/task/e1-001", timeout=2)
    etag = get_resp.headers.get("ETag")
    resp = _request("PATCH", f"{server_etag}/api/tasks/e1-001",
                    {"title": "Renamed"}, headers={"If-Match": etag})
    assert resp.status == 200


def test_patch_with_stale_etag_returns_409(server_etag):
    # Read once to capture an etag, then mutate via a separate PATCH
    # without If-Match, then try PATCH with the OLD etag.
    get_resp = urllib.request.urlopen(f"{server_etag}/api/task/e1-001", timeout=2)
    old_etag = get_resp.headers.get("ETag")
    # Bypass If-Match by issuing without the header (server allows missing
    # If-Match for backwards compat — see implementation note in Step 3).
    # To force a real change, call update_task directly:
    from taskmaster.taskmaster_v3 import update_task
    update_task("e1-001", {"title": "Changed by other"})
    resp = _request("PATCH", f"{server_etag}/api/tasks/e1-001",
                    {"title": "My change"}, headers={"If-Match": old_etag})
    assert resp.status == 409
    body = json.loads(resp.read())
    assert body["ok"] is False
    assert body.get("error") == "stale"
    assert "current" in body
    assert "current_etag" in body


def test_patch_without_if_match_proceeds(server_etag):
    """For backward compat with non-edit-aware clients (e.g. existing MCP
    tools that don't speak ETags), PATCH without If-Match goes through.
    The viewer always sends If-Match; this only matters for legacy callers."""
    resp = _request("PATCH", f"{server_etag}/api/tasks/e1-001",
                    {"title": "no etag"})
    assert resp.status == 200
