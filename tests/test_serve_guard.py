# User intent: bodies are capped and timed before they are read, refused peers are throttled, every response carries a request id, and no unauthenticated request reaches the body parser.
import asyncio
import json
import logging
import re
import socket
import time

import pytest

from serve_helpers import TwoKeyAuthenticator, closed_within, connect, read_response, running_app
from taskmaster.serve.auth import Principal
from taskmaster.serve.config import ServeConfig
from taskmaster.serve.guard import REQUEST_ID_KEY, RequestGuard, request_id
from taskmaster.serve.limits import KeyedLimiter, failure_budget
from taskmaster.serve.protocol import hardened_uvicorn_options

ALICE = Principal("alice", "member", "a1a1a1a1a1a1a1a1")
MAX = 2097152


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_guard(inner, *, body_timeout=30.0, failures=None, authenticator=None, max_body_bytes=MAX):
    return RequestGuard(inner, max_body_bytes=max_body_bytes, body_timeout=body_timeout,
                        failures=failures if failures is not None else failure_budget(),
                        authenticator=authenticator or TwoKeyAuthenticator({"ka": ALICE}))


def scope_for(method="POST", path="/mcp", headers=(), client=("203.0.113.9", 5000)):
    return {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
            "headers": [(n.lower().encode(), v.encode()) for n, v in headers], "client": client}


async def _forbidden_receive():
    raise AssertionError("the body was read")


def call(app, scope, receive=_forbidden_receive):
    sent = []

    async def send(message):
        sent.append(message)
    asyncio.run(app(scope, receive, send))
    return sent


def start_of(sent):
    return next(m for m in sent if m["type"] == "http.response.start")


def headers_of(message):
    return {k.decode().lower(): v.decode() for k, v in message["headers"]}


def body_of(sent):
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def answering(status, headers=()):
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope)
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-length", b"2"), *headers]})
        await send({"type": "http.response.body", "body": b"no"})
    inner.calls = calls
    return inner


async def reader(scope, receive, send):
    if scope["type"] == "lifespan":
        await _lifespan(receive, send)
        return
    total, seen = 0, []
    while True:
        message = await receive()
        seen.append(message["type"])
        if message["type"] == "http.disconnect":
            reader.seen = seen
            return
        total += len(message.get("body", b""))
        if not message.get("more_body"):
            break
    reader.seen = seen
    body = json.dumps({"read": total, "rid": scope.get(REQUEST_ID_KEY)}).encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def _lifespan(receive, send):
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


def lifespan_wrapped(inner):
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            await _lifespan(receive, send)
            return
        await inner(scope, receive, send)
    return app


def served(inner, tmp_path, *, config=None, **kw):
    cfg = ServeConfig(root=tmp_path, header_timeout=5.0, **(config or {}))
    return running_app(make_guard(lifespan_wrapped(inner), **kw), **hardened_uvicorn_options(cfg))


# --- body size ---

def test_oversized_body_is_refused_before_it_is_read(tmp_path):
    inner = answering(200)
    sent = call(make_guard(inner), scope_for(headers=[("content-length", "3000000")]))
    assert start_of(sent)["status"] == 413 and inner.calls == []
    assert json.loads(body_of(sent)) == {"error": "request body too large", "limit": MAX}
    assert headers_of(start_of(sent))["connection"] == "close"
    with served(reader, tmp_path) as url:
        sock = connect(url)
        try:
            started = time.monotonic()
            sock.sendall(b"POST /mcp HTTP/1.1\r\nHost: x\r\nContent-Length: 3000000\r\n\r\n")
            status, headers, _ = read_response(sock, timeout=1.0)
            assert status == 413 and time.monotonic() - started < 1.0
        finally:
            sock.close()


