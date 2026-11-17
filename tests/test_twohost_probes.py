# User intent: against a real daemon started with deliberately small limits, throw the hostile and no-key requests the internet can send (no key, oversize head or body, slow headers, wrong Host, unknown API versions, request floods, viewer writes without credentials) and show each gets the refusal the designs promise, then recovers (team-server-015 S10, Step 8, part 1).
from __future__ import annotations

import concurrent.futures
import http.client
import json

import pytest

import serve_helpers
from twohost import waits
from twohost.fixtures import team

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(180)]

HEADER_TIMEOUT = 2
MAX_BODY = 65536
SMALL_LIMITS = ["--max-body-bytes", str(MAX_BODY), "--header-timeout", str(HEADER_TIMEOUT), "--body-timeout", "3",
                "--rate-limit", "60", "--rate-burst", "10", "--max-inflight", "4"]
CRLF = serve_helpers.CRLF


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    try:
        with team(tmp_path_factory.mktemp("s10") / "run", patch, flags=SMALL_LIMITS, name="s10") as the_team:
            yield the_team
    finally:
        patch.undo()


def status_of(raw: bytes) -> int | None:
    return int(raw.split(b" ", 2)[1]) if raw.startswith(b"HTTP/1.") else None


def request_bytes(world, method, path, *, headers=(), body=b"", key=True, host=None) -> bytes:
    port = world.host.url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[1]
    lines = [f"{method} {path} HTTP/1.1", f"Host: {host or '127.0.0.1:' + port}", "X-Forwarded-For: 203.0.113.7",
             *([f"Authorization: Bearer {world.ann.key}"] if key else []), *headers]
    return (CRLF.decode().join(lines) + CRLF.decode() * 2).encode("latin-1") + body


def test_no_key_is_401_on_every_public_path_before_any_body_is_read(world):
    for method, path in (("POST", "/mcp"), ("GET", "/mcp"), ("GET", "/read/v1/meta"), ("POST", "/write/v1/merge-stamp"),
                         ("GET", "/anything/else"), ("GET", "/.well-known/oauth-authorization-server")):
        response = world.ann.http(method, path, key=None)
        assert response.status_code == 401, (method, path, response.status_code)
        assert "Bearer" in response.headers.get("www-authenticate", ""), path
    # a declared body that never arrives is not waited for: the refusal comes first
    raw, took = serve_helpers.raw_request(world.host.url, request_bytes(
        world, "POST", "/mcp", key=False, headers=["Content-Length: 5000", "Content-Type: application/json"]), read_timeout=3)
    assert status_of(raw) == 401 and took < 2.5, (raw[:200], took)


def test_an_over_cap_head_is_431(world):
    huge = "X-Pad: " + "a" * 20000
    raw, _ = serve_helpers.raw_request(world.host.url, request_bytes(world, "GET", "/read/v1/meta", headers=[huge]), read_timeout=3)
    assert status_of(raw) == 431, raw[:200]


def test_an_over_cap_body_is_413_declared_or_chunked(world):
    body = b"x" * (MAX_BODY + 1000)
    raw, _ = serve_helpers.raw_request(world.host.url, request_bytes(
        world, "POST", "/read/v1/tools", headers=["Content-Type: application/json", f"Content-Length: {len(body)}"],
        body=body), read_timeout=5)
    assert status_of(raw) == 413, raw[:200]
    chunk = f"{len(body):x}".encode() + CRLF + body + CRLF + b"0" + CRLF + CRLF
    raw, _ = serve_helpers.raw_request(world.host.url, request_bytes(
        world, "POST", "/read/v1/tools", headers=["Content-Type: application/json", "Transfer-Encoding: chunked"],
        body=chunk), read_timeout=5)
    assert status_of(raw) in (400, 411, 413), raw[:200]     # the read API refuses chunked bodies outright (007)


def test_a_slow_header_is_closed_within_the_header_timeout_plus_a_second(world):
    sock = serve_helpers.connect(world.host.url)
    try:
        sock.sendall(b"GET /read/v1/meta HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Slow: a")
        assert serve_helpers.closed_within(sock, HEADER_TIMEOUT + 1.0), "the daemon kept a trickling connection open"
    finally:
        sock.close()


def test_an_unknown_host_is_421(world):
    response = world.ann.http("GET", "/read/v1/meta", headers={"Host": "evil.example"})
    assert response.status_code == 421, (response.status_code, response.text)


def test_unknown_read_and_write_versions_answer_their_error_envelopes(world):
    for path in ("/read/v9/meta", "/read/v1/nope", "/write/v9/merge-stamp", "/write/v1/nope"):
        response = world.ann.http("GET", path)
        assert response.status_code in (400, 404, 405), (path, response.status_code)
        body = response.json()
        assert body.get("error") or body.get("v"), (path, body)
        assert "Traceback" not in response.text and str(world.root) not in response.text


def test_a_flood_gets_429_with_retry_after_and_then_recovers(world):
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        seen = list(pool.map(lambda _: world.bob.http("GET", "/read/v1/meta").status_code, range(60)))
    assert 429 in seen and 200 in seen, seen
    assert world.ann.http("GET", "/read/v1/meta").status_code == 200       # bob is throttled; ann's own bucket is untouched
    limited = world.bob.http("GET", "/read/v1/meta")
    if limited.status_code == 429:
        assert int(limited.headers["retry-after"]) >= 1
    waits.until(lambda: world.bob.http("GET", "/read/v1/meta").status_code == 200, 20, "the rate limit to recover")


def test_a_viewer_write_without_a_session_origin_or_csrf_is_refused(world):
    viewer = world.host.viewer_url
    assert viewer, world.host.ready
    port = int(viewer.rstrip("/").rsplit(":", 1)[1])
    for method, path in (("POST", "/api/notes"), ("PUT", "/api/viewer/prefs"), ("POST", "/api/tasks")):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request(method, path, body=json.dumps({}), headers={
                "Host": f"127.0.0.1:{port}", "X-Forwarded-For": "203.0.113.7", "Content-Type": "application/json"})
            response = connection.getresponse()
            response.read()
        finally:
            connection.close()
        assert response.status in (401, 403), (method, path, response.status)


def test_the_viewer_serves_nothing_to_a_stranger(world):
    port = int(world.host.viewer_url.rstrip("/").rsplit(":", 1)[1])
    for path in ("/api/board", "/api/sessions", "/backlog.yaml", "/v3/dev/edit-demo", "/file/.taskmaster/local/serve/operator.token"):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request("GET", path, headers={"Host": f"127.0.0.1:{port}", "X-Forwarded-For": "203.0.113.7"})
            response = connection.getresponse()
            body = response.read().decode("utf-8", "replace")
        finally:
            connection.close()
        assert response.status in (302, 303, 401, 403, 404), (path, response.status)
        assert world.operator.key not in body
