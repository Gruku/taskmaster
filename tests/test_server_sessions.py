"""HTTP + MCP tests for the sessions endpoint."""
import json
import threading
import time
import urllib.request
import urllib.error
import pytest


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    # Canonical v3 layout: backlog.yaml lives next to its artifact subdirs
    # (handovers/, ...). ISS-004.
    (tmp_path / ".taskmaster" / "backlog.yaml").write_text(
        "meta:\n  project: test\nepics: []\nphases: []\n"
    )
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
            time.sleep(0.05)
    yield base, server
    server.shutdown()
    server.server_close()


def test_http_get_sessions_returns_list(running_server, tmp_path):
    base, _ = running_server
    # Seed one handover so list_sessions has data
    (tmp_path / ".taskmaster" / "handovers").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".taskmaster" / "handovers" / "2026-04-26-1640-x.md").write_text(
        "---\nid: 2026-04-26-1640-x\ndate: 2026-04-26T16:40:00Z\n"
        "tldr: x\nnext_action: y\ntask_ids: [T-1]\n"
        "session_kind: continuity\ncontext_size_at_write: 0.5\n---\n\nbody\n",
        encoding="utf-8",
    )
    resp = urllib.request.urlopen(f"{base}/api/sessions")
    assert resp.status == 200
    body = json.loads(resp.read())
    assert isinstance(body, list)
    # Threadless handover → solo lane keyed by its own id (SES-NNNN is gone).
    assert body[0]["id"] == "2026-04-26-1640-x"
    sid = body[0]["id"]

    resp2 = urllib.request.urlopen(f"{base}/api/sessions/{sid}")
    assert resp2.status == 200
    detail = json.loads(resp2.read())
    assert detail["session"]["id"] == sid
    assert len(detail["handovers"]) == 1
    assert detail["handovers"][0]["viewer_kind"] == "wrap"


def test_http_get_unknown_session_returns_404(running_server):
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/api/sessions/SES-9999")
    assert exc.value.code == 404
