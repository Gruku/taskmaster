"""Tests for the Linear GraphQL client (linear-003).

The client is a thin transport with retry + token sanitization. It has no
knowledge of Taskmaster entities — payload shaping happens in the mapper
(linear-004). Tests use httpx.MockTransport for in-process responses; no
network calls and no real Linear API contact.
"""
import sys
from pathlib import Path

import httpx
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster.integrations.linear.client import LinearAPIError, LinearClient  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────


def _client_with_handler(handler, token: str = "lin_api_test_xyz") -> LinearClient:
    """Build a LinearClient backed by an httpx MockTransport whose every
    request invokes `handler(request) -> httpx.Response`."""
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return LinearClient(token=token, _http_client=http, _sleep=lambda _: None)


def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


# ── Auth header ────────────────────────────────────────────────


def test_client_sends_token_in_authorization_header():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return _ok({"teams": {"nodes": []}})

    client = _client_with_handler(handler, token="lin_api_secret_42")
    client.list_teams()
    assert seen["auth"] == "lin_api_secret_42"


# ── Read surfaces (bootstrap helpers) ──────────────────────────


def test_list_teams_returns_parsed_nodes():
    def handler(request):
        return _ok({"teams": {"nodes": [
            {"id": "t1", "name": "Project-CM-v2", "key": "CM"},
            {"id": "t2", "name": "Other", "key": "OTH"},
        ]}})

    client = _client_with_handler(handler)
    teams = client.list_teams()
    assert teams == [
        {"id": "t1", "name": "Project-CM-v2", "key": "CM"},
        {"id": "t2", "name": "Other", "key": "OTH"},
    ]


def test_list_users_returns_parsed_nodes():
    def handler(request):
        return _ok({"users": {"nodes": [
            {"id": "u1", "name": "Volodymyr", "email": "v@example.com"},
        ]}})

    client = _client_with_handler(handler)
    users = client.list_users(team_id="t1")
    assert users == [{"id": "u1", "name": "Volodymyr", "email": "v@example.com"}]


def test_list_issue_statuses_returns_team_states():
    def handler(request):
        return _ok({"team": {"states": {"nodes": [
            {"id": "s1", "name": "Todo", "type": "unstarted"},
            {"id": "s2", "name": "In Progress", "type": "started"},
            {"id": "s3", "name": "Done", "type": "completed"},
        ]}}})

    client = _client_with_handler(handler)
    states = client.list_issue_statuses(team_id="t1")
    assert {s["name"] for s in states} == {"Todo", "In Progress", "Done"}


def test_list_labels_returns_team_labels():
    def handler(request):
        return _ok({"team": {"labels": {"nodes": [
            {"id": "l1", "name": "Bug"},
            {"id": "l2", "name": "Feature"},
        ]}}})

    client = _client_with_handler(handler)
    labels = client.list_labels(team_id="t1")
    assert [l["name"] for l in labels] == ["Bug", "Feature"]


# ── Write surfaces (mutations) ─────────────────────────────────


def test_issue_upsert_uses_issueCreate_when_no_id():
    seen: dict = {}

    def handler(request):
        seen["body"] = request.read().decode()
        return _ok({"issueCreate": {"issue": {"id": "iss-new", "identifier": "CM-99"}}})

    client = _client_with_handler(handler)
    result = client.issue_upsert(team_id="t1", payload={"title": "Hello"})
    assert "issueCreate" in seen["body"]
    assert "issueUpdate" not in seen["body"]
    assert result == {"id": "iss-new", "identifier": "CM-99"}


def test_issue_upsert_uses_issueUpdate_when_id_present():
    seen: dict = {}

    def handler(request):
        seen["body"] = request.read().decode()
        return _ok({"issueUpdate": {"issue": {"id": "iss-existing", "identifier": "CM-42"}}})

    client = _client_with_handler(handler)
    result = client.issue_upsert(team_id="t1", payload={"id": "iss-existing", "title": "Updated"})
    assert "issueUpdate" in seen["body"]
    assert "issueCreate" not in seen["body"]
    assert result["id"] == "iss-existing"


def test_project_upsert_create_when_no_id():
    def handler(request):
        return _ok({"projectCreate": {"project": {"id": "p1", "name": "v3 release"}}})

    client = _client_with_handler(handler)
    result = client.project_upsert(team_id="t1", payload={"name": "v3 release"})
    assert result == {"id": "p1", "name": "v3 release"}


def test_comment_create_returns_comment_id():
    def handler(request):
        return _ok({"commentCreate": {"comment": {"id": "c1"}}})

    client = _client_with_handler(handler)
    result = client.comment_create(issue_id="iss-1", body="hello")
    assert result == {"id": "c1"}


# ── Retry behaviour ────────────────────────────────────────────


def test_retries_on_500_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "server"})
        return _ok({"teams": {"nodes": []}})

    client = _client_with_handler(handler)
    client.list_teams()
    assert calls["n"] == 2


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return _ok({"teams": {"nodes": []}})

    client = _client_with_handler(handler)
    client.list_teams()
    assert calls["n"] == 2


def test_retries_on_network_error_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return _ok({"teams": {"nodes": []}})

    client = _client_with_handler(handler)
    client.list_teams()
    assert calls["n"] == 2


def test_does_not_retry_on_401():
    """Auth failures are permanent — retrying would just waste API quota."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    client = _client_with_handler(handler)
    with pytest.raises(LinearAPIError, match="401|auth"):
        client.list_teams()
    assert calls["n"] == 1


def test_does_not_retry_on_403():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403)

    client = _client_with_handler(handler)
    with pytest.raises(LinearAPIError):
        client.list_teams()
    assert calls["n"] == 1


def test_exhausts_retries_then_raises():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500)

    client = _client_with_handler(handler)
    with pytest.raises(LinearAPIError):
        client.list_teams()
    assert calls["n"] == LinearClient.MAX_RETRIES


# ── Error sanitization ─────────────────────────────────────────


def test_sanitizes_token_from_error_messages():
    """An error trace that would echo the token gets the token redacted —
    so the operator can paste the error without leaking credentials."""
    token = "lin_api_super_secret_pls_dont_leak"

    def handler(request):
        # Pretend Linear echoes the token back in an error body (worst case)
        return httpx.Response(400, text=f"bad request including {token}")

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    client = LinearClient(token=token, _http_client=http, _sleep=lambda _: None)

    with pytest.raises(LinearAPIError) as exc:
        client.list_teams()
    assert token not in str(exc.value)
    assert "<token-redacted>" in str(exc.value)


# ── GraphQL errors ─────────────────────────────────────────────


def test_raises_on_graphql_errors_field():
    """200 OK with an `errors` array is still a failure in GraphQL.
    Don't return the empty data block as if it were success."""
    def handler(request):
        return httpx.Response(200, json={
            "data": None,
            "errors": [{"message": "Cannot query field 'foo' on type 'Team'"}],
        })

    client = _client_with_handler(handler)
    with pytest.raises(LinearAPIError, match="GraphQL"):
        client.list_teams()