def test_streamed_body_over_the_cap_is_refused(tmp_path):
    with served(reader, tmp_path) as url:
        sock = connect(url)
        try:
            sock.sendall(b"POST /mcp HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n")
            chunk = b"a" * (1024 * 1024)
            try:
                for _ in range(3):
                    sock.sendall(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                sock.sendall(b"0\r\n\r\n")
            except OSError:
                pass                     # the server may close while the rest is in flight
            try:
                status, headers, _ = read_response(sock)
                assert status == 413 and headers["connection"] == "close"
            except (ConnectionResetError, ConnectionAbortedError):
                # The server answered and closed while chunks were still in flight; the RST that the late
                # chunks draw can reach this socket before its 413 is read (Windows). The ASGI-direct test
                # below pins the 413 itself.
                pass
            assert closed_within(sock, 3.0)
        finally:
            sock.close()
    assert reader.seen[-1] == "http.disconnect"


def test_streamed_body_over_the_cap_asgi_direct():
    messages = [{"type": "http.request", "body": b"a" * 600, "more_body": True} for _ in range(3)]

    async def receive():
        return messages.pop(0)
    sent = call(make_guard(reader, max_body_bytes=1000), scope_for(headers=[("transfer-encoding", "chunked")]), receive)
    assert start_of(sent)["status"] == 413
    assert reader.seen == ["http.request", "http.disconnect"]      # the second chunk was never passed on
    assert len(messages) == 1


def test_complete_head_over_the_header_cap_is_refused():
    inner = answering(200)
    sent = call(make_guard(inner), scope_for(method="GET", headers=[("x-big", "a" * 20000)]))
    assert start_of(sent)["status"] == 431 and inner.calls == []
    assert json.loads(body_of(sent)) == {"error": "request headers too large"}


def test_slow_body_gets_408(tmp_path):
    with served(reader, tmp_path, body_timeout=1.0) as url:
        sock = connect(url)
        try:
            sock.sendall(b"POST /mcp HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\n\r\n12345")
            started = time.monotonic()
            status, headers, body = read_response(sock, timeout=3.0)
            elapsed = time.monotonic() - started
            assert status == 408 and headers["connection"] == "close" and 0.9 <= elapsed <= 2.0
            assert json.loads(body) == {"error": "request body timed out"}
        finally:
            sock.close()


def test_sse_wait_is_not_timed(tmp_path):
    async def streamer(scope, receive, send):
        message = await receive()
        assert not message.get("more_body")
        waiter = asyncio.ensure_future(receive())
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
        for i in range(7):
            await send({"type": "http.response.body", "body": b"data: %d\n\n" % i, "more_body": True})
            await asyncio.sleep(0.3)
        assert not waiter.done()
        waiter.cancel()
        await send({"type": "http.response.body", "body": b""})
    with served(streamer, tmp_path, body_timeout=0.5) as url:
        sock = connect(url)
        try:
            sock.sendall(b"GET /mcp HTTP/1.1\r\nHost: x\r\n\r\n")
            sock.settimeout(5)
            data = b""
            while b"0\r\n\r\n" not in data:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
            assert b" 200 " in data.split(b"\r\n", 1)[0] and b"data: 6" in data and b"408" not in data
        finally:
            sock.close()


@pytest.mark.parametrize("headers", [[("content-length", "abc")], [("content-length", "-1")],
                                     [("content-length", "10"), ("transfer-encoding", "chunked")],
                                     [("content-length", "10"), ("content-length", "11")]])
def test_bad_lengths_are_400(headers):
    inner = answering(200)
    sent = call(make_guard(inner), scope_for(headers=headers))
    assert start_of(sent)["status"] == 400 and inner.calls == []
    assert json.loads(body_of(sent)) == {"error": "bad request"}


# --- early answers close the connection (design D3a) ---

def _trickle_after_early_answer(url, then):
    sock = connect(url)
    try:
        sock.sendall(b"POST /mcp HTTP/1.1\r\nHost: x\r\nContent-Length: 1000\r\n\r\n0123456789")
        status, headers, _ = read_response(sock)
        assert status == 401
        answered = time.monotonic()
        closed = then(sock)
        return headers, closed, time.monotonic() - answered
    finally:
        sock.close()


def _one_byte_every(seconds, total):
    def then(sock):
        deadline = time.monotonic() + total
        while time.monotonic() < deadline:
            try:
                sock.sendall(b"x")
            except OSError:
                return True
            if closed_within(sock, seconds):
                return True
        return False
    return then


def test_early_answer_then_trickled_body_is_closed(tmp_path):
    with served(answering(401, [(b"connection", b"keep-alive")]), tmp_path) as url:
        headers, closed, elapsed = _trickle_after_early_answer(url, _one_byte_every(1.5, 4.0))
        assert headers["connection"] == "close" and closed and elapsed < 3.0
        headers, closed, elapsed = _trickle_after_early_answer(url, lambda s: (s.sendall(b"x"), closed_within(s, 3.0))[1])
        assert closed and elapsed < 3.0


def test_plain_uvicorn_keeps_a_trickled_connection_open():
    # The control: without the guard the same trickle pins the connection (the review spike's hole).
    with running_app(lifespan_wrapped(answering(401)), http="h11", timeout_keep_alive=1) as url:
        headers, closed, _ = _trickle_after_early_answer(url, _one_byte_every(1.5, 6.0))
        assert not closed


def test_complete_bodies_keep_alive(tmp_path):
    async def read_then_401(scope, receive, send):
        while (await receive()).get("more_body"):
            pass
        await answering(401)(scope, receive, send)
    with served(read_then_401, tmp_path) as url:
        sock = connect(url)
        try:
            sock.sendall(b"POST /mcp HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\n\r\n0123456789")
            status, headers, _ = read_response(sock)
            assert status == 401 and "connection" not in headers
            sock.sendall(b"GET /mcp HTTP/1.1\r\nHost: x\r\n\r\n")
            assert read_response(sock)[0] == 401
        finally:
            sock.close()
    with served(answering(401), tmp_path) as url:
        sock = connect(url)
        try:
            sock.sendall(b"GET /mcp HTTP/1.1\r\nHost: x\r\n\r\n")
            status, headers, _ = read_response(sock)
            assert status == 401 and "connection" not in headers
            sock.sendall(b"GET /mcp HTTP/1.1\r\nHost: x\r\n\r\n")
            assert read_response(sock)[0] == 401
        finally:
            sock.close()


# --- request ids ---

def test_every_response_has_a_request_id(tmp_path):
    hex12 = re.compile(r"^[0-9a-f]{12}$")
    with served(reader, tmp_path) as url:
        for raw, status in ((b"POST /a HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\nX-Request-Id: attacker\r\n\r\nab", 200),
                            (b"POST /a HTTP/1.1\r\nHost: x\r\nContent-Length: 3000000\r\n\r\n", 413)):
            sock = connect(url)
            try:
                sock.sendall(raw)
                got, headers, body = read_response(sock)
                assert got == status and hex12.match(headers["x-request-id"])
                if status == 200:
                    assert json.loads(body)["rid"] == headers["x-request-id"]
            finally:
                sock.close()
    sent = call(make_guard(answering(404)), scope_for(method="GET"))
    assert start_of(sent)["status"] == 404 and hex12.match(headers_of(start_of(sent))["x-request-id"])
    assert request_id({REQUEST_ID_KEY: "abc"}) == "abc" and request_id({}) is None


# --- the failure budget (design D6) ---

def test_failure_budget_throttles_a_refused_peer():
    clock = Clock()
    inner = answering(401)
    guard = make_guard(inner, failures=failure_budget(clock=clock))
    statuses = [start_of(call(guard, scope_for()))["status"] for _ in range(21)]
    assert statuses == [401] * 20 + [429]
    blocked = start_of(call(guard, scope_for()))
    assert "retry-after" in headers_of(blocked) and len(inner.calls) == 20
    assert start_of(call(guard, scope_for(client=("198.51.100.4", 1))))["status"] == 401
    clock.now += 3.0
    assert start_of(call(guard, scope_for()))["status"] == 401
    assert start_of(call(guard, scope_for()))["status"] == 429


def test_failure_budget_ignores_successes():
    guard = make_guard(answering(200), failures=failure_budget(clock=Clock()))
    assert {start_of(call(guard, scope_for(method="GET")))["status"] for _ in range(100)} == {200}


@pytest.mark.parametrize("status", [400, 408, 413, 421])
def test_authenticated_refusals_are_not_charged(status):
    failures = failure_budget(clock=Clock())
    guard = make_guard(answering(status), failures=failures)
    for _ in range(30):
        assert start_of(call(guard, scope_for(method="GET", headers=[("authorization", "Bearer ka")])))["status"] == status
    assert failures.blocked("203.0.113.9") == 0
    for headers in ([("authorization", "Bearer wrong")], []):
        failures = failure_budget(clock=Clock())
        guard = make_guard(answering(status), failures=failures)
        got = [start_of(call(guard, scope_for(method="GET", headers=headers)))["status"] for _ in range(21)]
        assert got == [status] * 20 + [429]


def test_guard_level_refusal_with_a_valid_key_is_not_charged():
    failures = failure_budget(clock=Clock())
    guard = make_guard(answering(200), failures=failures)
    for _ in range(25):
        sent = call(guard, scope_for(headers=[("authorization", "Bearer ka"), ("content-length", "3000000")]))
        assert start_of(sent)["status"] == 413
    assert failures.blocked("203.0.113.9") == 0


def test_401_is_charged_even_with_a_bearer_header():
    failures = failure_budget(clock=Clock())
    guard = make_guard(answering(401), failures=failures)
    got = [start_of(call(guard, scope_for(method="GET", headers=[("authorization", "Bearer ka")])))["status"]
           for _ in range(21)]
    assert got == [401] * 20 + [429]


def test_unauthenticated_check_runs_only_on_charging_statuses():
    for status in (200, 404, 429, 403):
        auth = TwoKeyAuthenticator({"ka": ALICE})
        guard = make_guard(answering(status), authenticator=auth)
        call(guard, scope_for(method="GET", headers=[("authorization", "Bearer ka")]))
        assert auth.calls == []
    auth = TwoKeyAuthenticator({"ka": ALICE})
    call(make_guard(answering(400), authenticator=auth), scope_for(method="GET", headers=[("authorization", "Bearer ka")]))
    assert auth.calls == ["ka"]


def test_ipv6_peers_share_a_64():
    guard = make_guard(answering(401), failures=failure_budget(clock=Clock()))
    got = [start_of(call(guard, scope_for(client=(f"2001:db8:1:2::{i + 1:x}", 1))))["status"] for i in range(21)]
    assert got == [401] * 20 + [429]
    assert start_of(call(guard, scope_for(client=("2001:db8:1:3::1", 1))))["status"] == 401
    assert start_of(call(guard, scope_for(client=("203.0.113.9", 1))))["status"] == 401


def test_exhaustion_is_logged_once(caplog):
    guard = make_guard(answering(401), failures=failure_budget(clock=Clock()))
    with caplog.at_level(logging.INFO, logger="taskmaster.serve"):
        for _ in range(50):
            call(guard, scope_for())
    assert sum("exhausted its failure budget" in r.getMessage() for r in caplog.records) == 1


def test_logged_values_are_escaped(caplog):
    guard = make_guard(answering(200))
    with caplog.at_level(logging.INFO, logger="taskmaster.serve"):
        call(guard, scope_for(path="/x\r\nforged", headers=[("content-length", "3000000")], client=("1.2.3.4\nforged", 1)))
    lines = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert len(lines) == 1 and "\n" not in lines[0] and "\r" not in lines[0] and "413" in lines[0]
    assert "ref=" in lines[0]


def test_lifespan_passes_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope)
    scope = {"type": "lifespan", "asgi": {}}
    asyncio.run(make_guard(inner)(scope, _forbidden_receive, None))
    assert seen == [scope] and REQUEST_ID_KEY not in scope


