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


def test_auto_state_returns_most_recent_session(running_server, tmp_path):
    base, _ = running_server
    _seed_session(tmp_path, sid="v3-014", started_at="2026-04-26T17:00:00Z")
    _seed_session(tmp_path, sid="v3-022", started_at="2026-04-26T18:30:00Z")

    body = json.loads(urllib.request.urlopen(f"{base}/api/auto/state").read())
    assert body["session_id"] == "v3-022"


def test_auto_state_no_sessions(running_server):
    base, _ = running_server
    body = json.loads(urllib.request.urlopen(f"{base}/api/auto/state").read())
    assert body == {"running": False}


def test_post_auto_pause_sets_state_and_appends_event(running_server, tmp_path):
    from taskmaster_v3 import load_auto_session, read_auto_events
    base, _ = running_server
    _seed_session(tmp_path, sid="v3-014")

    body = json.dumps({"session_id": "v3-014"}).encode()
    req = urllib.request.Request(
        f"{base}/api/auto/pause", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    assert json.loads(resp.read())["ok"] is True

    state = load_auto_session("v3-014")
    assert state["paused"] is True
    events = read_auto_events("v3-014")
    assert any(e["kind"] == "control_pause" for e in events)


def test_post_auto_stop_sets_state_and_appends_event(running_server, tmp_path):
    from taskmaster_v3 import load_auto_session, read_auto_events
    base, _ = running_server
    _seed_session(tmp_path, sid="v3-014")

    req = urllib.request.Request(
        f"{base}/api/auto/stop",
        data=json.dumps({"session_id": "v3-014"}).encode(),
        method="POST", headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req)
    state = load_auto_session("v3-014")
    assert state["stopped"] is True
    assert any(e["kind"] == "control_stop" for e in read_auto_events("v3-014"))


def test_post_auto_pause_unknown_session_404(running_server):
    base, _ = running_server
    req = urllib.request.Request(
        f"{base}/api/auto/pause",
        data=json.dumps({"session_id": "nope"}).encode(),
        method="POST", headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 404


def test_get_auto_events_filtered_by_since(running_server, tmp_path):
    from taskmaster_v3 import append_auto_event
    base, _ = running_server
    _seed_session(tmp_path, sid="v3-014")
    append_auto_event("v3-014", {"ts":"2026-04-26T18:00:00Z","kind":"stage_enter","stage":"PICK","msg":"a"})
    append_auto_event("v3-014", {"ts":"2026-04-26T19:00:00Z","kind":"stage_enter","stage":"IMPLEMENT","msg":"b"})

    resp = urllib.request.urlopen(f"{base}/api/auto/events?sid=v3-014")
    body = json.loads(resp.read())
    assert len(body["events"]) == 2

    resp = urllib.request.urlopen(f"{base}/api/auto/events?sid=v3-014&since=2026-04-26T18:30:00Z")
    body = json.loads(resp.read())
    assert len(body["events"]) == 1
    assert body["events"][0]["stage"] == "IMPLEMENT"


def test_get_auto_events_missing_sid_400(running_server):
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/api/auto/events")
    assert exc.value.code == 400


def test_get_auto_budget(running_server, tmp_path):
    base, _ = running_server
    _seed_session(tmp_path, sid="v3-014")
    body = json.loads(urllib.request.urlopen(f"{base}/api/auto/budget/v3-014").read())
    assert body["meters"]["tokens"]["used"] == 12000
    assert body["meters"]["tokens"]["limit"] == 200000
    assert body["meters"]["tokens"]["pct"] == pytest.approx(12000 / 200000)
    assert body["meters"]["tokens"]["tier"] == "ok"   # under 60%


def test_get_auto_budget_tiers(running_server, tmp_path):
    base, _ = running_server
    _seed_session(tmp_path, sid="v3-014",
                  budget={
                      "tokens": {"used": 65, "limit": 100},  # 65% → warn
                      "time_seconds": {"used": 95, "limit": 100},  # 95% → crit
                      "context": {"used": 0.10, "limit": 1.0},
                      "cost_usd": {"used": 0.0, "limit": 5.0},
                  })
    body = json.loads(urllib.request.urlopen(f"{base}/api/auto/budget/v3-014").read())
    assert body["meters"]["tokens"]["tier"] == "warn"
    assert body["meters"]["time_seconds"]["tier"] == "crit"
