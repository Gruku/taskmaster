# User intent: the read client sends the key only to its own origin and never through a redirect or a proxy in
# plaintext, answers within its whole-request deadline whatever DNS or the peer does, and names every failure with
# one kind, serving stale data only where the view's policy allows (team-server-007 D10, D12, D13; review I2).
from __future__ import annotations

import http.server
import json
import socket
import threading
import time

import pytest

from taskmaster.remote import cache as cache_mod
from taskmaster.remote.cache import Cache
from taskmaster.remote.client import Client, ReadUnavailable, hook_deadline
from taskmaster.remote.keys import Key
from taskmaster.remote.locate import server_ref

KEY = "tm_u_0123456789abcdef_" + "A" * 43
EPS = 0.75


def envelope(view="paths", data=None, revision=3, store_id="s1"):
    return {"v": 1, "view": view, "store_id": store_id, "revision": revision, "generated_at": "t",
            "data": data if data is not None else {"listed": []}}


class Script:
    """What the fake server answers next: (status, headers, body bytes)."""

    def __init__(self):
        self.replies = []
        self.seen = []

    def push(self, status=200, body=None, headers=None, raw=None, delay=0.0):
        payload = raw if raw is not None else json.dumps(body if body is not None else envelope()).encode()
        self.replies.append((status, {"Content-Type": "application/json", **(headers or {})}, payload, delay))


@pytest.fixture
def server():
    script = Script()

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            script.seen.append({"path": self.path, "method": self.command, "auth": self.headers.get("Authorization"),
                                "body": body, "host": self.headers.get("Host")})
            status, headers, payload, delay = script.replies.pop(0) if script.replies else \
                (200, {"Content-Type": "application/json"}, json.dumps(envelope()).encode(), 0.0)
            time.sleep(delay)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = _answer

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    script.url = f"http://127.0.0.1:{httpd.server_address[1]}/mcp"
    yield script
    httpd.shutdown()
    httpd.server_close()


def key():
    return Key(KEY, "0123456789abcdef", "file:1:2", "file")


def client_for(url, tmp_path=None, **kwargs):
    cache = Cache(server_ref(url).server_id, "0123456789abcdef", base=tmp_path / "c") if tmp_path else None
    return Client(server_ref(url), key(), cache, **kwargs)


def kind_of(fn):
    with pytest.raises(ReadUnavailable) as raised:
        fn()
    return raised.value


def test_a_view_is_read_with_the_key(server):
    server.push(body=envelope(data={"listed": [{"kind": "bug", "id": "B-1", "status": "open"}]}))
    result = client_for(server.url).read("paths", {"path": "a b.py"})
    assert result.source == "server" and result.data["listed"][0]["id"] == "B-1" and result.revision == 3
    seen = server.seen[0]
    assert seen["auth"] == f"Bearer {KEY}" and seen["method"] == "GET"
    assert seen["path"] == "/read/v1/paths?path=a+b.py"


def test_tools_is_a_post_with_the_calls(server):
    server.push(body=envelope("tools", {"results": []}))
    calls = [{"tool": "backlog_get_task", "args": {"task_id": "x"}}]
    client_for(server.url).read("tools", {"calls": calls})
    assert server.seen[0]["method"] == "POST" and json.loads(server.seen[0]["body"]) == {"calls": calls}


@pytest.mark.parametrize("status, kind", [
    (400, "bad-request"), (401, "refused"), (403, "refused"), (404, "version"), (408, "unreachable"),
    (411, "bad-request"), (413, "bad-request"), (421, "bad-request"), (429, "busy"), (431, "bad-request"),
    (500, "server"), (503, "server"), (302, "bad-response"), (418, "bad-response")])
def test_every_status_has_one_kind(server, status, kind):
    server.push(status, {"v": 1, "error": {"code": "x", "message": "nope", "ref": "abc"}},
                headers={"Location": "http://127.0.0.2/x", "Retry-After": "3"})
    failure = kind_of(lambda: client_for(server.url).read("paths", {"path": "a"}))
    assert failure.kind == kind
    if kind == "busy":
        assert failure.retry_after == 3.0


def test_a_plain_404_says_the_server_is_too_old(server):
    server.push(404, raw=b"Not Found", headers={"Content-Type": "text/plain"})
    failure = kind_of(lambda: client_for(server.url).read("paths", {"path": "a"}))
    assert failure.kind == "version" and "update the server" in failure.message


