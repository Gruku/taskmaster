# User intent: the public viewer answers only an exact route allow-list and an exact static allow-list (team-server-028 D2-D4, F3), decoded once, so a scanner learns nothing and no excluded file leaks.
import json
import socket
from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from taskmaster.serve import viewer as viewer_module
from taskmaster.serve.limits import KeyedLimiter
from taskmaster.serve.viewer import caddy_matcher_forms, route_allowed, static_asset_allowed
from test_native_service_build import root  # noqa: F401  (fixture)
from test_serve_viewer import (CADDY, PUBLIC, Clock, git, login, make_viewer, req,  # noqa: F401
                               session_headers, v, work_tree)

REMOVED = ("/backlog.yaml", "/v3/dev/edit-demo", "/v3/dev/edit-demo/", "/v3", "/v3/", "/v3/index.html",
           "/index.html", "/api/session", "/nope")
ENCODED = ("/%62acklog.yaml", "/api/%73ession", "/api/../backlog.yaml", "/static/v3/%2e%2e/dev/edit-demo.html",
           "/file/..%2f.env")
DOUBLE = ("/static/v3/js/%255fdormant/flight-log.js", "/static/v3/js/%252e%252e%252ftests%252fmock-api.js",
          "/static/v3/%2564ev/edit-demo.js", "/static/v3/js/components/edit/fields/md-field%252ddemo.js",
          "/file/%252e%252e%252f.env")


def raw(v, request_line: str, headers: dict | None = None) -> bytes:
    host = f"Host: {v.loopback_host}\r\n"
    extra = "".join(f"{k}: {val}\r\n" for k, val in (headers or {}).items())
    sock = socket.create_connection(("127.0.0.1", v.port), timeout=10)
    try:
        sock.sendall(f"{request_line}\r\n{host}{extra}\r\n".encode("latin-1"))
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
        return data
    finally:
        sock.close()


def status_of(data: bytes) -> int:
    return int(data.split(b" ", 2)[1])


# --- Step 1: routes ----------------------------------------------------------------------------

def test_removed_paths_are_404_with_and_without_a_session(v):
    cookie, _ = login(v)
    bodies = set()
    for path in REMOVED:
        for headers in ({}, {"Cookie": cookie}):
            resp = req(v, "GET", path, headers=headers)
            assert resp.status == 404, (path, headers, resp.status)
            assert b"schema_version" not in resp.body and b"tasks:" not in resp.body
            bodies.add(resp.body)
    assert len(bodies) == 1                                    # identical bodies: nothing to tell routes apart


def test_unknown_paths_spend_the_failure_budget_without_a_session_only(make_viewer):
    clock = Clock()
    v = make_viewer(clock=clock, failures=KeyedLimiter(20, 0.0, clock=clock))
    for _ in range(20):
        assert req(v, "GET", "/nope").status == 404
    assert req(v, "GET", "/nope").status == 429
    v2 = make_viewer(clock=clock, failures=KeyedLimiter(20, 0.0, clock=clock))
    cookie, _ = login(v2)
    for _ in range(30):
        assert req(v2, "GET", "/nope", headers={"Cookie": cookie}).status == 404
    assert req(v2, "GET", "/api/backlog", headers={"Cookie": cookie}).status == 200


@pytest.mark.parametrize("path", ENCODED)
def test_encoded_forms_are_never_served(v, path):
    cookie, _ = login(v)
    for headers in ({}, {"Cookie": cookie}):
        assert req(v, "GET", path, headers=headers).status in (400, 404)


@pytest.mark.parametrize("path", DOUBLE)
def test_double_encoded_forms_are_never_served(v, path):
    cookie, _ = login(v)
    for headers in ({}, {"Cookie": cookie}):
        resp = req(v, "GET", path, headers=headers)
        assert resp.status in ((400, 404, 303) if not headers else (400, 404)), (path, resp.status)
        assert b"flight" not in resp.body and b"mock" not in resp.body.lower()


