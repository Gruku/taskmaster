"""Tests for GET /api/dashboard/recent-events."""
import json
import urllib.parse
import urllib.request
import pytest

# Reuse the running_server fixture from Plan 1.
from tests.test_server_api import running_server  # noqa: F401


def test_recent_events_returns_list(running_server):
    base, _ = running_server
    since = "2025-01-01T00:00:00Z"
    qs = urllib.parse.urlencode({"since": since})
    resp = urllib.request.urlopen(f"{base}/api/dashboard/recent-events?{qs}")
    assert resp.status == 200
    body = json.loads(resp.read())
    assert isinstance(body, list)
    for ev in body:
        assert "kind" in ev
        assert "at" in ev
        assert "summary" in ev


def test_recent_events_rejects_missing_since(running_server):
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/api/dashboard/recent-events")
    assert exc.value.code == 400


def test_recent_events_filters_by_since(running_server, tmp_path):
    """Events older than `since` are excluded."""
    base, _ = running_server
    far_future = "2999-01-01T00:00:00Z"
    qs = urllib.parse.urlencode({"since": far_future})
    resp = urllib.request.urlopen(f"{base}/api/dashboard/recent-events?{qs}")
    body = json.loads(resp.read())
    assert body == []
