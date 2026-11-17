# User intent: every HTTP route refuses a request without a valid token, before its body is read, and the principal reaches the tools.
import asyncio
import json
import logging
import time

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware

from serve_helpers import call_tool, raw_initialize, running_app, toy_server
from taskmaster.serve import app as app_module
from taskmaster.serve.app import RequirePrincipalMiddleware, build_app, streamable_asgi
from taskmaster.serve.auth import KeyAuthenticator, OperatorTokenAuthenticator, ensure_operator_token, token_path
from taskmaster.serve.principals import PrincipalRegistry, add_user, principals_path

ACCEPT = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def token_file(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    token, _ = ensure_operator_token(tmp_path)
    return token, token_path(tmp_path)


def make_app(token_file, member=False, **kw):
    authenticator = OperatorTokenAuthenticator(token_file[1])
    if member:
        authenticator = KeyAuthenticator(authenticator, PrincipalRegistry(principals_path(token_file[1].parents[3])))
    return build_app(toy_server(), authenticator, **kw)


def test_unauthenticated_request_is_refused(token_file):
    with running_app(make_app(token_file)) as url:
        bad = [
            httpx.post(url + "/mcp", json={}, headers=ACCEPT),
            httpx.post(url + "/mcp", json={}, headers={**ACCEPT, "Authorization": "Bearer wrong"}),
            httpx.post(url + "/mcp", json={}, headers={**ACCEPT, "Authorization": "Basic Zm9vOmJhcg=="}),
            httpx.get(url + "/mcp", headers=ACCEPT),
            httpx.delete(url + "/mcp", headers=ACCEPT),
            httpx.post(url + "/anything", headers=ACCEPT),
        ]
        for response in bad:
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == 'Bearer realm="taskmaster"'
            assert response.json() == {"error": "authentication required"}
        with pytest.raises(httpx.HTTPStatusError) as exc:
            call_tool(url + "/mcp", None, "whoami")
        assert exc.value.response.status_code == 401


def test_refusal_never_reads_the_body():
    async def never_called(scope, receive, send):
        raise AssertionError("inner app reached")

    async def receive():
        raise AssertionError("body was read")

    sent = []

    async def send(message):
        sent.append(message)
    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": [], "client": ("1.2.3.4", 5)}
    asyncio.run(RequirePrincipalMiddleware(never_called)(scope, receive, send))
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 401


def test_valid_token_reaches_the_tool_with_the_operator_principal(token_file):
    with running_app(make_app(token_file)) as url:
        assert json.loads(call_tool(url + "/mcp", token_file[0], "whoami")) == {"handle": "operator", "role": "operator",
                                                                               "key_id": "operator"}


def test_valid_token_on_an_unknown_route_is_404(token_file):
    with running_app(make_app(token_file)) as url:
        r = httpx.post(url + "/rpc", headers={"Authorization": f"Bearer {token_file[0]}"})
        assert r.status_code == 404


def test_unknown_host_header_is_misdirected(token_file):
    # The operator token is host-local only (team-server-002), so the Host cases use a member key.
    key, _, _ = add_user(token_file[1].parents[3], "ann")
    bearer = {"Authorization": f"Bearer {key}"}
    with running_app(make_app(token_file, member=True)) as url:
        r = httpx.post(url + "/mcp", headers={**bearer, **ACCEPT, "Host": "evil.example"}, json={})
        assert r.status_code == 421
    with running_app(make_app(token_file, member=True, allowed_hosts=("tm.example.com",))) as url:
        r = httpx.post(url + "/mcp", headers={**bearer, **ACCEPT, "Host": "tm.example.com",
                                              "Content-Type": "application/json"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                           "protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "1"}}})
        assert r.status_code != 421
        operator = httpx.post(url + "/mcp", headers={"Authorization": f"Bearer {token_file[0]}", **ACCEPT,
                                                     "Host": "tm.example.com"}, json={})
        assert operator.status_code == 403


def test_refusals_are_logged_without_the_token(token_file, caplog):
    token = token_file[0]
    with running_app(make_app(token_file)) as url:
        with caplog.at_level(logging.WARNING, logger="taskmaster.serve"):
            httpx.post(url + "/mcp", headers={**ACCEPT, "Authorization": f"Bearer {token}x"}, json={})
            httpx.post(url + "/mcp", headers=ACCEPT, json={})
    msgs = [r.getMessage() for r in caplog.records if r.name == "taskmaster.serve"]
    invalid = [m for m in msgs if "invalid token" in m]
    assert len(invalid) == 1 and "/mcp" in invalid[0] and "127.0.0.1" in invalid[0] and token not in invalid[0]
    assert any("missing token" in m for m in msgs)


def test_a_crafted_path_cannot_forge_a_log_line(token_file, caplog):
    # review-gate I1: uvicorn percent-decodes the path, so a raw %0D%0A would start a fake record.
    with running_app(make_app(token_file)) as url:
        with caplog.at_level(logging.WARNING, logger="taskmaster.serve"):
            httpx.post(url + "/x%0D%0A2026-10-31 00:00:00 INFO taskmaster.serve: FORGED", headers=ACCEPT)
    msgs = [r.getMessage() for r in caplog.records if r.name == "taskmaster.serve"]
    forged = [m for m in msgs if "FORGED" in m]
    assert forged and all("\r" not in m and "\n" not in m for m in forged)
    assert "\\r\\n" in forged[0]


def test_log_safe_escapes_and_caps():
    assert app_module.log_safe("/a\r\nb\x00") == "/a\\r\\nb\\x00"
    assert app_module.log_safe(None) == ""
    assert app_module.log_safe("x" * 300).endswith("...") and len(app_module.log_safe("x" * 300)) == 259


def test_websocket_scopes_are_refused_without_reaching_the_app():
    # review-gate M1: only http (authenticated) and lifespan scopes may reach the inner app.
    async def never_called(scope, receive, send):
        raise AssertionError("inner app reached")

    async def receive():
        raise AssertionError("receive called")

    sent = []

    async def send(message):
        sent.append(message)
    scope = {"type": "websocket", "path": "/mcp", "headers": [], "client": ("1.2.3.4", 5)}
    asyncio.run(RequirePrincipalMiddleware(never_called)(scope, receive, send))
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_abandoned_sessions_expire(token_file):
    app = make_app(token_file, session_idle_seconds=1.0)
    with running_app(app) as url:
        for _ in range(5):
            assert raw_initialize(url + "/mcp", token_file[0]).status_code == 200
        manager = streamable_asgi(app, "/mcp").session_manager
        assert len(manager._server_instances) >= 1
        deadline = time.monotonic() + 6
        while len(manager._server_instances) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert len(manager._server_instances) == 0


def test_active_session_survives_its_idle_window(token_file):
    from fastmcp import Client
    app = make_app(token_file, session_idle_seconds=1.0)

    async def go(url):
        async with Client(url + "/mcp", auth=token_file[0]) as client:
            end = time.monotonic() + 2.5
            while time.monotonic() < end:
                await client.call_tool("whoami", {})
                await asyncio.sleep(0.4)
    with running_app(app) as url:
        asyncio.run(go(url))


def test_default_session_idle_timeout_is_set(token_file):
    app = make_app(token_file)
    with running_app(app):
        assert streamable_asgi(app, "/mcp").session_manager.session_idle_timeout == 1800.0


def test_build_app_fails_loudly_if_fastmcp_internals_change(token_file, monkeypatch):
    monkeypatch.setattr(app_module, "create_streamable_http_app", lambda **kw: Starlette())
    with pytest.raises(RuntimeError, match="no longer has the shape"):
        make_app(token_file)


def test_extra_middleware_sees_only_authenticated_requests(token_file):
    seen = []

    class Recorder:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                seen.append(scope["path"])
            await self.app(scope, receive, send)
    with running_app(make_app(token_file, middleware=[Middleware(Recorder)])) as url:
        httpx.post(url + "/refused", headers=ACCEPT, json={})
        httpx.post(url + "/accepted", headers={"Authorization": f"Bearer {token_file[0]}"})
    assert seen == ["/accepted"]


# --- team-server-006 Step 4: per-key limits and the whole stack ---

import threading

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from serve_helpers import TwoKeyAuthenticator, closed_within, connect, read_response
from taskmaster.serve.auth import AccessToken, Principal
from taskmaster.serve.config import ServeConfig
from taskmaster.serve.guard import PrincipalLimits, RequestGuard
from taskmaster.serve.limits import InFlight, KeyedLimiter, failure_budget
from taskmaster.serve.protocol import hardened_uvicorn_options

ALICE = Principal("alice", "member", "a1a1a1a1a1a1a1a1")
ALICE2 = Principal("alice", "member", "a2a2a2a2a2a2a2a2")
BOB = Principal("bob", "member", "b0b0b0b0b0b0b0b0")


def limited_stack(limiter=None, inflight=None, failures=None, release=None):
    from starlette.responses import JSONResponse
    server = toy_server()
    release = release or threading.Event()

    @server.custom_route("/wait", methods=["GET"])
    async def wait(request):
        while not release.is_set():
            await asyncio.sleep(0.02)
        return JSONResponse({"waited": True})

    @server.custom_route("/ping", methods=["GET"])
    async def ping(request):
        return JSONResponse({"pong": True})

    auth = TwoKeyAuthenticator({"ka": ALICE, "ka2": ALICE2, "kb": BOB})
    limiter = limiter if limiter is not None else KeyedLimiter(3, 0.0)
    inflight = inflight if inflight is not None else InFlight(2)
    failures = failures if failures is not None else failure_budget()
    app = RequestGuard(build_app(server, auth, middleware=[Middleware(PrincipalLimits, limiter=limiter,
                                                                       inflight=inflight)]),
                       max_body_bytes=2097152, body_timeout=30.0, failures=failures, authenticator=auth)
    return app, limiter, inflight, failures, release


def serve_hardened(app, tmp_path):
    return running_app(app, **hardened_uvicorn_options(ServeConfig(root=tmp_path)))


def _http_scope(headers, method="POST"):
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
            "path": "/mcp", "raw_path": b"/mcp", "query_string": b"", "root_path": "",
            "headers": [(b"host", b"127.0.0.1:7470"), *headers], "client": ("127.0.0.1", 5000),
            "server": ("127.0.0.1", 7470)}


def _run(app, scope, receive):
    sent = []

    async def send(message):
        sent.append(message)
    asyncio.run(app(scope, receive, send))
    return sent


async def _raising_receive():
    raise AssertionError("the body was read")


def test_unauthenticated_request_never_reaches_the_body_parser(tmp_path):
    app, *_ = limited_stack()
    with serve_hardened(app, tmp_path) as url:
        sock = connect(url)
        try:
            started = time.monotonic()
            sock.sendall(b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: application/json, text/event-stream\r\n"
                         b"Content-Type: application/json\r\nContent-Length: 1000\r\n\r\n")
            status, headers, _ = read_response(sock, timeout=1.0)
            assert status == 401 and time.monotonic() - started < 1.0 and headers["connection"] == "close"
        finally:
            sock.close()
    sent = _run(app, _http_scope([(b"content-length", b"1000"), (b"accept", b"application/json, text/event-stream")]),
                _raising_receive)
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 401


def test_rate_limit_trips_per_principal(tmp_path):
    app, *_ = limited_stack()
    with serve_hardened(app, tmp_path) as url:
        assert [raw_initialize(url + "/mcp", "ka").status_code for _ in range(3)] == [200, 200, 200]
        refused = raw_initialize(url + "/mcp", "ka")
        assert refused.status_code == 429 and refused.headers["retry-after"] == "3600"
        assert refused.json() == {"error": "rate limited"}
        assert raw_initialize(url + "/mcp", "kb").status_code == 200


def test_limits_are_keyed_by_key_id_not_handle_or_peer(tmp_path, caplog):
    app, limiter, inflight, failures, _ = limited_stack()
    with serve_hardened(app, tmp_path) as url, caplog.at_level(logging.INFO, logger="taskmaster.serve"):
        for _ in range(3):
            raw_initialize(url + "/mcp", "ka")
        for _ in range(25):
            assert raw_initialize(url + "/mcp", "ka").status_code == 429
        assert raw_initialize(url + "/mcp", "ka2").status_code == 200
        assert raw_initialize(url + "/mcp", "kb").status_code == 200
    assert failures.blocked("127.0.0.1") == 0
    lines = [r.getMessage() for r in caplog.records if "rate limited" in r.getMessage()]
    assert lines and "key=a1a1a1a1a1a1a1a1" in lines[0] and "principal=alice" in lines[0]


def test_inflight_cap_per_key(tmp_path):
    app, limiter, inflight, _, release = limited_stack(limiter=KeyedLimiter(100, 0.0))
    results = []
    with serve_hardened(app, tmp_path) as url:
        def hold():
            results.append(httpx.get(url + "/wait", headers={"Authorization": "Bearer ka"}, timeout=20).status_code)
        holders = [threading.Thread(target=hold) for _ in range(2)]
        for t in holders:
            t.start()
        deadline = time.monotonic() + 10
        while inflight.count(ALICE.key_id) < 2:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        third = httpx.get(url + "/wait", headers={"Authorization": "Bearer ka"}, timeout=5)
        assert third.status_code == 429 and third.json() == {"error": "too many concurrent requests"}
        assert third.headers["retry-after"] == "1"
        assert httpx.get(url + "/ping", headers={"Authorization": "Bearer kb"}).status_code == 200
        release.set()
        for t in holders:
            t.join(10)
        assert results == [200, 200]
        assert httpx.get(url + "/ping", headers={"Authorization": "Bearer ka"}).status_code == 200
    assert inflight.count(ALICE.key_id) == 0
    assert sum(limiter.take(ALICE.key_id) == 0.0 for _ in range(100)) == 97   # the refused one spent no token


def _user(subject, handle="alice"):
    return AuthenticatedUser(AccessToken(token="key:x", client_id=handle, subject=subject, scopes=[], expires_at=None,
                                         claims={"taskmaster_principal": handle, "taskmaster_role": "member",
                                                 "taskmaster_key_id": subject}))


def test_inflight_slot_held_by_a_stream_until_it_ends():
    inflight = InFlight(4)
    counts = []

    async def stream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"a", "more_body": True})
        counts.append(inflight.count(ALICE.key_id))
        await receive()
        await send({"type": "http.response.body", "body": b""})

    async def disconnect():
        return {"type": "http.disconnect"}
    scope = {"type": "http", "user": _user(ALICE.key_id)}
    _run(PrincipalLimits(stream, limiter=KeyedLimiter(10, 0.0), inflight=inflight), scope, disconnect)
    assert counts == [1] and inflight.count(ALICE.key_id) == 0

    async def boom(scope, receive, send):
        raise RuntimeError("x")
    with pytest.raises(RuntimeError):
        _run(PrincipalLimits(boom, limiter=KeyedLimiter(10, 0.0), inflight=inflight), scope, disconnect)
    assert inflight.count(ALICE.key_id) == 0


