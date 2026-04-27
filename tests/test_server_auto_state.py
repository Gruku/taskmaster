"""Tests for /api/auto/state read-only endpoint and helper."""
import json
import threading
import time
import urllib.request
import urllib.error
import pytest


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    """Spin up backlog_server on an ephemeral port — same shape as Plan 1's fixture."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    (tmp_path / "backlog.yaml").write_text(
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


def test_load_auto_state_returns_none_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    from backlog_server import _load_auto_state
    assert _load_auto_state() is None


def test_load_auto_state_returns_parsed_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    auto_dir = tmp_path / ".taskmaster" / "auto"
    auto_dir.mkdir(parents=True)
    payload = {
        "mode": "running",
        "target": "v3-009",
        "started_at": "2026-04-26T10:00:00Z",
        "cursor": {"task_id": "v3-009", "stage": "IMPLEMENT", "model": "sonnet"},
        "completed": [],
        "pending": ["v3-011", "v3-012"],
        "failed": [],
        "models": {},
        "config": {},
    }
    (auto_dir / "state.json").write_text(json.dumps(payload))
    from backlog_server import _load_auto_state
    got = _load_auto_state()
    assert got["mode"] == "running"
    assert got["cursor"]["stage"] == "IMPLEMENT"
    assert got["pending"] == ["v3-011", "v3-012"]


def test_load_auto_state_returns_none_on_corrupt_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    auto_dir = tmp_path / ".taskmaster" / "auto"
    auto_dir.mkdir(parents=True)
    (auto_dir / "state.json").write_text("{ this is not json")
    from backlog_server import _load_auto_state
    assert _load_auto_state() is None