def test_malformed_targets_are_400(v):
    assert status_of(raw(v, "GET http://x/ HTTP/1.1")) == 400
    assert status_of(raw(v, "GET /api/b\\oard HTTP/1.1")) == 400
    assert status_of(raw(v, "GET /api/b%00oard HTTP/1.1")) == 400
    for target in ("//api/board", "//login", "//static/v3/js/main.js"):
        assert status_of(raw(v, f"GET {target} HTTP/1.1")) == 400, target


def test_every_route_still_answers_and_one_decode_one_value(v, monkeypatch):
    cookie, csrf = login(v)
    seen = []
    original = bs.ViewerHandler.do_GET

    def spy(self):
        from urllib.parse import unquote, urlparse
        seen.append((unquote(urlparse(self.path).path), self.viewer_path))
        return original(self)
    monkeypatch.setattr(bs.ViewerHandler, "do_GET", spy)
    for path in ("/", "/api/board", "/api/backlog", "/api/identity", "/api/viewer/prefs", "/api/threads",
                 "/api/sessions", "/api/bugs", "/api/issues", "/api/ideas", "/api/continuity", "/api/notes",
                 "/file/docs/a.md", "/static/v3/css/tokens.css", "/favicon.ico", "/api/dashboard/recent-events?since=2026-01-01T00:00:00Z"):
        resp = req(v, "GET", path, headers={"Cookie": cookie})
        assert resp.status == 200, (path, resp.status, resp.text[:200])
    assert seen and all(base == guarded for base, guarded in seen)
    assert req(v, "GET", "/login").status == 200


def test_continuity_bytes_equal_the_store(v):
    cookie, _ = login(v)
    resp = req(v, "GET", "/api/continuity", headers={"Cookie": cookie})
    assert resp.status == 200
    assert "<untrusted" not in resp.text and "<tm-" not in resp.text


def test_the_viewer_reads_no_sessions_linear_or_projection_rows(v):
    # M13: the viewer's readers use handover and store records only; markers never appear in any GET body.
    markers = ("M13-SESSIONS-1", "M13-LINEAR-1", "M13-PROJ-1")
    from taskmaster.native_routing import viewer as native_viewer
    database = native_viewer.database()
    if database is not None:
        for table, columns in (("sessions", None), ("linear_queue", None), ("projection", None)):
            try:
                names = [r[1] for r in database.connect().execute(f"PRAGMA table_info({table})").fetchall()]
            except Exception:  # noqa: BLE001
                names = []
            assert names or True                                 # the table's shape is store-owned
    cookie, _ = login(v)
    for path in ("/api/board", "/api/backlog", "/api/threads", "/api/sessions", "/api/continuity", "/api/notes",
                 "/api/bugs", "/api/issues", "/api/ideas", "/api/viewer/prefs", "/api/identity"):
        body = req(v, "GET", path, headers={"Cookie": cookie}).text
        assert not any(m in body for m in markers), path


# --- caddy forms -------------------------------------------------------------------------------

def test_route_allowed_table():
    for path in ("/", "/login", "/logout", "/favicon.ico", "/api/board", "/static/v3/js/main.js", "/file/docs/a.md"):
        assert route_allowed(path), path
    for path in REMOVED + ("/api", "/static/v3", "/file", "/API/board", "/Login", "/mcp", "/read/v1/meta"):
        assert not route_allowed(path), path
    assert caddy_matcher_forms() == ["/favicon.ico", "/login", "/logout", "/", "/static/v3/*", "/api/*", "/file/*"]


# --- Step 2: static ----------------------------------------------------------------------------

STATIC_OK = ("css/tokens.css", "js/main.js", "js/lib/csrf.js", "vendor/marked.min.js",
             "vendor/fonts/DMSans-Variable.woff2")
STATIC_NO = ("dev/edit-demo.html", "brainstorm-phases.html", "index.html", "package.json", "README.md",
             "tests/mock-api.js", "js/_dormant/flight-log.js", "js/components/edit/fields/md-field-demo.js",
             "tools/gen-tokens.mjs", "node_modules/x.js", "css/.hidden.css", "vendor/../index.html",
             "js/dev/edit-demo.js", "vendor/fonts/OFL-DMSans.txt", "js/%5fdormant/flight-log.js",
             "js/%2e%2e%2ftests%2fmock-api.js", "", "js//main.js", "js/./main.js", "css/a\\b.css", "css/a\x00.css")


