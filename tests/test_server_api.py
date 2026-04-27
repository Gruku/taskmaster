"""HTTP API tests. Spin up the server in-process on an ephemeral port."""
import json
import threading
import time
import urllib.request
import pytest


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    """Start backlog_server on a free port, yielding (base_url, server)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    # Minimal backlog.yaml so the server doesn't 404
    (tmp_path / "backlog.yaml").write_text(
        "meta:\n  project: test\nepics: []\nphases: []\n"
    )

    from backlog_server import _make_server  # added in this task
    server, port = _make_server(host="127.0.0.1", port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    # Wait briefly for thread to be ready
    for _ in range(20):
        try:
            urllib.request.urlopen(f"{base}/api/identity", timeout=0.5).read()
            break
        except Exception:
            time.sleep(0.05)

    yield base, server

    server.shutdown()
    server.server_close()


def test_get_viewer_prefs_returns_defaults_on_first_call(running_server):
    base, _ = running_server
    resp = urllib.request.urlopen(f"{base}/api/viewer/prefs")
    assert resp.status == 200
    body = json.loads(resp.read())
    assert body["theme"] == "dark"
    assert body["card_density"] == "full"
    assert body["zoom"] == 1.5


def test_put_viewer_prefs_merges_patch(running_server):
    base, _ = running_server
    body = json.dumps({"theme": "light", "kanban": {"filters": {"search": "auth"}}}).encode()
    req = urllib.request.Request(
        f"{base}/api/viewer/prefs",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    assert json.loads(resp.read())["ok"] is True

    # GET reflects the patch
    after = json.loads(urllib.request.urlopen(f"{base}/api/viewer/prefs").read())
    assert after["theme"] == "light"
    assert after["kanban"]["filters"]["search"] == "auth"
    assert after["card_density"] == "full"  # untouched


def test_put_viewer_prefs_rejects_non_object(running_server):
    base, _ = running_server
    req = urllib.request.Request(
        f"{base}/api/viewer/prefs",
        data=b'"not an object"',
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 400
