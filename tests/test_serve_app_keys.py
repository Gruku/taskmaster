# User intent: the bootstrap token works only on the host, a session belongs to the key that opened it, and revoking a key ends its open sessions.
from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace

import httpx
import pytest
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from fastmcp.server.auth import AccessToken

from serve_helpers import call_tool, running_app, toy_server
from taskmaster.serve import app as app_module
from taskmaster.serve import auth
from taskmaster.serve.app import (OPERATOR_REMOTE_REFUSED, RequirePrincipalMiddleware, build_app,
                                  check_session_internals, streamable_asgi)
from taskmaster.serve.auth import KeyAuthenticator, OperatorTokenAuthenticator, ensure_operator_token, token_path
from taskmaster.serve.principals import PrincipalRegistry, RegistryUnavailable, add_user, principals_path, revoke_user, rotate_user

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}


class Flaky(PrincipalRegistry):
    """Lets a test make the sweep's read fail without touching authentication."""
    mode = None

    def active_ids(self):
        if self.mode == "unavailable":
            raise RegistryUnavailable
        if self.mode == "boom":
            self.mode = None
            raise ValueError("boom")
        return super().active_ids()


@pytest.fixture
def world(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    operator, _ = ensure_operator_token(tmp_path)
    ann_key, ann, _ = add_user(tmp_path, "ann")
    bob_key, bob, _ = add_user(tmp_path, "bob")
    registry = Flaky(principals_path(tmp_path))
    authn = KeyAuthenticator(OperatorTokenAuthenticator(token_path(tmp_path)), registry)
    app = build_app(toy_server(), authn, allowed_hosts=("tm.example.com",), registry=registry, sweep_seconds=0.2)
    return SimpleNamespace(root=tmp_path, operator=operator, ann_key=ann_key, ann=ann.key_id, bob_key=bob_key,
                           bob=bob.key_id, registry=registry, app=app)


def post(url, token, *, body=INIT, headers=None, sid=None):
    merged = {"Authorization": f"Bearer {token}", **ACCEPT, **(headers or {})}
    if sid:
        merged["mcp-session-id"] = sid
    return httpx.post(url + "/mcp", headers=merged, json=body)


def open_session(url, token):
    response = post(url, token)
    assert response.status_code == 200, response.text
    sid = response.headers["mcp-session-id"]
    sent = post(url, token, body={"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=sid)
    assert sent.status_code == 202
    return sid


TOOLS = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def instances(world):
    return streamable_asgi(world.app, "/mcp").session_manager._server_instances


def test_member_key_reaches_the_tool(world):
    with running_app(world.app) as url:
        assert json.loads(call_tool(url + "/mcp", world.ann_key, "whoami")) == {
            "handle": "ann", "role": "member", "key_id": world.ann}


@pytest.mark.parametrize("headers", [{"X-Forwarded-For": "203.0.113.9"}, {"Forwarded": "for=203.0.113.9"},
                                     {"X-Real-IP": "203.0.113.9"}, {"Host": "tm.example.com"}])
def test_operator_token_is_refused_when_forwarded(world, headers, caplog):
    with running_app(world.app) as url:
        with caplog.at_level(logging.DEBUG, logger="taskmaster.serve"):
            refused = post(url, world.operator, headers=headers)
        assert refused.status_code == 403 and refused.json() == {"error": OPERATOR_REMOTE_REFUSED}
        assert refused.headers["cache-control"] == "no-store"
        assert sum("not host-local" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records) == 1
        assert post(url, world.ann_key, headers=headers).status_code != 403


def test_operator_token_works_on_the_host(world):
    with running_app(world.app) as url:
        assert post(url, world.operator).status_code == 200


def test_the_middleware_uses_the_shared_check(world, monkeypatch):
    monkeypatch.setattr(auth, "is_host_local", lambda scope: False)
    with running_app(world.app) as url:
        assert post(url, world.operator).status_code == 403


def test_operator_refusal_never_reads_the_body():
    async def inner(scope, receive, send):
        raise AssertionError("inner app reached")

    async def receive():
        raise AssertionError("body was read")
    sent = []

    async def send(message):
        sent.append(message)
    user = AuthenticatedUser(AccessToken(token="key:operator", client_id="operator", subject="operator", scopes=[],
                                         claims={"taskmaster_principal": "operator", "taskmaster_role": "operator",
                                                 "taskmaster_key_id": "operator"}))
    scope = {"type": "http", "method": "POST", "path": "/mcp", "user": user, "client": ("203.0.113.9", 5),
             "headers": [(b"host", b"127.0.0.1"), (b"x-forwarded-for", b"203.0.113.9")]}
    asyncio.run(RequirePrincipalMiddleware(inner)(scope, receive, send))
    assert sent[0]["status"] == 403


def test_a_session_belongs_to_the_key_that_opened_it(world):
    with running_app(world.app) as url:
        sid = open_session(url, world.ann_key)
        other = post(url, world.bob_key, body=TOOLS, sid=sid)
        assert other.status_code == 404 and "Session not found" in other.text
        assert post(url, world.ann_key, body=TOOLS, sid=sid).status_code == 200
        new_key, _, _ = rotate_user(world.root, "ann")
        assert post(url, new_key, body=TOOLS, sid=sid).status_code == 404


def test_a_revoked_key_is_refused_on_an_open_session(world):
    with running_app(world.app) as url:
        sid = open_session(url, world.ann_key)
        assert post(url, world.ann_key, body=TOOLS, sid=sid).status_code == 200
        revoke_user(world.root, "ann")
        refused = post(url, world.ann_key, body=TOOLS, sid=sid)
        assert refused.status_code == 401 and refused.json() == {"error": "authentication required"}


def test_revoking_a_key_ends_its_open_sessions(world, caplog):
    with running_app(world.app) as url:
        ann_sid, bob_sid = open_session(url, world.ann_key), open_session(url, world.bob_key)
        assert {ann_sid, bob_sid} <= set(instances(world))
        with caplog.at_level(logging.INFO, logger="taskmaster.serve"):
            revoke_user(world.root, "ann")
            deadline = time.monotonic() + 3
            while ann_sid in instances(world) and time.monotonic() < deadline:
                time.sleep(0.05)
        assert ann_sid not in instances(world) and bob_sid in instances(world)
        assert f"ended session {ann_sid[:8]} of revoked key {world.ann}" in [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("manager,stub", [
    (SimpleNamespace(_server_instances={}), False),
    (SimpleNamespace(_session_owners={}), False),
    (SimpleNamespace(_session_owners=[], _server_instances={}), False),
    (SimpleNamespace(_session_owners={}, _server_instances={}), True)])
def test_build_app_requires_session_internals(manager, stub, monkeypatch):
    if stub:
        class Stub:                       # an SDK whose transports no longer record an idle scope
            def __init__(self):
                pass
        monkeypatch.setattr(app_module, "StreamableHTTPServerTransport", Stub)
    with pytest.raises(RuntimeError, match="record their owner and idle scope"):
        check_session_internals(manager)


def test_session_internals_check_accepts_the_real_sdk(world):
    check_session_internals(SimpleNamespace(_session_owners={}, _server_instances={}))
    with running_app(world.app) as url:      # the lifespan runs the same check
        assert post(url, world.operator).status_code == 200


def test_sweep_errors_are_logged_not_raised(world, caplog):
    with running_app(world.app) as url:
        with caplog.at_level(logging.DEBUG, logger="taskmaster.serve"):
            world.registry.mode = "boom"
            deadline = time.monotonic() + 3
            while world.registry.mode and time.monotonic() < deadline:
                time.sleep(0.05)
            time.sleep(0.5)
        assert sum(r.levelno == logging.ERROR for r in caplog.records) == 1
        assert post(url, world.operator).status_code == 200


def test_a_transient_registry_error_never_ends_sessions(world, caplog):
    with running_app(world.app) as url:
        ann_sid, bob_sid = open_session(url, world.ann_key), open_session(url, world.bob_key)
        with caplog.at_level(logging.DEBUG, logger="taskmaster.serve"):
            world.registry.mode = "unavailable"
            time.sleep(1.0)
            world.registry.mode = None
        assert {ann_sid, bob_sid} <= set(instances(world))
        assert not [r for r in caplog.records if "ended session" in r.getMessage()]