def test_websocket_scopes_never_reach_the_app():
    inner = answering(200)
    sent = call(make_guard(inner), {"type": "websocket", "path": "/mcp", "headers": [], "client": ("1.2.3.4", 1)})
    assert inner.calls == [] and sent == [{"type": "websocket.close", "code": 1008}]


def test_app_sends_after_a_guard_answer_are_dropped():
    async def slow_reader(scope, receive, send):
        message = await receive()
        assert message["type"] == "http.disconnect"
        await send({"type": "http.response.start", "status": 500, "headers": []})
        await send({"type": "http.response.body", "body": b"late"})

    async def receive():
        await asyncio.sleep(5)
    sent = call(make_guard(slow_reader, body_timeout=0.2), scope_for(headers=[("content-length", "5")]), receive)
    assert [m.get("status") for m in sent if m["type"] == "http.response.start"] == [408]
    assert b"late" not in body_of(sent)


# --- lingering close after an early answer (D3a follow-up) ---------------------------------------

def _early_401_then(url, send_after):
    sock = connect(url)
    try:
        sock.sendall(b"POST /mcp HTTP/1.1\r\nHost: x\r\nContent-Length: 2000000\r\n\r\n" + b"a" * 1000)
        status, headers, _ = read_response(sock)
        assert status == 401 and headers["connection"] == "close"
        answered = time.monotonic()
        return send_after(sock), time.monotonic() - answered
    finally:
        sock.close()


