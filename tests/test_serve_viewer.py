# User intent: the server-mode viewer logs in by token, refuses forged and cross-origin writes, sends no CORS, and keeps secrets out of /file/.
import contextlib
import contextvars
import http.client
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from serve_helpers import TwoKeyAuthenticator
from taskmaster import backlog_server as bs
from taskmaster.bounded_run import run_bounded
from taskmaster.coordinator import client as client_module
from taskmaster.serve import auth
from taskmaster.serve import viewer as viewer_module
from taskmaster.serve.auth import OPERATOR, Principal
from taskmaster.serve.config import ServeConfig, ServeUnavailable, allowed_viewer_origins
from taskmaster.serve.limits import InFlight, KeyedLimiter, failure_budget
from taskmaster.serve.tracked import TrackedFiles, file_path_allowed, tracked_file_allowed, tracked_files_for
from taskmaster.serve.viewer import (ViewerGuard, start_viewer,
                                     stop_viewer)
from test_native_service_build import root  # noqa: F401  (fixture)

ALICE = Principal("alice", "member", "a1a1a1a1a1a1a1a1")
BOB = Principal("bob", "member", "b0b0b0b0b0b0b0b0")
PUBLIC = "https://v.example"
CADDY = {"Host": "v.example", "X-Forwarded-For": "203.0.113.7"}
WRITE_METHODS = ("PUT", "POST", "PATCH", "DELETE", "FROB")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def git(root, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    result = run_bounded(["git", "-C", str(root), *args], timeout=60, env=env)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def work_tree(root):  # noqa: F811
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "a.md").write_text("# A\n", encoding="utf-8")
    (root / ".gitignore").write_text(".taskmaster/local/\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "docs/a.md", ".gitignore")
    git(root, "commit", "-q", "-m", "init")
    (root / ".mcp.json").write_text("{}", encoding="utf-8")
    (root / "notes.md").write_text("# secret notes\n", encoding="utf-8")
    (root / ".env").write_text("KEY=1\n", encoding="utf-8")
    serve = root / ".taskmaster" / "local" / "serve"
    serve.mkdir(parents=True, exist_ok=True)
    (serve / "operator.token").write_text("tm_op_secret\n", encoding="utf-8")
    return root


@pytest.fixture
def make_viewer(work_tree, monkeypatch):
    started = []
    monkeypatch.setattr(bs, "VIEWER_PORT", bs.VIEWER_PORT)

    def make(**overrides):
        clock = overrides.pop("clock", None) or Clock()
        authenticator = overrides.pop("authenticator", None) or TwoKeyAuthenticator(
            {"ka": ALICE, "kb": BOB, "kop": OPERATOR})
        options = dict(authenticator=authenticator,
                       origins=allowed_viewer_origins(ServeConfig(root=work_tree, viewer_origins=(PUBLIC,)), 0),
                       root=work_tree, max_body_bytes=2097152, header_timeout=10.0,
                       principals=KeyedLimiter(1000, 0.0), inflight=InFlight(8),
                       failures=failure_budget(clock=clock), tracked=TrackedFiles(work_tree, clock=clock),
                       viewer_port=0, clock=clock)
        options.update(overrides)
        guard = ViewerGuard(**options)
        server, thread, port = start_viewer(bs, guard, 0)
        started.append((server, thread))
        return SimpleNamespace(guard=guard, port=port, clock=clock, auth=authenticator, root=work_tree,
                               loopback=f"http://127.0.0.1:{port}", loopback_host=f"127.0.0.1:{port}",
                               server=server, thread=thread)
    yield make
    for server, thread in started:
        stop_viewer(server, thread)


@pytest.fixture
def v(make_viewer):
    return make_viewer()


def req(v, method, path, *, headers=None, body=None, conn=None):
    own = conn is None
    conn = conn or http.client.HTTPConnection("127.0.0.1", v.port, timeout=10)
    try:
        hdrs = {"Host": v.loopback_host, **(headers or {})}
        hdrs = {k: val for k, val in hdrs.items() if val is not None}
        data = body.encode() if isinstance(body, str) else body
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        return SimpleNamespace(status=resp.status, headers=resp.headers, body=payload,
                               text=payload.decode("utf-8", "replace"))
    finally:
        if own:
            conn.close()


def login(v, key="ka", *, public=False, extra=None, csrf=True):
    headers = ({**CADDY, "Origin": PUBLIC} if public else {"Origin": v.loopback})
    headers.update({"Content-Type": "application/x-www-form-urlencoded", **(extra or {})})
    resp = req(v, "POST", "/login", headers=headers, body=f"token={key}")
    assert resp.status == 303, (resp.status, resp.text)
    cookie = resp.headers["Set-Cookie"].split(";", 1)[0]
    if not csrf:
        return cookie, None
    page = req(v, "GET", "/", headers={**(CADDY if public else {}), "Cookie": cookie})
    assert page.status == 200, page.text
    csrf = re.search(r'<meta name="taskmaster-csrf" content="([^"]+)">', page.text).group(1)
    return cookie, csrf


def session_headers(v, cookie, csrf=None, *, public=False, origin=True):
    headers = dict(CADDY) if public else {}
    headers["Cookie"] = cookie
    if origin:
        headers["Origin"] = PUBLIC if public else v.loopback
    if csrf is not None:
        headers["X-Taskmaster-CSRF"] = csrf
    return headers


def prefs_put(v, headers, method="PUT", theme="light"):
    return req(v, method, "/api/viewer/prefs", headers={**headers, "Content-Type": "application/json"},
               body=json.dumps({"theme": theme}))


def theme(v, cookie):
    resp = req(v, "GET", "/api/viewer/prefs", headers={"Cookie": cookie})
    assert resp.status == 200, resp.text
    return json.loads(resp.body)["theme"]


# --- login -----------------------------------------------------------------------------------

def test_login_sets_httponly_samesite_strict_cookie(v):
    resp = req(v, "POST", "/login", headers={**CADDY, "Origin": PUBLIC}, body="token=ka")
    assert resp.status == 303 and resp.headers["Location"] == "/"
    cookie = resp.headers["Set-Cookie"]
    assert cookie.startswith("__Host-tm_session=")
    parts = [p.strip() for p in cookie.split(";")]
    assert {"HttpOnly", "Secure", "SameSite=Strict", "Path=/", "Max-Age=86400"} <= set(parts)
    assert not any(p.lower().startswith("domain") for p in parts)
    plain = req(v, "POST", "/login", headers={"Origin": v.loopback}, body="token=ka").headers["Set-Cookie"]
    parts = [p.strip() for p in plain.split(";")]
    assert plain.startswith(f"tm_session_{v.port}=") and "HttpOnly" in parts and "SameSite=Strict" in parts
    assert "Secure" not in parts
    json_login = req(v, "POST", "/login", headers={"Origin": v.loopback, "Content-Type": "application/json"},
                     body=json.dumps({"token": "kb"}))
    assert json_login.status == 303


def test_operator_token_refused_from_a_public_origin(v, monkeypatch):
    for _ in range(25):
        resp = req(v, "POST", "/login", headers={**CADDY, "Origin": PUBLIC}, body="token=kop")
        assert resp.status == 403 and "Set-Cookie" not in resp.headers
        assert json.loads(resp.body)["error"] == ("the operator bootstrap token works only on the host; "
                                                  "use a key from taskmaster serve add-user")
    assert v.guard.failures.blocked("203.0.113.7") == 0
    login(v, "ka", public=True)                               # a member key logs in through Caddy as before
    cookie, _ = login(v, "kop")                               # loopback, no X-Forwarded-For
    resp = req(v, "GET", "/api/backlog", headers={"Cookie": cookie, "X-Forwarded-For": "203.0.113.7"})
    assert resp.status == 403                                 # the check runs on every request
    seen = []

    def recorder(scope):
        seen.append(scope)
        return False
    monkeypatch.setattr(auth, "is_host_local", recorder)
    resp = req(v, "POST", "/login", headers={"Origin": v.loopback, "X-Forwarded-For": "198.51.100.1"},
               body="token=kop")
    assert resp.status == 403 and "Set-Cookie" not in resp.headers
    assert seen and seen[-1]["client"][0] == "127.0.0.1"
    assert (b"x-forwarded-for", b"198.51.100.1") in seen[-1]["headers"]


def test_bad_key_is_refused_and_charged(v):
    for _ in range(20):
        resp = req(v, "POST", "/login", headers={**CADDY, "Origin": PUBLIC}, body="token=nope")
        assert resp.status == 401 and "That key was not accepted." in resp.text
    resp = req(v, "POST", "/login", headers={**CADDY, "Origin": PUBLIC}, body="token=ka")
    assert resp.status == 429 and "Retry-After" in resp.headers
    other = {**CADDY, "X-Forwarded-For": "203.0.113.8", "Origin": PUBLIC}
    assert req(v, "POST", "/login", headers=other, body="token=ka").status == 303
    for i in range(20):
        headers = {**CADDY, "X-Forwarded-For": f"2001:db8:5:6::{i + 1:x}", "Origin": PUBLIC}
        assert req(v, "POST", "/login", headers=headers, body="token=nope").status == 401
    blocked = {**CADDY, "X-Forwarded-For": "2001:db8:5:6:ffff::1", "Origin": PUBLIC}
    assert req(v, "POST", "/login", headers=blocked, body="token=ka").status == 429
    fresh = {**CADDY, "X-Forwarded-For": "2001:db8:5:7::1", "Origin": PUBLIC}
    assert req(v, "POST", "/login", headers=fresh, body="token=ka").status == 303


def test_refusals_with_a_valid_session_are_not_charged(v):
    cookie, csrf = login(v, "ka", public=True)
    headers = session_headers(v, cookie, csrf, public=True)
    for _ in range(25):
        assert req(v, "PUT", "/api/viewer/prefs", headers={**headers, "Content-Length": "3000000"}).status == 413
        assert req(v, "PUT", "/api/viewer/prefs", headers={**headers, "Transfer-Encoding": "chunked"}).status == 411
    assert v.guard.failures.blocked("203.0.113.7") == 0
    login(v, "ka", public=True)
    for _ in range(20):
        req(v, "PUT", "/api/viewer/prefs", headers={**CADDY, "Origin": PUBLIC, "Content-Length": "3000000"})
    assert req(v, "PUT", "/api/viewer/prefs", headers={**CADDY, "Content-Length": "3000000"}).status == 429


def test_login_requires_an_allowed_origin(v):
    for origin in ("https://evil.example", None):
        resp = req(v, "POST", "/login", headers={**CADDY, "Origin": origin}, body="token=ka")
        assert resp.status == 403 and "Set-Cookie" not in resp.headers


def test_unauthenticated_requests(v):
    resp = req(v, "GET", "/")
    assert resp.status == 303 and resp.headers["Location"] == "/login"
    resp = req(v, "GET", "/api/backlog")
    assert resp.status == 401 and "error" in json.loads(resp.body)
    assert req(v, "GET", "/static/v3/css/tokens.css").status == 200
    page = req(v, "GET", "/login")
    assert page.status == 200 and 'name="token"' in page.text and 'type="password"' in page.text


def test_login_page_with_a_valid_cookie_goes_home(v):
    cookie, _ = login(v)
    resp = req(v, "GET", "/login", headers={"Cookie": cookie})
    assert resp.status == 303 and resp.headers["Location"] == "/"


# --- writes ----------------------------------------------------------------------------------

@pytest.mark.parametrize("method", WRITE_METHODS)
def test_cross_origin_write_is_refused(v, method):
    cookie, csrf = login(v)
    base = session_headers(v, cookie, csrf, origin=False)
    for extra in ({"Origin": "https://evil.example"}, {}, {"Origin": v.loopback, "Sec-Fetch-Site": "cross-site"}):
        resp = prefs_put(v, {**base, **extra}, method=method)
        assert resp.status == 403 and json.loads(resp.body) == {"error": "cross-origin write refused"}
    assert theme(v, cookie) == "dark"


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_write_without_csrf_token_is_refused(v, method):
    cookie, csrf = login(v)
    _, bob_csrf = login(v, "kb")
    for token in (None, "wrong", bob_csrf):
        resp = prefs_put(v, session_headers(v, cookie, token), method=method)
        assert resp.status == 403 and json.loads(resp.body) == {"error": "missing or invalid CSRF token"}
    assert theme(v, cookie) == "dark"
    if method in ("DELETE", "FROB"):
        assert prefs_put(v, session_headers(v, cookie, csrf), method=method).status == 501


def test_valid_write_passes_through(v, monkeypatch):
    seen = []
    original = bs.ViewerHandler.do_PUT

    def recording(self):
        seen.append(self.principal.handle)
        return original(self)
    monkeypatch.setattr(bs.ViewerHandler, "do_PUT", recording)
    cookie, csrf = login(v)
    assert prefs_put(v, session_headers(v, cookie, csrf)).status == 200
    assert theme(v, cookie) == "light" and seen == ["alice"]


def test_logout(v):
    cookie, csrf = login(v)
    resp = req(v, "POST", "/logout", headers=session_headers(v, cookie, csrf))
    assert resp.status == 303 and resp.headers["Location"] == "/login"
    assert any("Max-Age=0" in value for value in resp.headers.get_all("Set-Cookie"))
    assert req(v, "GET", "/api/backlog", headers={"Cookie": cookie}).status == 401


# --- write credit (design D10.10) ------------------------------------------------------------

@pytest.fixture
def team(work_tree, monkeypatch):
    """Real member keys (002's registry) and the daemon's coordinator, so viewer writes really commit."""
    from taskmaster.serve.daemon import ServeCoordinator
    from taskmaster.serve.principals import PrincipalRegistry, add_user, principals_path
    keys = {}
    for handle in ("alice", "bob"):
        key, record, _ = add_user(work_tree, handle)
        keys[handle] = (key, Principal(record.handle, record.role, record.key_id))
    origins = []
    original = ServeCoordinator._origin

    def recording(self, message):
        origin = original(self, message)
        if message.get("method") == "execute":
            origins.append(message.get("origin"))
        return origin
    monkeypatch.setattr(ServeCoordinator, "_origin", recording)
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", auth.request_key_id)   # what 002's daemon installs
    from taskmaster.native import claims
    from taskmaster.serve.sessions import SessionRegistry
    registry = PrincipalRegistry(principals_path(work_tree))
    sessions = SessionRegistry(registry)                                         # what 003's daemon installs
    monkeypatch.setattr(bs, "SESSION_PROVIDER", sessions.current)
    monkeypatch.setattr(claims, "CONNECTED", sessions.connected)
    owner = ServeCoordinator(work_tree, registry=registry).start()
    threading.Thread(target=lambda: (owner.stopping.wait(), owner.close()), daemon=True).start()
    yield SimpleNamespace(keys=keys, origins=origins,
                          authenticator=TwoKeyAuthenticator({key: p for key, p in keys.values()}))
    owner.stop()
    owner.close()


def test_viewer_writes_are_credited_per_user_concurrently(make_viewer, team, monkeypatch):
    v = make_viewer(authenticator=team.authenticator)
    alice_id, bob_id = team.keys["alice"][1].key_id, team.keys["bob"][1].key_id
    provider_before = client_module.ORIGIN_PROVIDER
    recorded, entered = [], []
    original_execute = client_module.Client.execute

    def execute(self, envelope):
        recorded.append((threading.get_ident(), auth.request_key_id()))
        time.sleep(0.3)
        return original_execute(self, envelope)
    monkeypatch.setattr(client_module.Client, "execute", execute)
    original_cm = auth.viewer_principal

    @contextlib.contextmanager
    def recording_cm(key_id):
        entered.append(key_id)
        with original_cm(key_id):
            yield
    monkeypatch.setattr(auth, "viewer_principal", recording_cm)
    sessions = {"alice": login(v, team.keys["alice"][0]), "bob": login(v, team.keys["bob"][0])}
    entered.clear()      # login's page reads run as the person too (team-server-003 Q5); count the writes
    statuses = {}

    def write(who, title):
        cookie, csrf = sessions[who]
        resp = req(v, "PATCH", "/api/tasks/test-epic-001",
                   headers={**session_headers(v, cookie, csrf), "Content-Type": "application/json"},
                   body=json.dumps({"title": title}))
        statuses[who] = (resp.status, resp.text)
    threads = [threading.Thread(target=write, args=("alice", "By alice")),
               threading.Thread(target=write, args=("bob", "By bob"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert all(status == 200 for status, _ in statuses.values()), statuses
    assert sorted(key for _, key in recorded) == sorted([alice_id, bob_id])
    assert sorted(entered) == sorted([alice_id, bob_id])
    assert sorted(o["principal"] for o in team.origins) == sorted([alice_id, bob_id])
    assert len({ident for ident, _ in recorded}) == 2              # two viewer threads, overlapping
    assert auth.VIEWER_KEY_ID.get() is None
    assert client_module.ORIGIN_PROVIDER is provider_before

    # A kept-alive connection: a refused write never reaches execute, and a read after a write sees no key.
    reads = []
    original_get = bs.ViewerHandler.do_GET

    def recording_get(self):
        reads.append(auth.VIEWER_KEY_ID.get())
        return original_get(self)
    monkeypatch.setattr(bs.ViewerHandler, "do_GET", recording_get)
    cookie, csrf = sessions["alice"]
    conn = http.client.HTTPConnection("127.0.0.1", v.port, timeout=10)
    try:
        before = len(recorded)
        ok = req(v, "PATCH", "/api/tasks/test-epic-001", conn=conn,
                 headers={**session_headers(v, cookie, csrf), "Content-Type": "application/json"},
                 body=json.dumps({"title": "Again"}))
        assert ok.status == 200 and len(recorded) == before + 1
        # team-server-003 Q5: a read runs as its own person; alice's write leaves nothing behind for bob's read.
        assert req(v, "GET", "/api/viewer/prefs", conn=conn, headers={"Cookie": sessions["bob"][0]}).status == 200
        # A read with no principal on the same connection runs unwrapped: nothing from the writes leaks into it.
        req(v, "GET", "/favicon.ico", conn=conn)
        assert reads == [bob_id, None]
    finally:
        conn.close()
    refused = req(v, "PATCH", "/api/tasks/test-epic-001",
                  headers={**session_headers(v, cookie), "Content-Type": "application/json"},
                  body=json.dumps({"title": "No"}))
    assert refused.status == 403 and len(recorded) == before + 1


def test_viewer_reads_and_writes_run_as_the_person(make_viewer, team, monkeypatch):
    """team-server-003 Q5/D9: the board's ETag is per person, and a write's scope is `<handle>@viewer`."""
    v = make_viewer(authenticator=team.authenticator)
    alice, bob = login(v, team.keys["alice"][0]), login(v, team.keys["bob"][0])
    # /api/backlog is the payload whose ETag carries the session (viewer_board.compatibility).
    tags = {who: req(v, "GET", "/api/backlog", headers={"Cookie": cookie}).headers.get("ETag")
            for who, (cookie, _csrf) in (("alice", alice), ("bob", bob))}
    assert tags["alice"] and tags["bob"] and tags["alice"] != tags["bob"]
    assert "alice@viewer" in tags["alice"] and "bob@viewer" in tags["bob"]
    scopes = []
    original_execute = client_module.Client.execute

    def execute(self, envelope):
        scopes.append(envelope["caller_scope"])
        return original_execute(self, envelope)
    monkeypatch.setattr(client_module.Client, "execute", execute)
    cookie, csrf = alice
    resp = req(v, "PATCH", "/api/tasks/test-epic-001",
               headers={**session_headers(v, cookie, csrf), "Content-Type": "application/json"},
               body=json.dumps({"title": "Scoped"}))
    assert resp.status == 200, resp.text
    assert scopes and set(scopes) == {"alice@viewer"}


def test_viewer_answers_name_files_relative_to_the_repository(make_viewer, team):
    """028 gate, Codex minor: an idea's `path` and the related panel's nested `_path` values are repository-relative;
    the host's directory layout never reaches a member."""
    v = make_viewer(authenticator=team.authenticator)
    cookie, csrf = login(v, team.keys["alice"][0])
    created = req(v, "POST", "/api/ideas", headers={**session_headers(v, cookie, csrf), "Content-Type": "application/json"},
                  body=json.dumps({"title": "Relative idea", "body": "x"}))
    assert created.status == 201, created.text
    path = json.loads(created.body)["path"]
    assert path.startswith(".taskmaster/") and "\\" not in path, path       # the export's place, as the repo names it
    bs.backlog_handover_create(tldr="Rel paths", body="note", task_ids=["test-epic-001"])
    related = req(v, "GET", "/api/task/test-epic-001/related", headers={"Cookie": cookie})
    assert related.status == 200, related.text
    for root in {str(v.root), str(v.root).replace("\\", "/"), str(v.root.resolve())}:
        assert root not in related.text and root.replace("\\", "\\\\") not in related.text
    paths = [h["_path"] for h in json.loads(related.body)["handovers"]]
    assert paths and all(p.startswith(".taskmaster/") for p in paths), paths


def test_repo_relative_walks_nested_records(tmp_path):
    root = tmp_path / "repo"
    win = str(root).replace("/", "\\")
    payload = {"path": str(root / "a" / "b.md"), "rows": [{"_path": str(root / "c.md")}, {"n": 1, "s": "plain"}],
               "other": "/elsewhere/x.md", "root": str(root), "near": str(root) + "-sibling/x", "w": win + "\\d.md"}
    out = viewer_module.repo_relative(payload, root)
    assert out["path"] == "a/b.md" and out["rows"][0]["_path"] == "c.md" and out["rows"][1] == {"n": 1, "s": "plain"}
    assert out["other"] == "/elsewhere/x.md" and out["root"] == "." and out["near"] == str(root) + "-sibling/x"

def test_viewer_shows_a_teammates_claim_refusal_as_409(make_viewer, team):
    """R-M1: a member's viewer write that would drop a teammate's live claim (D11, E7) answers 409 with the
    reason, not a scrubbed internal error, and leaves the claim."""
    from native_twins import committed
    v = make_viewer(authenticator=team.authenticator)
    bob_id = team.keys["bob"][1].key_id
    bob_session = "bob@" + "b" * 16
    client = client_module.Client(v.root, autostart=False)
    with auth.viewer_principal(bob_id):    # the daemon's provider attaches bob's origin (005: never the caller)
        client.execute({
            "protocol": 2, "store_id": client.identity["store_id"], "caller_scope": bob_session,
            "request_id": "bob-pick", "operation": "task.pick",
            "arguments": {"id": "test-epic-001", "session": bob_session}, "expected_revisions": []})
    cookie, csrf = login(v, team.keys["alice"][0])
    resp = req(v, "PATCH", "/api/tasks/test-epic-001",
               headers={**session_headers(v, cookie, csrf), "Content-Type": "application/json"},
               body=json.dumps({"status": "archived"}))
    assert resp.status == 409, (resp.status, resp.text)
    error = json.loads(resp.body)["error"]
    assert "bob" in error and "internal error" not in error
    assert committed(v.root)[("task", "test-epic-001")][0].get("locked_by") == bob_session


def test_context_travels_only_with_copy_context():
    seen = []
    with auth.viewer_principal(ALICE.key_id):
        context = contextvars.copy_context()
        with_copy = threading.Thread(target=lambda: context.run(lambda: seen.append(auth.request_key_id())))
        without = threading.Thread(target=lambda: seen.append(auth.request_key_id()))
        with_copy.start(), with_copy.join()
        without.start(), without.join()
    assert seen == [ALICE.key_id, None]


# --- headers, hosts, sizes ---------------------------------------------------------------------

def _no_cors(resp):
    assert not [name for name in resp.headers if name.lower().startswith("access-control-")], dict(resp.headers)
    for name, value in (("X-Frame-Options", "DENY"), ("X-Content-Type-Options", "nosniff"),
                        ("Referrer-Policy", "same-origin")):
        assert resp.headers[name] == value
    assert len(resp.headers.get_all("Content-Security-Policy")) == 1


def test_no_cors_header_in_server_mode(make_viewer):
    v = make_viewer(principals=KeyedLimiter(6, 0.0))
    cookie, csrf = login(v)
    responses = [req(v, "GET", "/login"), req(v, "GET", "/"), req(v, "GET", "/", headers={"Cookie": cookie}),
                 req(v, "GET", "/api/backlog", headers={"Cookie": cookie}),
                 req(v, "GET", "/static/v3/css/tokens.css"),
                 prefs_put(v, session_headers(v, cookie, "wrong"))]
    options = [req(v, "OPTIONS", "/api/x"), req(v, "OPTIONS", "/api/x", headers={"Cookie": cookie})]
    for _ in range(5):
        last = req(v, "GET", "/api/viewer/prefs", headers={"Cookie": cookie})
    assert last.status == 429
    for resp in [*responses, *options, last]:
        _no_cors(resp)
    assert [r.status for r in options] == [405, 405]


def test_unknown_host_is_421(v):
    cookie, _ = login(v)
    assert req(v, "GET", "/api/backlog", headers={"Cookie": cookie, "Host": "evil.example"}).status == 421


def test_oversized_and_chunked_bodies(v):
    cookie, csrf = login(v)
    sock = socket.create_connection(("127.0.0.1", v.port), timeout=5)
    try:
        sock.sendall((f"PUT /api/viewer/prefs HTTP/1.1\r\nHost: {v.loopback_host}\r\nCookie: {cookie}\r\n"
                      f"Origin: {v.loopback}\r\nX-Taskmaster-CSRF: {csrf}\r\nContent-Length: 3000000\r\n\r\n").encode())
        assert b" 413 " in sock.recv(4096).split(b"\r\n", 1)[0]
    finally:
        sock.close()
    assert req(v, "PUT", "/api/viewer/prefs",
               headers={**session_headers(v, cookie, csrf), "Transfer-Encoding": "chunked"}).status == 411
    big = req(v, "POST", "/login", headers={"Origin": v.loopback}, body="token=" + "a" * 5000)
    assert big.status == 413


def test_rotated_or_revoked_key_ends_the_session(v):
    cookie, _ = login(v)
    del v.auth.keys["ka"]
    assert req(v, "GET", "/api/backlog", headers={"Cookie": cookie}).status == 401
    resp = req(v, "GET", "/", headers={"Cookie": cookie})
    assert resp.status == 303 and resp.headers["Location"] == "/login"


def test_session_expiry(v):
    cookie, _ = login(v)
    v.clock.now += 8 * 3600 + 1
    assert req(v, "GET", "/api/backlog", headers={"Cookie": cookie}).status == 401
    cookie, _ = login(v)
    for _ in range(3):                     # kept active: never idle 8 h
        v.clock.now += 7 * 3600
        assert req(v, "GET", "/api/viewer/prefs", headers={"Cookie": cookie}).status == 200
    v.clock.now += 4 * 3600                # 25 h after login
    assert req(v, "GET", "/api/viewer/prefs", headers={"Cookie": cookie}).status == 401


# --- /file/ ------------------------------------------------------------------------------------

def test_file_route_serves_only_tracked_files(v):
    cookie, _ = login(v)
    sibling = v.root.parent / (v.root.name + "-x")
    sibling.mkdir(exist_ok=True)
    (sibling / "x").write_text("x", encoding="utf-8")
    for path in ("/file/.mcp.json", "/file/notes.md", "/file/.env", "/file/.taskmaster/local/serve/operator.token",
                 "/file/.git/config", f"/file/../{sibling.name}/x", "/file/DOCS/A.MD"):
        resp = req(v, "GET", path, headers={"Cookie": cookie})
        assert resp.status == (400 if "/../" in path else 404), path       # a dot segment is refused up front (028)
        assert "tm_op_secret" not in resp.text
    ok = req(v, "GET", "/file/docs/a.md", headers={"Cookie": cookie})
    assert ok.status == 200 and "vscode://" not in ok.text and "data-b64=" in ok.text
    assert str(v.root) not in ok.text and str(v.root).replace("\\", "/") not in ok.text
    git(v.root, "add", "notes.md")
    assert req(v, "GET", "/file/notes.md", headers={"Cookie": cookie}).status == 404     # still cached
    v.clock.now += 31
    assert req(v, "GET", "/file/notes.md", headers={"Cookie": cookie}).status == 200


def test_file_path_allowed_rules(tmp_path):
    tracked = frozenset({"docs/a.md", ".taskmaster/local/x", ".git/config", "a\\b", "sub"})
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()                       # a submodule gitlink: tracked, but a directory
    assert file_path_allowed(tmp_path, "docs/a.md", tracked)
    assert not file_path_allowed(tmp_path, "sub", tracked)
    for rel in ("../docs/a.md", "docs/../docs/a.md", "/docs/a.md", "C:/x", "a\\b", ".taskmaster/local/x",
                ".git/config", "DOCS/A.MD", "", "docs//a.md"):
        assert not file_path_allowed(tmp_path, rel, tracked), rel


def test_file_route_fails_closed_without_git(make_viewer, tmp_path, caplog):
    def timing_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 10)
    clock = Clock()
    with caplog.at_level(logging.WARNING, logger="taskmaster.serve"):
        v = make_viewer(clock=clock, tracked=TrackedFiles(tmp_path, clock=clock, runner=timing_out))
        cookie, _ = login(v)
        for _ in range(2):
            assert req(v, "GET", "/file/docs/a.md", headers={"Cookie": cookie}).status == 404
            clock.now += 31
        not_a_tree = tmp_path / "plain"
        not_a_tree.mkdir()
        files = TrackedFiles(not_a_tree, clock=clock)
        assert files.get() == frozenset()
    warnings = [r for r in caplog.records if "viewer /file/ disabled" in r.getMessage()]
    assert len(warnings) == 2          # one per failure streak: the timing-out runner, and the non-tree root


def test_identity_hides_the_root(v):
    cookie, _ = login(v)
    body = json.loads(req(v, "GET", "/api/identity", headers={"Cookie": cookie}).body)
    assert "version" in body and "root" not in body


def test_viewer_error_text_is_scrubbed(v, monkeypatch):
    from taskmaster.admission import UnsupportedStoreError
    from taskmaster.native_routing import viewer as native_viewer
    root = v.root

    def failing(handler, method, database):
        raise UnsupportedStoreError(f"cannot write {root}/x")
    monkeypatch.setattr(native_viewer, "write", failing)
    cookie, csrf = login(v)
    resp = prefs_put(v, session_headers(v, cookie, csrf))
    assert resp.status == 409
    error = json.loads(resp.body)["error"]
    assert "<root>/x" in error and str(root) not in error


# --- limits ------------------------------------------------------------------------------------

def test_per_principal_limit_is_shared(make_viewer):
    import asyncio

    from taskmaster.serve.guard import PrincipalLimits
    principals = KeyedLimiter(2, 0.0)
    v = make_viewer(principals=principals)
    cookie, _ = login(v, csrf=False)
    # The login spent the first token (006 review I4), so only one more request fits.
    assert req(v, "GET", "/api/viewer/prefs", headers={"Cookie": cookie}).status == 200
    third = req(v, "GET", "/api/viewer/prefs", headers={"Cookie": cookie})
    assert third.status == 429 and third.headers["Retry-After"] == "3600"
    bob, _ = login(v, "kb", csrf=False)
    assert req(v, "GET", "/api/viewer/prefs", headers={"Cookie": bob}).status == 200
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from taskmaster.serve.auth import AccessToken
    user = AuthenticatedUser(AccessToken(token="key:x", client_id="alice", subject=ALICE.key_id, scopes=[],
                                         expires_at=None, claims={"taskmaster_principal": "alice",
                                                                  "taskmaster_role": "member",
                                                                  "taskmaster_key_id": ALICE.key_id}))
    sent = []

    async def send(message):
        sent.append(message)

    async def never(scope, receive, send):
        raise AssertionError("passed")
    asyncio.run(PrincipalLimits(never, limiter=principals, inflight=InFlight(4))({"type": "http", "user": user},
                                                                                  None, send))
    assert sent[0]["status"] == 429


def test_viewer_inflight_is_shared_and_released(make_viewer, monkeypatch):
    inflight = InFlight(1)
    v = make_viewer(inflight=inflight)
    alice, _ = login(v)
    bob, _ = login(v, "kb")
    release, entered = threading.Event(), threading.Event()
    original = bs.ViewerHandler.do_GET

    def blocking(self):
        if self.path == "/api/viewer/prefs" and self.principal.handle == "alice":
            entered.set()
            release.wait(10)
            raise RuntimeError("the base handler failed")
        return original(self)
    monkeypatch.setattr(bs.ViewerHandler, "do_GET", blocking)
    def hold():
        with contextlib.suppress(http.client.HTTPException, OSError):    # the base handler raises on purpose
            req(v, "GET", "/api/viewer/prefs", headers={"Cookie": alice})
    holder = threading.Thread(target=hold)
    holder.start()
    assert entered.wait(10)
    second = req(v, "GET", "/api/viewer/prefs", headers={"Cookie": alice})
    assert second.status == 429 and json.loads(second.body) == {"error": "too many concurrent requests"}
    assert req(v, "GET", "/api/viewer/prefs", headers={"Cookie": bob}).status == 200
    release.set()
    holder.join(10)
    deadline = time.monotonic() + 5
    while inflight.count(ALICE.key_id) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert inflight.count(ALICE.key_id) == 0


def test_connection_cap(make_viewer, monkeypatch):
    monkeypatch.setattr(viewer_module, "VIEWER_MAX_CONNECTIONS", 2)
    v = make_viewer()
    idle = [socket.create_connection(("127.0.0.1", v.port), timeout=5) for _ in range(2)]
    try:
        time.sleep(0.3)
        third = socket.create_connection(("127.0.0.1", v.port), timeout=5)
        try:
            third.sendall(f"GET /login HTTP/1.1\r\nHost: {v.loopback_host}\r\n\r\n".encode())
        except OSError:
            pass
        try:
            data = third.recv(4096)
        except (ConnectionResetError, ConnectionAbortedError):
            data = b""
        assert data == b""
        third.close()
    finally:
        for sock in idle:
            sock.close()



def test_a_second_live_viewer_on_the_port_is_refused(v):
    req(v, "GET", "/login")
    with pytest.raises(ServeUnavailable):
        start_viewer(bs, v.guard, v.port)
    assert req(v, "GET", "/login").status == 200          # the first one keeps serving


@pytest.mark.skipif(os.name == "nt", reason="POSIX bind semantics: Windows keeps the exclusive bind (028 gate I1)")
def test_a_restarted_viewer_rebinds_its_port_while_old_connections_sit_in_time_wait(v):
    # The viewer closes every connection itself, so each request leaves a server-side TIME_WAIT on the port; a
    # `systemctl restart` within 60 s of viewer traffic must still bind it, or the unit exits 69 and hits its
    # start limit (028 gate I1).
    port = v.port
    for _ in range(5):
        assert req(v, "GET", "/login").status == 200
    stop_viewer(v.server, v.thread)
    server, thread, bound = start_viewer(bs, v.guard, port)
    try:
        assert bound == port
        assert req(v, "GET", "/login").status == 200
    finally:
        stop_viewer(server, thread)

# --- tracked symlinks (005 review finding) and the shared rule ----------------------------------

def _symlink_or_skip(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable here: {exc}")


def test_tracked_symlinks_to_secrets_are_refused(make_viewer, work_tree):
    _symlink_or_skip(os.path.join("..", ".taskmaster", "local", "serve", "operator.token"), work_tree / "docs" / "tok")
    _symlink_or_skip(os.path.join("..", ".env"), work_tree / "docs" / "env")
    _symlink_or_skip("a.md", work_tree / "docs" / "alias.md")
    git(work_tree, "-c", "core.symlinks=true", "add", "docs/tok", "docs/env", "docs/alias.md")
    git(work_tree, "-c", "core.symlinks=true", "commit", "-q", "-m", "links")
    v = make_viewer()
    tracked = v.guard.tracked.get()
    assert {"docs/tok", "docs/env", "docs/alias.md"} <= tracked          # the probe's premise: git tracks the links
    cookie, _ = login(v)
    for path in ("/file/docs/tok", "/file/docs/env"):
        resp = req(v, "GET", path, headers={"Cookie": cookie})
        assert resp.status == 404 and "tm_op_secret" not in resp.text and "KEY=1" not in resp.text, path
    assert req(v, "GET", "/file/docs/alias.md", headers={"Cookie": cookie}).status == 200   # tracked -> tracked
    assert not file_path_allowed(work_tree, "docs/tok", tracked)
    assert not file_path_allowed(work_tree, "docs/env", tracked)
    assert file_path_allowed(work_tree, "docs/alias.md", tracked)


def test_symlink_out_of_the_root_is_refused(tmp_path):
    root = tmp_path / "proj"
    (root / "docs").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    _symlink_or_skip(outside, root / "docs" / "out")
    assert not file_path_allowed(root, "docs/out", frozenset({"docs/out", "outside.txt"}))


def test_tracked_file_allowed_is_the_shared_rule(work_tree):
    assert tracked_file_allowed(work_tree, "docs/a.md")
    for rel in ("notes.md", ".env", ".taskmaster/local/serve/operator.token", "../x", "spec:/etc/passwd", "/etc/passwd"):
        assert not tracked_file_allowed(work_tree, rel), rel
    assert tracked_files_for(work_tree) is tracked_files_for(work_tree / "docs" / "..")
    clock = Clock()
    files = TrackedFiles(work_tree, clock=clock)
    assert tracked_file_allowed(work_tree, "docs/a.md", tracked=files)


# --- 006 review gate fixes ---------------------------------------------------------------------

VIEWER_JS = Path(__file__).resolve().parent.parent / "viewer"


def test_referrer_policy_keeps_the_origin_on_a_form_post(v):
    # C1: under no-referrer a browser's same-origin form POST sends `Origin: null`, which login refuses.
    for resp in (req(v, "GET", "/login"), req(v, "GET", "/static/v3/css/tokens.css")):
        assert resp.headers["Referrer-Policy"] == "same-origin"


BROWSER_LOGIN = """
const path = require('path');
const { chromium } = require(path.join(process.argv[2], 'node_modules', '@playwright', 'test'));
(async () => {
  let browser;
  try { browser = await chromium.launch(); } catch (e) { console.log('SKIP ' + e.message.split('\\n')[0]); return; }
  const page = await browser.newPage();
  await page.goto(process.argv[3] + '/login');
  await page.fill('#token', process.argv[4]);
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
  const meta = await page.$('meta[name="taskmaster-csrf"]');
  console.log('URL ' + page.url() + ' META ' + (meta !== null));
  await browser.close();
})();
"""


def test_a_real_browser_can_sign_in(v, tmp_path):
    # C1, end to end: headless Chromium submits the login form and lands on the board.
    import shutil
    node = shutil.which("node")
    if node is None or not (VIEWER_JS / "node_modules" / "@playwright" / "test").exists():
        pytest.skip("node or @playwright/test is not installed (npm ci in viewer/)")
    script = tmp_path / "login.js"
    script.write_text(BROWSER_LOGIN, encoding="utf-8")
    result = run_bounded([node, str(script), str(VIEWER_JS), v.loopback, "ka"], timeout=120)
    out = result.stdout.decode("utf-8", "replace").strip()
    if out.startswith("SKIP"):
        pytest.skip(out)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert out == f"URL {v.loopback}/ META true", out


def test_file_pages_are_sandboxed_and_the_path_is_escaped(v, make_viewer, work_tree):
    # I1: a teammate's markdown runs in an opaque origin, so it cannot ride the visitor's cookie.
    (work_tree / "docs" / "x.md").write_text("<img src=x onerror=alert(1)>\n", encoding="utf-8")
    git(work_tree, "add", "docs/x.md")
    git(work_tree, "commit", "-q", "-m", "x")
    viewer = make_viewer()
    cookie, _ = login(viewer)
    resp = req(viewer, "GET", "/file/docs/x.md", headers={"Cookie": cookie})
    assert resp.status == 200
    assert resp.headers["Content-Security-Policy"].startswith("sandbox allow-scripts;")
    assert req(viewer, "GET", "/file/nope.md", headers={"Cookie": cookie}) \
        .headers["Content-Security-Policy"].startswith("sandbox;")
    assert "sandbox" not in " ".join(req(viewer, "GET", "/", headers={"Cookie": cookie})
                                     .headers.get_all("Content-Security-Policy"))
    # 028: the page names no editor link and no host path in server mode (the escape is tested in test_serve_viewer_pages).
    assert "vscode://" not in resp.text and "open-editor" not in resp.text
    assert str(work_tree) not in resp.text and work_tree.as_posix() not in resp.text


def test_blocked_peers_are_answered_silently(make_viewer, caplog):
    # I2: while a peer's budget is empty, its requests add no log lines; one "exhausted" warning only.
    v = make_viewer()
    with caplog.at_level(logging.INFO, logger="taskmaster.serve"):
        for _ in range(20):
            req(v, "POST", "/login", headers={**CADDY, "Origin": PUBLIC}, body="token=nope")
        before = len(caplog.records)
        for _ in range(30):
            assert req(v, "GET", "/api/backlog", headers=CADDY).status == 429
        assert req(v, "GET", "/api/backlog", headers={**CADDY, "Host": "evil.example"}).status == 429
    assert len(caplog.records) == before
    assert sum("exhausted its failure budget" in r.getMessage() for r in caplog.records) == 1


def _drip_login(v, length=4000, every=0.3, seconds=6.0, cookie=None, path="/login", method="POST", csrf=None):
    sock = socket.create_connection(("127.0.0.1", v.port), timeout=10)
    extra = (f"Cookie: {cookie}\r\n" if cookie else "") + (f"X-Taskmaster-CSRF: {csrf}\r\n" if csrf else "")
    try:
        sock.sendall((f"{method} {path} HTTP/1.1\r\nHost: {v.loopback_host}\r\nOrigin: {v.loopback}\r\n{extra}"
                      f"Content-Type: application/x-www-form-urlencoded\r\nContent-Length: {length}\r\n\r\n"
                      ).encode())
        started = time.monotonic()
        sock.settimeout(every)
        data = b""
        while time.monotonic() - started < seconds:
            try:
                sock.sendall(b"t")
            except OSError:
                break
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data:
                break
        return data, time.monotonic() - started
    finally:
        sock.close()


def test_a_slow_login_body_gets_408_and_is_charged(make_viewer):
    # I3: one absolute deadline for the body, not a per-recv idle timer a drip keeps resetting.
    clock = Clock()
    v = make_viewer(clock=clock, body_timeout=1.0, failures=KeyedLimiter(1, 0.0, clock=clock))
    data, elapsed = _drip_login(v)
    assert data.startswith(b"HTTP/1.0 408") and 0.8 <= elapsed <= 3.0, (data[:80], elapsed)
    assert v.guard.failures.blocked("127.0.0.1") > 0


def test_a_slow_write_body_gets_408(make_viewer):
    v = make_viewer(body_timeout=1.0)
    cookie, csrf = login(v)
    data, elapsed = _drip_login(v, cookie=cookie, csrf=csrf, path="/api/viewer/prefs", method="PUT")
    assert data.startswith(b"HTTP/1.0 408") and elapsed <= 3.0
    assert v.guard.failures.blocked("127.0.0.1") == 0        # a session's 408 is not charged


def test_logins_spend_the_keys_limits(make_viewer):
    # I4: a login takes the key's rate budget and in-flight slot.
    v = make_viewer(principals=KeyedLimiter(2, 0.0))
    login(v, csrf=False)
    login(v, csrf=False)
    third = req(v, "POST", "/login", headers={"Origin": v.loopback}, body="token=ka")
    assert third.status == 429 and "Set-Cookie" not in third.headers
    busy = make_viewer(inflight=InFlight(1))
    busy.guard.inflight.acquire(ALICE.key_id)
    assert req(busy, "POST", "/login", headers={"Origin": busy.loopback}, body="token=ka").status == 429
    busy.guard.inflight.release(ALICE.key_id)
    assert req(busy, "POST", "/login", headers={"Origin": busy.loopback}, body="token=ka").status == 303


def test_one_key_evicts_only_its_own_sessions(v):
    # I4: logging in again and again cannot sign anyone else out.
    bob, _ = login(v, "kb", csrf=False)
    alice = [login(v, csrf=False)[0] for _ in range(viewer_module.VIEWER_MAX_SESSIONS_PER_KEY + 3)]
    assert req(v, "GET", "/api/viewer/prefs", headers={"Cookie": bob}).status == 200
    assert req(v, "GET", "/api/viewer/prefs", headers={"Cookie": alice[0]}).status == 401
    assert req(v, "GET", "/api/viewer/prefs", headers={"Cookie": alice[-1]}).status == 200


def test_a_file_swapped_after_the_check_is_not_served(make_viewer, work_tree, monkeypatch):
    # I5: the check and the read use one verified handle; a symlink swapped in between is refused.
    v = make_viewer()
    cookie, _ = login(v)
    token = work_tree / ".taskmaster" / "local" / "serve" / "operator.token"
    target = work_tree / "docs" / "a.md"
    original = viewer_module.tracked_file_path

    def check_then_swap(root, rel, tracked):
        checked = original(root, rel, tracked)
        target.unlink()
        _symlink_or_skip(token, target)
        return checked
    monkeypatch.setattr(viewer_module, "tracked_file_path", check_then_swap)
    resp = req(v, "GET", "/file/docs/a.md", headers={"Cookie": cookie})
    assert resp.status != 200 and "tm_op_secret" not in resp.text


def test_read_checked_refuses_a_swapped_file_or_directory(work_tree):
    from taskmaster.serve.tracked import read_checked, read_tracked_file
    secret_dir = work_tree / ".taskmaster" / "local" / "serve"
    (secret_dir / "a.md").write_text("tm_op_secret", encoding="utf-8")
    checked = (work_tree / "docs" / "a.md").resolve()
    assert read_checked(work_tree, checked) == checked.read_bytes()
    assert read_tracked_file(work_tree, "docs/a.md") == checked.read_bytes()
    assert read_tracked_file(work_tree, ".env") is None
    (work_tree / "docs").rename(work_tree / "docs_old")              # an ancestor swapped for a symlink
    try:
        os.symlink(secret_dir, work_tree / "docs", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable here: {exc}")
    with pytest.raises(PermissionError):
        read_checked(work_tree, checked)


def test_non_ascii_digit_length_is_a_400(v):
    # M1: "\xb2".isdigit() is True and int() then raised from admit, answering nothing.
    sock = socket.create_connection(("127.0.0.1", v.port), timeout=5)
    try:
        sock.sendall(f"PUT /api/viewer/prefs HTTP/1.1\r\nHost: {v.loopback_host}\r\n".encode()
                     + b"Content-Length: \xb2\r\n\r\n")
        assert sock.recv(4096).startswith(b"HTTP/1.0 400")
    finally:
        sock.close()


def test_a_malformed_login_form_is_a_charged_400(make_viewer):
    clock = Clock()
    v = make_viewer(clock=clock, failures=KeyedLimiter(1, 0.0, clock=clock))
    resp = req(v, "POST", "/login", headers={"Origin": v.loopback}, body="a=1&b=2&c=3&d=4&e=5")
    assert resp.status == 400 and v.guard.failures.blocked("127.0.0.1") > 0


def test_viewer_500s_carry_a_ref_not_the_exception_text(v, monkeypatch):
    from taskmaster.native_routing import viewer as native_viewer

    def failing(handler, method, database):
        handler._send_json(500, {"ok": False, "error": "KeyError: 'secret detail'"})
    monkeypatch.setattr(native_viewer, "write", failing)
    cookie, csrf = login(v)
    resp = prefs_put(v, session_headers(v, cookie, csrf))
    error = json.loads(resp.body)["error"]
    assert resp.status == 500 and error.startswith("internal error in the viewer (ref ") and "secret" not in error
