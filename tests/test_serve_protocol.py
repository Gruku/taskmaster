# User intent: a slow-header client is cut off, oversize headers are refused, normal keep-alive still works, and the hardening options cannot be overridden.
import time

import pytest

from serve_helpers import closed_within, connect, read_response, running_app
from taskmaster.serve.config import KEEP_ALIVE_SECONDS, ServeConfig
from taskmaster.serve.daemon import ServeDaemon
from taskmaster.serve.protocol import DeadlineH11Protocol, HARDENED_KEYS, hardened_uvicorn_options

SEEN = []


async def asgi_ok(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    SEEN.append((scope["type"], scope.get("client")))
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]})
    await send({"type": "http.response.body", "body": b"ok"})


def hardened(tmp_path, **kw):
    return hardened_uvicorn_options(ServeConfig(root=tmp_path, header_timeout=1.0, **kw))


def trickle_headers(sock, first: bytes, seconds: float, closed_by: float) -> float | None:
    """Send `first`, then one header line every 0.4 s; return when the server closed, or None."""
    started = time.monotonic()
    sock.sendall(first)
    while time.monotonic() - started < seconds:
        if closed_within(sock, 0.4):
            return time.monotonic() - started
        try:
            sock.sendall(b"X-A: b\r\n")
        except OSError:
            return time.monotonic() - started
    return None


def test_slow_header_client_is_cut_off(tmp_path):
    with running_app(asgi_ok, **hardened(tmp_path)) as url:
        sock = connect(url)
        try:
            started = time.monotonic()
            closed = trickle_headers(sock, b"GET / HTTP/1.1\r\nHost: x\r\n", 4.0, 2.5)
            assert closed is not None and time.monotonic() - started < 2.5
        finally:
            sock.close()


def test_plain_uvicorn_never_cuts_a_slow_header_client():
    # The design D2 premise: without the protocol subclass the trickle is never cut.
    with running_app(asgi_ok, http="h11", timeout_keep_alive=1) as url:
        sock = connect(url)
        try:
            assert trickle_headers(sock, b"GET / HTTP/1.1\r\nHost: x\r\n", 3.0, 3.0) is None
        finally:
            sock.close()


def test_complete_requests_on_one_connection_still_work(tmp_path):
    with running_app(asgi_ok, **hardened(tmp_path)) as url:
        sock = connect(url)
        try:
            sock.sendall(b"GET /a HTTP/1.1\r\nHost: x\r\n\r\n")
            assert read_response(sock)[0] == 200
            time.sleep(0.5)
            sock.sendall(b"GET /b HTTP/1.1\r\nHost: x\r\n\r\n")
            assert read_response(sock)[0] == 200
        finally:
            sock.close()


def test_slow_headers_on_a_second_request_are_cut_off(tmp_path):
    with running_app(asgi_ok, **hardened(tmp_path)) as url:
        sock = connect(url)
        try:
            sock.sendall(b"GET /a HTTP/1.1\r\nHost: x\r\n\r\n")
            assert read_response(sock)[0] == 200
            answered = time.monotonic()
            time.sleep(0.1)
            closed = trickle_headers(sock, b"GET /b HTTP/1.1\r\n", 4.0, 2.5)
            assert closed is not None and time.monotonic() - answered < 1.0 + 1.5
        finally:
            sock.close()


def test_idle_keep_alive_after_a_response_is_closed(tmp_path):
    with running_app(asgi_ok, **hardened_uvicorn_options(ServeConfig(root=tmp_path))) as url:
        sock = connect(url)
        try:
            sock.sendall(b"GET /a HTTP/1.1\r\nHost: x\r\n\r\n")
            assert read_response(sock)[0] == 200
            assert closed_within(sock, KEEP_ALIVE_SECONDS + 1.5)
        finally:
            sock.close()


def test_oversize_headers_are_refused(tmp_path):
    # h11's limit applies to an incomplete head, so this head is never finished. A complete head that
    # arrives in one read gets past h11; RequestGuard refuses that one (431, test_serve_guard).
    with running_app(asgi_ok, **hardened(tmp_path)) as url:
        sock = connect(url)
        try:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nX-Big: " + b"a" * 20480)   # never finished
            status, headers, _ = read_response(sock)
            assert status == 400 and headers.get("connection") == "close"
            assert closed_within(sock, 2)
        finally:
            sock.close()


def test_websocket_upgrade_is_not_honoured(tmp_path):
    SEEN.clear()
    with running_app(asgi_ok, **hardened(tmp_path)) as url:
        sock = connect(url)
        try:
            sock.sendall(b"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n")
            assert read_response(sock)[0] == 200
        finally:
            sock.close()
    assert [kind for kind, _ in SEEN] == ["http"]


def test_forwarded_trust_is_pinned(tmp_path, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    options = hardened(tmp_path)
    assert options["forwarded_allow_ips"] == ["127.0.0.1", "::1"]
    SEEN.clear()
    with running_app(asgi_ok, **options) as url:
        sock = connect(url)
        try:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nX-Forwarded-For: 198.51.100.1, 203.0.113.9\r\n\r\n")
            assert read_response(sock)[0] == 200
        finally:
            sock.close()
    assert SEEN[0][1][0] == "203.0.113.9"


def test_hardened_options_shape(tmp_path):
    options = hardened_uvicorn_options(ServeConfig(root=tmp_path, max_connections=64))
    assert set(options) == HARDENED_KEYS
    assert issubclass(options["http"], DeadlineH11Protocol) and options["http"].header_timeout == 10.0
    assert options["ws"] == "none" and options["limit_concurrency"] == 64
    assert options["h11_max_incomplete_event_size"] == 16384 and options["proxy_headers"] is True
    assert options["timeout_keep_alive"] == 5
    assert options["http"].linger_seconds == 2.0 and options["http"].linger_bytes == 65536


@pytest.mark.parametrize("key", sorted(HARDENED_KEYS))
def test_hardened_keys_cannot_be_overridden(tmp_path, key):
    with pytest.raises(ValueError, match=key):
        ServeDaemon(ServeConfig(root=tmp_path), uvicorn_options={key: "x"})