def test_a_redirect_is_never_followed(server):
    target = Script()
    hits = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass
    other = http.server.HTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    server.push(302, raw=b"", headers={"Location": f"http://127.0.0.1:{other.server_address[1]}/x"})
    assert kind_of(lambda: client_for(server.url).read("paths", {"path": "a"})).kind == "bad-response"
    assert hits == []
    other.shutdown()
    other.server_close()


def test_a_non_v1_or_non_json_reply_is_a_bad_response(server):
    server.push(raw=b"<html>", headers={"Content-Type": "text/html"})
    server.push(body={"v": 2, "view": "paths", "store_id": "s", "revision": 1, "data": {}})
    server.push(body=envelope(view="merge-gate"))
    c = client_for(server.url)
    for _ in range(3):
        assert kind_of(lambda: c.read("paths", {"path": "a"})).kind == "bad-response"


def test_an_oversize_reply_is_refused(server):
    server.push(body=envelope(data={"blob": "x" * (300 * 1024)}))
    assert kind_of(lambda: client_for(server.url).read("paths", {"path": "a"})).kind == "bad-response"


def test_a_loopback_request_never_goes_through_a_proxy(server, monkeypatch):
    proxied = []

    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            proxied.append(self.path)
            self.send_response(502)
            self.end_headers()

        def log_message(self, *args):
            pass
    proxy = http.server.HTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{proxy.server_address[1]}")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert client_for(server.url).read("paths", {"path": "a"}).source == "server"
    assert proxied == []
    proxy.shutdown()
    proxy.server_close()


# ── deadline (review I2) ────────────────────────────────────────


def _timed(fn):
    started = time.monotonic()
    failure = kind_of(fn)
    return failure, time.monotonic() - started


def test_a_stalled_resolver_ends_at_the_deadline(server, monkeypatch):
    def stall(*args, **kwargs):
        time.sleep(10)
        raise OSError("never")
    monkeypatch.setattr(socket, "getaddrinfo", stall)
    failure, took = _timed(lambda: client_for(server.url).read("paths", {"path": "a"},
                                                               deadline=time.monotonic() + 1.0))
    assert failure.kind == "unreachable" and took < 1.0 + EPS


def _drip_server(head: bytes, drip: bytes):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    stop = threading.Event()

    def serve():
        conn, _ = listener.accept()
        try:
            conn.recv(65536)
            conn.sendall(head)
            for byte in drip:
                if stop.is_set():
                    break
                conn.sendall(bytes([byte]))
                time.sleep(0.4)
        except OSError:
            pass
        finally:
            conn.close()
    threading.Thread(target=serve, daemon=True).start()
    return f"http://127.0.0.1:{listener.getsockname()[1]}/mcp", stop, listener


def test_dripping_headers_end_at_the_deadline():
    url, stop, listener = _drip_server(b"HTTP/1.1 200 OK\r\n", b"X-Slow: " + b"a" * 100)
    failure, took = _timed(lambda: client_for(url).read("paths", {"path": "a"}, deadline=time.monotonic() + 1.5))
    stop.set()
    listener.close()
    assert failure.kind == "unreachable" and took < 1.5 + EPS


def test_a_dripping_body_ends_at_the_deadline():
    head = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n"
    url, stop, listener = _drip_server(head, b"{" + b" " * 499)
    failure, took = _timed(lambda: client_for(url).read("paths", {"path": "a"}, deadline=time.monotonic() + 1.5))
    stop.set()
    listener.close()
    assert failure.kind == "unreachable" and took < 1.5 + EPS


def test_a_hooks_absolute_cap_wins_over_the_view_budget(server):
    started = time.monotonic() - 7.5           # the hook process began 7.5 s ago
    deadline = hook_deadline(5.0, started=started)
    assert deadline <= started + 8.0 + 1e-6
    server.push(delay=2.0)
    failure, took = _timed(lambda: client_for(server.url).read("merge-gate", {"branch": "b"}, deadline=deadline))
    assert failure.kind == "unreachable" and took < 0.5 + EPS


def test_no_time_left_asks_nothing(server):
    failure = kind_of(lambda: client_for(server.url).read("paths", {"path": "a"}, deadline=time.monotonic() - 1))
    assert failure.kind == "unreachable" and server.seen == []


