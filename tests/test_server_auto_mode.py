"""HTTP API tests for Auto Mode endpoints (Plan 6).

Reuses the running_server fixture from test_server_api.py.
"""
import json
import urllib.request
import urllib.error
import pytest

from tests.test_server_api import running_server  # noqa: F401  (fixture re-export)


def _seed_session(tmp_path, sid="v3-014", **overrides):
    """Helper: write a session JSON file directly into the server's working dir."""
    from taskmaster_v3 import save_auto_session
    base = {
        "session_id": sid,
        "task_id": sid,
        "title": "Auto-mode status indicator",
        "worktree": ".worktrees/v3-014",
        "mode": "walk",
        "started_at": "2026-04-26T18:42:09Z",
        "cursor": {"task_id": sid, "stage": "IMPLEMENT", "model": "sonnet"},
        "completed": ["PICK"],
        "pending": ["REVIEW", "HANDOVER_STUB", "COMPLETE"],
        "failed": [],
        "subagents": [],
        "tool_log": [],
        "budget": {
            "tokens": {"used": 12000, "limit": 200000},
            "time_seconds": {"used": 1820, "limit": 14400},
            "context": {"used": 0.18, "limit": 1.0},
            "cost_usd": {"used": 0.42, "limit": 5.00},
        },
    }
    base.update(overrides)
    save_auto_session(sid, base)
    return base


def test_get_auto_sessions_lists_all(running_server, tmp_path, monkeypatch):
    base, _ = running_server
    _seed_session(tmp_path, sid="v3-014")
    _seed_session(tmp_path, sid="v3-022", title="Filter bar polish")

    resp = urllib.request.urlopen(f"{base}/api/auto/sessions")
    assert resp.status == 200
    payload = json.loads(resp.read())
    ids = sorted(s["session_id"] for s in payload["sessions"])
    assert ids == ["v3-014", "v3-022"]


def test_get_auto_session_detail(running_server, tmp_path):
    base, _ = running_server
    _seed_session(tmp_path, sid="v3-014")

    resp = urllib.request.urlopen(f"{base}/api/auto/sessions/v3-014")
    assert resp.status == 200
    body = json.loads(resp.read())
    assert body["session_id"] == "v3-014"
    assert body["cursor"]["stage"] == "IMPLEMENT"


def test_get_auto_session_detail_404(running_server):
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/api/auto/sessions/missing")
    assert exc.value.code == 404
