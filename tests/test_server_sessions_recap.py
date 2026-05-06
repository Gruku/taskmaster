"""HTTP + MCP tests for sessions / recap / snapshot-diff endpoints."""
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
    # (handovers/, recaps/, ...). ISS-004.
    (tmp_path / ".taskmaster" / "backlog.yaml").write_text(
        "meta:\n  project: test\nepics: []\nphases: []\n"
    )
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
            time.sleep(0.05)
    yield base, server
    server.shutdown()
    server.server_close()


def test_recap_set_then_get_round_trip_via_mcp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    from backlog_server import recap_set, recap_get
    msg = recap_set(
        "SES-0001",
        json.dumps({"snapshot_before": "SNAP-0000", "snapshot_after": "SNAP-0001",
                    "generator": "claude", "generated_at": "2026-04-26T16:48Z",
                    "token_cost": 1840}),
        "Hero",
        "Started worktree-shadow.",
        "Three closed.",
        "Rebase tomorrow.",
    )
    assert "ok" in msg.lower()
    text = recap_get("SES-0001")
    body = json.loads(text)
    assert body["title"] == "Hero"
    assert body["what_happened"].startswith("Started")


def test_recap_list_via_mcp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    from backlog_server import recap_set, recap_list
    recap_set("SES-0002",
              json.dumps({"snapshot_before":"a","snapshot_after":"b","generator":"x",
                          "generated_at":"2026-04-26T00:00Z","token_cost":0}),
              "t", "x", "y", "z")
    out = json.loads(recap_list())
    assert "SES-0002" in out


def test_snapshot_diff_via_mcp():
    from backlog_server import snapshot_diff as snap_diff_tool
    out = snap_diff_tool(
        json.dumps({"tasks": {"T-1": {"status": "todo"}}}),
        json.dumps({"tasks": {"T-1": {"status": "done"}}}),
    )
    body = json.loads(out)
    assert body["tasks_changed"][0]["id"] == "T-1"


def test_http_get_sessions_returns_list(running_server, tmp_path):
    base, _ = running_server
    # Seed one handover so list_sessions has data
    (tmp_path / ".taskmaster" / "handovers").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".taskmaster" / "handovers" / "2026-04-26-1640-x.md").write_text(
        "---\nid: 2026-04-26-1640-x\ndate: 2026-04-26T16:40:00Z\n"
        "tldr: x\nnext_action: y\ntask_ids: [T-1]\n"
        "session_kind: end-of-day\ncontext_size_at_write: 0.5\n---\n\nbody\n",
        encoding="utf-8",
    )
    resp = urllib.request.urlopen(f"{base}/api/sessions")
    assert resp.status == 200
    body = json.loads(resp.read())
    assert isinstance(body, list)
    assert body[0]["id"].startswith("SES-")
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


def test_http_recap_put_then_get_round_trip(running_server):
    base, _ = running_server
    payload = {
        "frontmatter": {
            "snapshot_before": "SNAP-0000", "snapshot_after": "SNAP-0001",
            "generator": "claude", "generated_at": "2026-04-26T16:48Z",
            "token_cost": 1840,
        },
        "title": "Hero",
        "what_happened": "A",
        "what_landed": "B",
        "whats_next": "C",
    }
    req = urllib.request.Request(
        f"{base}/api/recap/SES-0001",
        data=json.dumps(payload).encode(),
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    assert json.loads(resp.read())["ok"] is True

    resp2 = urllib.request.urlopen(f"{base}/api/recap/SES-0001")
    body = json.loads(resp2.read())
    assert body["title"] == "Hero"
    assert body["frontmatter"]["session_id"] == "SES-0001"


def test_http_recap_get_unknown_returns_404(running_server):
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/api/recap/SES-9999")
    assert exc.value.code == 404


def test_http_snapshot_diff_endpoint(running_server, tmp_path):
    base, _ = running_server
    snaps = tmp_path / ".taskmaster" / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    (snaps / "SNAP-A.json").write_text(json.dumps({"tasks": {"T-1": {"status":"todo"}}}))
    (snaps / "SNAP-B.json").write_text(json.dumps({"tasks": {"T-1": {"status":"done"}}}))
    resp = urllib.request.urlopen(f"{base}/api/snapshots/diff?from=SNAP-A&to=SNAP-B")
    assert resp.status == 200
    body = json.loads(resp.read())
    assert body["tasks_changed"][0]["id"] == "T-1"


def test_http_snapshot_diff_missing_param_400(running_server):
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/api/snapshots/diff?from=SNAP-A")
    assert exc.value.code == 400
