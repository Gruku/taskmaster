"""HTTP tests for /api/tasks PATCH/PUT/POST."""
import json
import threading
import time
import urllib.request
import urllib.error
import pytest
import yaml


@pytest.fixture
def server_with_backlog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Patch ROOT so _resolve_paths() finds backlog.yaml in tmp_path regardless
    # of when backlog_server was first imported (ROOT is set at import time).
    import backlog_server
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({
        "meta": {"project": "test"},
        "epics": [{"id": "e1", "name": "E1", "status": "active",
                   "tasks": [{"id": "e1-001", "title": "X", "status": "todo",
                              "priority": "medium"}]}],
        "phases": [],
    }))
    from backlog_server import _make_server
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


def _request(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                  headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=2)
    except urllib.error.HTTPError as e:
        return e


def test_patch_task_updates_field(server_with_backlog):
    base = server_with_backlog
    resp = _request("PATCH", f"{base}/api/tasks/e1-001", {"title": "Renamed"})
    assert resp.status == 200
    body = json.loads(resp.read())
    assert body["task"]["title"] == "Renamed"


def test_patch_unknown_id_returns_404(server_with_backlog):
    base = server_with_backlog
    resp = _request("PATCH", f"{base}/api/tasks/nope", {"title": "x"})
    assert resp.status == 404


def test_patch_invalid_json_returns_400(server_with_backlog):
    base = server_with_backlog
    req = urllib.request.Request(f"{base}/api/tasks/e1-001",
                                  data=b"not-json", method="PATCH",
                                  headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=2)
        assert False, "expected 400"
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_post_creates_task(server_with_backlog):
    base = server_with_backlog
    resp = _request("POST", f"{base}/api/tasks",
                     {"epic": "e1", "title": "New", "priority": "high"})
    assert resp.status == 201
    body = json.loads(resp.read())
    assert body["task"]["id"] == "e1-002"
    assert body["task"]["title"] == "New"


def test_post_archive_sets_status(server_with_backlog):
    base = server_with_backlog
    resp = _request("POST", f"{base}/api/tasks/e1-001/archive", {})
    assert resp.status == 200
    # Verify
    detail = urllib.request.urlopen(f"{base}/api/task/e1-001").read()
    assert json.loads(detail)["status"] == "archived"