def test_a_closed_port_is_unreachable():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    assert kind_of(lambda: client_for(f"http://127.0.0.1:{port}/mcp").read("paths", {"path": "a"})).kind == \
        "unreachable"


# ── cache interplay ─────────────────────────────────────────────


def test_a_fresh_hit_skips_the_network(server, tmp_path):
    c = client_for(server.url, tmp_path)
    c.read("paths", {"path": "a"})
    again = c.read("paths", {"path": "a"})
    assert again.source == "cache" and len(server.seen) == 1
    assert c.read("paths", {"path": "a"}, fresh=True).source == "server" and len(server.seen) == 2


def test_stale_is_served_for_transient_failures_only(server, tmp_path, monkeypatch):
    c = client_for(server.url, tmp_path)
    c.read("paths", {"path": "a"})
    monkeypatch.setitem(cache_mod.CACHE_POLICY, "paths", cache_mod.Policy(0, 600))
    server.push(503, {"v": 1, "error": {"code": "store-unavailable", "message": "x"}})
    assert c.read("paths", {"path": "a"}).source == "stale"
    server.push(400, {"v": 1, "error": {"code": "bad-request", "message": "x"}})
    assert kind_of(lambda: c.read("paths", {"path": "a"})).kind == "bad-request"


def test_merge_gate_is_never_served_from_cache(server, tmp_path):
    c = client_for(server.url, tmp_path)
    server.push(body=envelope("merge-gate", {"policy_required": False}))
    c.read("merge-gate", {"branch": "b"})
    server.push(503, {"v": 1, "error": {"code": "x", "message": "x"}})
    assert kind_of(lambda: c.read("merge-gate", {"branch": "b"})).kind == "server"


def test_after_an_outage_cacheable_views_fail_fast_but_merge_gate_tries(server, tmp_path):
    url = server.url
    c = client_for(url, tmp_path)
    c.cache.mark_down()
    assert kind_of(lambda: c.read("paths", {"path": "a"})).kind == "unreachable"
    assert server.seen == []
    server.push(body=envelope("merge-gate", {"policy_required": False}))
    assert c.read("merge-gate", {"branch": "b"}).source == "server"
    assert c.cache.down_since() is None


def test_an_unreachable_server_marks_the_cache_down(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    c = client_for(f"http://127.0.0.1:{port}/mcp", tmp_path)
    kind_of(lambda: c.read("paths", {"path": "a"}))
    assert c.cache.down_since() is not None


def test_a_confirmed_refusal_fails_fast_except_for_the_merge_gate(server, tmp_path, monkeypatch):
    clock = [1000.0]
    c = client_for(server.url, tmp_path)
    monkeypatch.setattr(c.cache, "_clock", lambda: clock[0])
    for _ in range(2):
        server.push(401, {"error": "authentication required"})
        kind_of(lambda: c.read("paths", {"path": "a"}))
        clock[0] += 6
    seen = len(server.seen)
    assert kind_of(lambda: c.read("paths", {"path": "a"})).kind == "refused"
    assert len(server.seen) == seen                     # failed from memory
    server.push(body=envelope("merge-gate", {"policy_required": False}))
    assert c.read("merge-gate", {"branch": "b"}).source == "server"


def test_one_busy_retry_when_asked_and_it_fits(server):
    server.push(429, {"error": "rate limited"}, headers={"Retry-After": "1"})
    server.push(body=envelope("merge-gate", {"policy_required": False}))
    slept = []
    c = client_for(server.url, sleep=slept.append)
    assert c.read("merge-gate", {"branch": "b"}, retry_busy=True).source == "server"
    assert slept == [1.0]


@pytest.mark.parametrize("header, expected", [("-5", 0.0), ("nan", None), ("inf", None), ("soon", None)])
def test_a_garbage_retry_after_stays_a_typed_failure(server, header, expected):
    """Review M1: a negative Retry-After is clamped to 0 and a non-finite one ignored, never a ValueError."""
    server.push(429, {"error": "rate limited"}, headers={"Retry-After": header})
    server.push(body=envelope("merge-gate", {"policy_required": False}))
    slept = []
    c = client_for(server.url, sleep=slept.append)
    if expected is None:
        assert kind_of(lambda: c.read("merge-gate", {"branch": "b"}, retry_busy=True)).kind == "busy"
        assert slept == []
    else:
        assert c.read("merge-gate", {"branch": "b"}, retry_busy=True).source == "server"
        assert slept == [expected]