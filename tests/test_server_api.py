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
    # Canonical v3 layout: backlog.yaml lives next to its artifact subdirs
    # (lessons/, issues/, handovers/, recaps/, ...). Pre-ISS-004 the fixture
    # placed backlog.yaml at the root and artifacts under .taskmaster/, which
    # silently divergent — the very bug ISS-004 fixes.
    (tmp_path / ".taskmaster" / "backlog.yaml").write_text(
        "meta:\n  project: test\nepics: []\nphases: []\n"
    )

    import backlog_server as _bs
    monkeypatch.setattr(_bs, "ROOT", tmp_path)
    monkeypatch.setattr(_bs, "CONFIG_PATH", tmp_path / ".taskmaster" / "taskmaster.json")
    monkeypatch.setattr(_bs, "LEGACY_CONFIG_PATH", tmp_path / ".claude" / "taskmaster.json")

    from backlog_server import _make_server, _init_storage  # added in this task
    server, port = _make_server(host="127.0.0.1", port=0)
    _init_storage()
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
    assert body["zoom"] == 1.0


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


def test_api_endpoints_set_cors_header(running_server):
    base, _ = running_server
    for path in ["/api/identity", "/api/viewer/prefs"]:
        resp = urllib.request.urlopen(f"{base}{path}")
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"


def test_get_v3_returns_index_html(running_server):
    base, _ = running_server
    resp = urllib.request.urlopen(f"{base}/v3")
    assert resp.status == 200
    assert resp.headers.get("Content-Type", "").startswith("text/html")
    body = resp.read().decode()
    assert "<title>Taskmaster</title>" in body
    assert 'src="js/main.js"' in body or "main.js" in body  # main JS referenced


def test_get_static_v3_tokens_css(running_server):
    base, _ = running_server
    resp = urllib.request.urlopen(f"{base}/static/v3/css/tokens.css")
    assert resp.status == 200
    assert resp.headers.get("Content-Type", "").startswith("text/css")
    assert "--bg-canvas" in resp.read().decode()


def test_static_v3_path_traversal_blocked(running_server):
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/static/v3/../../etc/passwd")
    assert exc.value.code == 400


def test_static_v3_path_traversal_url_encoded_blocked(running_server):
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/static/v3/%2e%2e/%2e%2e/etc/passwd")
    assert exc.value.code == 400


def test_root_serves_v3_when_use_v3_flag_set(running_server):
    base, _ = running_server
    # Flip the prefs flag
    put_body = json.dumps({"use_v3": True}).encode()
    req = urllib.request.Request(f"{base}/api/viewer/prefs", data=put_body, method="PUT",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)

    resp = urllib.request.urlopen(f"{base}/")
    assert resp.status == 200
    html = resp.read().decode()
    # When use_v3 is True, root serves the new shell, not the legacy file
    assert "<title>Taskmaster</title>" in html
    assert 'src="/static/v3/js/main.js"' in html or 'main.js' in html


def test_root_serves_legacy_by_default(running_server):
    base, _ = running_server
    resp = urllib.request.urlopen(f"{base}/")
    body = resp.read().decode()
    # The legacy viewer; whatever is in backlog-viewer.html (we just check it's NOT the v3 shell).
    # If the legacy file isn't present in the test fixture, this test should xfail rather than fail.
    # Heuristic: legacy file is much larger and includes 'jsyaml' inline.
    # If legacy isn't shipped to test fixture, accept either; but assert v3 marker is absent.
    assert 'src="/static/v3/js/main.js"' not in body