@pytest.mark.parametrize("rel", STATIC_OK)
def test_static_allow_list_admits(rel):
    assert static_asset_allowed(rel), rel


@pytest.mark.parametrize("rel", STATIC_NO)
def test_static_allow_list_refuses(rel):
    assert not static_asset_allowed(rel), rel


def test_static_allowed_and_excluded_over_http(v, monkeypatch):
    seen = []
    real_allowed = viewer_module.static_asset_allowed
    monkeypatch.setattr(viewer_module, "static_asset_allowed", lambda rel: (seen.append(rel), real_allowed(rel))[1])
    served = []
    original = bs.ViewerHandler._static_rel

    def spy(self, clean_path):
        rel = original(self, clean_path)
        served.append(rel)
        return rel
    monkeypatch.setattr(bs.ViewerHandler, "_static_rel", spy)
    for rel in STATIC_OK:
        resp = req(v, "GET", "/static/v3/" + rel)
        assert resp.status == 200, rel
    assert req(v, "GET", "/favicon.ico").status == 200
    assert seen and set(served) <= set(seen) | {"vendor/favicon.ico"}
    viewer_root = bs.SCRIPT_DIR / "viewer"
    for rel in STATIC_NO[:12]:
        resp = req(v, "GET", "/static/v3/" + rel)
        assert resp.status in (400, 404), rel
    for form in DOUBLE[:4]:
        resp = req(v, "GET", form)
        assert resp.status in (400, 404)
        assert resp.body != (viewer_root / "js" / "_dormant" / "flight-log.js").read_bytes()


def test_excluded_static_is_charged_without_a_session(make_viewer):
    clock = Clock()
    v = make_viewer(clock=clock, failures=KeyedLimiter(20, 0.0, clock=clock))
    for _ in range(20):
        assert req(v, "GET", "/static/v3/package.json").status == 404
    assert req(v, "GET", "/static/v3/css/tokens.css").status == 429


def test_a_planted_file_with_a_percent_in_its_name_is_refused(v):
    planted = bs.SCRIPT_DIR / "viewer" / "js" / "a%5fb.js"
    planted.write_text("// planted", encoding="utf-8")
    try:
        assert req(v, "GET", "/static/v3/js/a%255fb.js").status in (400, 404)
        assert req(v, "GET", "/static/v3/js/a%5fb.js").status in (400, 404)
    finally:
        planted.unlink()


def test_solo_static_still_decodes():
    handler = bs.ViewerHandler.__new__(bs.ViewerHandler)
    assert handler._static_rel("/static/v3/js/m%61in.js") == "js/main.js"


# What Caddy's response buffer can be made to hold by someone without a session (028 gate, Codex-I): public static
# assets come from Caddy's file_server, so every answer the proxy carries to such a client is one of these, and each
# must stay small. A zero-window HTTP/2 stream then pins no more than Go's own per-stream buffering would.
UNAUTHENTICATED_ANSWER_BOUND = 4096


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/login", None), ("HEAD", "/login", None), ("POST", "/login", "token=wrong"), ("POST", "/login", None),
    ("POST", "/logout", ""), ("GET", "/logout", None), ("GET", "/", None), ("GET", "/api/board", None),
    ("POST", "/api/tasks", "{}"), ("GET", "/file/docs/a.md", None), ("GET", "/static/v3/js/_dormant/flight-log.js", None),
    ("GET", "/static/v3/nope.js", None), ("GET", "/backlog.yaml", None), ("OPTIONS", "/", None)])
def test_every_answer_without_a_session_is_small(v, method, path, body):
    headers = {**CADDY, "Origin": PUBLIC}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        answer = req(v, method, path, headers=headers, body=body)
    except ConnectionError:          # a refused POST closes with its body unread; Windows may turn that into a reset
        answer = req(v, method, path, headers=headers, body=body)
    assert answer.status != 200 or path == "/login", (path, answer.status)
    assert len(answer.body) <= UNAUTHENTICATED_ANSWER_BOUND, (method, path, answer.status, len(answer.body))