def test_rate_limited_request_body_is_not_read():
    app, limiter, *_ = limited_stack()
    for _ in range(3):
        limiter.take(ALICE.key_id)
    sent = _run(app, _http_scope([(b"authorization", b"Bearer ka"), (b"content-length", b"100"),
                                  (b"accept", b"application/json, text/event-stream"),
                                  (b"content-type", b"application/json")]), _raising_receive)
    start = sent[0]
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    assert start["status"] == 429 and headers["connection"] == "close"


@pytest.mark.parametrize("subject", [None, ""])
def test_unkeyed_request_is_500_not_passed(subject):
    called = []

    async def inner(scope, receive, send):
        called.append(scope)
    scope = {"type": "http"} if subject is None else {"type": "http", "user": _user(subject)}
    sent = _run(PrincipalLimits(inner, limiter=KeyedLimiter(10, 0.0), inflight=InFlight(2)), scope, _raising_receive)
    assert sent[0]["status"] == 500 and called == []


def test_refusal_log_carries_the_ref(tmp_path, caplog):
    app, *_ = limited_stack()
    with serve_hardened(app, tmp_path) as url, caplog.at_level(logging.WARNING, logger="taskmaster.serve"):
        response = httpx.post(url + "/mcp", headers=ACCEPT, json={})
    assert response.status_code == 401
    rid = response.headers["x-request-id"]
    lines = [r.getMessage() for r in caplog.records if "refused unauthenticated" in r.getMessage()]
    assert len(lines) == 1 and f"ref={rid}" in lines[0]


def test_refusals_are_read_while_the_body_is_still_arriving(tmp_path):
    # The lingering close (D3a follow-up): an early 401 or 429 closes the connection, but the client still reads
    # it while its body is in flight, instead of a reset (which Windows delivered before the answer).
    app, limiter, *_ = limited_stack()
    for _ in range(3):
        limiter.take(ALICE.key_id)
    body = b'{"jsonrpc": "2.0", "id": 1, "method": "ping", "pad": "' + b"x" * 32768 + b'"}'
    headers = {**ACCEPT, "Content-Type": "application/json"}
    with serve_hardened(app, tmp_path) as url:
        for _ in range(15):                          # 401s are charged; stay inside the budget of 20
            assert httpx.post(url + "/mcp", headers=headers, content=body).status_code == 401
        for _ in range(30):
            assert httpx.post(url + "/mcp", headers={**headers, "Authorization": "Bearer ka"},
                              content=body).status_code == 429