def test_a_client_still_sending_reads_the_early_answer(tmp_path):
    import httpx
    with served(answering(401), tmp_path) as url:
        for _ in range(15):                          # 401s are charged; stay inside the budget of 20
            assert httpx.post(url + "/mcp", content=b"x" * 32768).status_code == 401


def server_gone_within(sock, seconds, every=0.2):
    """With the lingering close the client sees EOF (the server's half-close) at once, so EOF proves nothing.
    The server has really closed once a byte sent to it fails (its stack answers with a reset)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            sock.sendall(b"x")
        except OSError:
            return True
        time.sleep(every)
    return False


def test_linger_time_cap_cuts_a_slow_drip(tmp_path):
    with served(answering(401), tmp_path, config={"linger_seconds": 1.0}) as url:
        gone, elapsed = _early_401_then(url, lambda sock: server_gone_within(sock, 5.0))
        assert gone and 0.7 <= elapsed <= 2.5


def test_linger_byte_cap_cuts_a_fast_sender(tmp_path):
    def flood(sock):
        try:
            for _ in range(64):
                sock.sendall(b"y" * 16384)          # 1 MiB, far past the 64 KiB cap
        except OSError:
            return True
        return server_gone_within(sock, 3.0, every=0.05)
    with served(answering(401), tmp_path, config={"linger_seconds": 10.0, "linger_bytes": 65536}) as url:
        gone, elapsed = _early_401_then(url, flood)
        assert gone and elapsed < 4.0               # cut off by bytes, long before the 10 s time cap


def test_linger_off_closes_at_once(tmp_path):
    with served(answering(401), tmp_path, config={"linger_seconds": 0}) as url:
        gone, elapsed = _early_401_then(url, lambda sock: server_gone_within(sock, 3.0, every=0.05))
        assert gone and elapsed < 1.0


def test_default_linger_still_ends_a_trickle(tmp_path):
    # D3a's guarantee with the default caps: one byte every 0.5 s cannot hold the connection past ~2 s.
    with served(answering(401), tmp_path) as url:
        gone, elapsed = _early_401_then(url, lambda sock: server_gone_within(sock, 6.0, every=0.5))
        assert gone and elapsed <= 4.0



def test_blocked_peers_add_no_log_lines(caplog):
    # 006 review I2 (design D6): silent while the budget stays empty.
    guard = make_guard(answering(401), failures=failure_budget(clock=Clock()))
    with caplog.at_level(logging.INFO, logger="taskmaster.serve"):
        for _ in range(20):
            call(guard, scope_for())
        before = len(caplog.records)
        for _ in range(50):
            assert start_of(call(guard, scope_for()))["status"] == 429
    assert len(caplog.records) == before
