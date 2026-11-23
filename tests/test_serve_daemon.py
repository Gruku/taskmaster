# User intent: the in-process daemon serves tools over HTTP, outlives the idle timeout, shuts down gracefully and exits when its coordinator is lost.
import io
import json
import logging
import os
import signal
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from serve_helpers import call_tool, list_tool_names
from taskmaster import backlog_server as bs
from taskmaster.coordinator import client as client_module
from taskmaster.coordinator import service
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.ownership import ownership_held
from taskmaster.coordinator.protocol import HandshakeError
from taskmaster.coordinator.service import Coordinator
from taskmaster.serve import daemon as daemon_module
from taskmaster.serve.auth import ensure_operator_token
from taskmaster.serve.config import BindRefused, ServeConfig, ServeRefused, ServeUnavailable
from taskmaster.serve.daemon import ServeDaemon
from test_native_service_build import forbid_launch, request, root  # noqa: F401

FUTURE = {'version': '99.0.0', 'digest': 'c' * 32}
WATCHED = ("taskmaster.serve", "taskmaster.coordinator", "taskmaster.integrations.linear", "uvicorn.error")


@pytest.fixture
def served(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)   # restored even if close() fails
    monkeypatch.setenv("TASKMASTER_SERVICE_IDLE_SECONDS", "0.5")
    token, _ = ensure_operator_token(root)
    out = io.StringIO()
    config = ServeConfig(root=root, port=0, viewer_port=0, viewer_origins=("https://v.example",))
    daemon = ServeDaemon(config, stdout=out).start()
    codes = []
    thread = threading.Thread(target=lambda: codes.append(daemon.serve()), daemon=True)
    thread.start()
    assert daemon.ready.wait(30)
    yield SimpleNamespace(daemon=daemon, token=token, url=daemon.url, out=out, codes=codes, thread=thread, root=root)
    daemon.request_stop("test teardown")
    thread.join(30)
    assert not thread.is_alive()


def base(url):
    return url.rsplit("/mcp", 1)[0]


def serve_log(root):
    return (root / ".taskmaster/local/serve/serve.log").read_text(encoding="utf-8")


def task_count(s):
    return call_tool(s.url, s.token, "backlog_list_tasks")


def test_tool_call_over_http_against_the_store(served):
    added = call_tool(served.url, served.token, "backlog_add_task",
                      {"title": "Over HTTP", "epic": "test-epic", "phase": "dev"})
    assert not added.startswith("Error") and "[seq " in added
    assert "Over HTTP" in call_tool(served.url, served.token, "backlog_get_task", {"task_id": "test-epic-002"})


def test_unauthenticated_mcp_call_is_refused(served):
    before = task_count(served)
    for token in (None, "tm_op_wrong" + "x" * 30):
        with pytest.raises(httpx.HTTPStatusError) as exc:
            call_tool(served.url, token, "backlog_list_tasks")
        assert exc.value.response.status_code == 401
    assert task_count(served) == before


def test_daemon_owns_the_coordinator_in_process(served):
    record = json.loads((served.root / ".taskmaster/local/coordinator/discovery.json").read_text())
    assert record["pid"] == os.getpid() and record["nonce"] == served.daemon.coordinator.nonce
    assert served.daemon.coordinator.server.server_address[0] == "127.0.0.1"
    assert ownership_held(served.root)


def test_daemon_outlives_the_idle_timeout(served, monkeypatch):
    def raising(self, seconds):
        raise AssertionError("serve must never consult idle expiry")
    monkeypatch.setattr(service.Coordinator, "idle_expired", raising)
    nonce = served.daemon.coordinator.nonce
    time.sleep(1.5)
    assert not served.daemon.coordinator.stopping.is_set()
    assert ownership_held(served.root)
    record = json.loads((served.root / ".taskmaster/local/coordinator/discovery.json").read_text())
    assert record["nonce"] == nonce
    assert call_tool(served.url, served.token, "backlog_list_tasks")


def test_remote_client_cannot_trigger_retire(served, monkeypatch):
    names = list_tool_names(served.url, served.token)
    assert not [n for n in names if "retire" in n or "shutdown" in n]
    r = httpx.post(base(served.url) + "/rpc", headers={"Authorization": f"Bearer {served.token}"},
                   json={"method": "retire"})
    assert r.status_code == 404
    assert Client(served.root).autostart is False
    nonce = served.daemon.coordinator.nonce
    forbid_launch(monkeypatch)
    newer = Client(served.root)
    newer.autostart = True
    newer.build = FUTURE
    with pytest.raises(HandshakeError, match="refused to retire"):
        newer.execute(request(newer))
    assert served.daemon.coordinator.nonce == nonce and not served.daemon.coordinator.stopping.is_set()
    assert call_tool(served.url, served.token, "backlog_list_tasks")


def test_ready_line_is_json_without_the_token(served):
    line = json.loads(served.out.getvalue().splitlines()[0])
    assert line["event"] == "ready" and line["url"] == served.daemon.url and line["pid"] == os.getpid()
    assert line["token_file"].endswith("operator.token")
    assert served.token not in served.out.getvalue()


def test_ready_line_reports_the_server_root(served):
    line = json.loads(served.out.getvalue().splitlines()[0])
    assert line["root"] == line["server_root"] == str(served.root.resolve())


def test_serve_log_records_start_and_refusals_without_tokens(served):
    with pytest.raises(httpx.HTTPStatusError):
        call_tool(served.url, None, "backlog_list_tasks")
    log = serve_log(served.root)
    assert "starting:" in log and "missing token" in log and served.token not in log


def test_signal_stops_gracefully(served):
    served.daemon._on_signal(signal.SIGINT, None)
    served.thread.join(30)
    assert not served.thread.is_alive()
    assert served.codes == [0]
    assert not ownership_held(served.root)
    assert served.daemon.coordinator.stopping.is_set()
    assert client_module.AUTOSTART_ALLOWED is True
    log = serve_log(served.root)
    assert "stop requested (SIGINT)" in log and "stopped (exit 0)" in log
    served.daemon.request_stop("again")      # a harmless no-op on a closed daemon


def test_committed_write_survives_graceful_stop(served):
    client = Client(served.root, autostart=False)
    assert client.execute(request(client))["receipt"]["affected"]
    served.daemon.request_stop()
    served.thread.join(30)
    assert not served.thread.is_alive()
    with Coordinator(served.root):
        assert Client(served.root, autostart=False).receipt("build-tests", "one")["state"] == "committed"


def test_lost_coordinator_exits_75(served):
    served.daemon.coordinator.stop()
    served.thread.join(30)
    assert not served.thread.is_alive()
    assert served.codes == [75]
    assert "coordinator stopped" in serve_log(served.root)
    assert not ownership_held(served.root)


def test_outer_middleware_sees_requests_before_auth(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    seen = []

    class Recorder:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                seen.append(scope["path"])
            await self.app(scope, receive, send)
    ensure_operator_token(root)
    daemon = ServeDaemon(ServeConfig(root=root, port=0), stdout=io.StringIO(), outer_middleware=[Recorder]).start()
    codes = []
    thread = threading.Thread(target=lambda: codes.append(daemon.serve()), daemon=True)
    thread.start()
    try:
        assert daemon.ready.wait(30)
        r = httpx.post(daemon.url, json={}, headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 401 and seen == ["/mcp"]
    finally:
        daemon.request_stop("test")
        thread.join(30)


# -- start() and its refusals --------------------------------------------------------------------

def test_server_module_is_loaded_before_the_coordinator_starts(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    order = []
    real_load = daemon_module.load_server_module
    real_start = daemon_module.ServeCoordinator.start

    def load(r):
        order.append("server")
        return real_load(r)

    def start(self):
        order.append("coordinator")
        return real_start(self)
    monkeypatch.setattr(daemon_module, "load_server_module", load)
    monkeypatch.setattr(daemon_module.ServeCoordinator, "start", start)
    daemon = ServeDaemon(ServeConfig(root=root, port=0)).start()
    try:
        assert order == ["server", "coordinator"]
    finally:
        daemon.close()


def snapshot():
    return {n: (logging.getLogger(n).level, logging.getLogger(n).propagate, len(logging.getLogger(n).handlers))
            for n in WATCHED}


def test_close_restores_logging(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    before = snapshot()
    daemon = ServeDaemon(ServeConfig(root=root, port=0)).start()
    assert snapshot() != before
    daemon.close()
    assert snapshot() == before


def test_logging_captures_coordinator_child_loggers(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    daemon = ServeDaemon(ServeConfig(root=root, port=0)).start()
    try:
        logging.getLogger("taskmaster.coordinator.git").warning("probe-xyz")
        for handler in logging.getLogger("taskmaster.serve").handlers:
            handler.flush()
        assert "probe-xyz" in serve_log(root)
    finally:
        daemon.close()


def test_non_loopback_bind_is_refused_before_touching_the_store(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    with pytest.raises(BindRefused):
        ServeDaemon(ServeConfig(root=root, host="0.0.0.0", port=0)).start()
    assert not (root / ".taskmaster/local/serve").exists()
    assert not ownership_held(root)
    assert client_module.AUTOSTART_ALLOWED is True


def test_insecure_bind_starts_with_a_warning(root, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    monkeypatch.setattr(daemon_module, "bind_socket", lambda host, port: socket.create_server(("127.0.0.1", 0)))
    daemon = ServeDaemon(ServeConfig(root=root, host="0.0.0.0", port=0, insecure_bind=True)).start()
    try:
        assert "unencrypted" in capsys.readouterr().err
        for handler in logging.getLogger("taskmaster.serve").handlers:
            handler.flush()
        assert "unencrypted" in serve_log(root)
    finally:
        daemon.close()


def test_start_refuses_when_another_coordinator_owns_the_store(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    with Coordinator(root) as plain:
        with pytest.raises(ServeUnavailable, match="another coordinator") as exc:
            ServeDaemon(ServeConfig(root=root, port=0)).start()
        assert exc.value.exit_code == 69
        assert "taskmaster coordinator stop --root" in str(exc.value) and "stop that daemon" in str(exc.value)
        assert client_module.AUTOSTART_ALLOWED is True
        assert not plain.stopping.is_set() and ownership_held(root)


def test_start_refuses_a_root_without_a_store(tmp_path):
    with pytest.raises(ServeRefused, match="not a taskmaster project"):
        ServeDaemon(ServeConfig(root=tmp_path, port=0)).start()


def test_server_module_must_point_at_the_root(root, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    monkeypatch.setattr(bs, "ROOT", tmp_path / "elsewhere")
    with pytest.raises(ServeRefused, match="pointed at"):
        ServeDaemon(ServeConfig(root=root, port=0)).start()
    assert not ownership_held(root)
    assert client_module.AUTOSTART_ALLOWED is True


def test_failed_start_undoes_everything(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    before = snapshot()
    taken = []
    real_bind = daemon_module.bind_socket

    def bind(host, port):
        sock = real_bind(host, port)
        taken.append(sock.getsockname()[1])
        return sock

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(daemon_module, "bind_socket", bind)
    monkeypatch.setattr(daemon_module, "build_app", boom)
    with pytest.raises(RuntimeError, match="boom"):
        ServeDaemon(ServeConfig(root=root, port=0)).start()
    assert not ownership_held(root)
    with socket.create_server(("127.0.0.1", taken[0])):
        pass
    assert client_module.AUTOSTART_ALLOWED is True
    assert snapshot() == before


# -- team-server-006 Step 9: the hardening layers, composed --------------------------------------

import contextlib
import http.client
import re
import sqlite3
import urllib.request
from pathlib import Path

from serve_helpers import closed_within, connect, raw_initialize, read_response
from taskmaster.serve import auth as auth_module
from taskmaster.serve.protocol import DeadlineH11Protocol
from taskmaster.serve.sanitize import SanitizeErrors

ACCEPT = "application/json, text/event-stream"


@contextlib.contextmanager
def running_daemon(root, monkeypatch, **config):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    token, _ = ensure_operator_token(root)
    out = io.StringIO()
    daemon = ServeDaemon(ServeConfig(root=root, port=0, **config), stdout=out).start()
    codes = []
    thread = threading.Thread(target=lambda: codes.append(daemon.serve()), daemon=True)
    thread.start()
    try:
        assert daemon.ready.wait(30)
        yield SimpleNamespace(daemon=daemon, token=token, url=daemon.url, out=out, root=root)
    finally:
        daemon.request_stop("test teardown")
        thread.join(30)
        assert not thread.is_alive()


def ready_line(s):
    return json.loads(s.out.getvalue().splitlines()[0])


def test_daemon_applies_the_hardened_uvicorn_options(served):
    config = served.daemon._server.config
    assert issubclass(config.http, DeadlineH11Protocol) and config.http.header_timeout == 10.0
    assert config.ws == "none" and config.limit_concurrency == 128
    assert config.forwarded_allow_ips == ["127.0.0.1", "::1"]


def test_daemon_refuses_an_oversized_body_before_auth(served):
    sock = connect(served.url)
    try:
        started = time.monotonic()
        sock.sendall(b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 3000000\r\n\r\n")
        status, headers, _ = read_response(sock, timeout=1.0)
        assert status == 413 and time.monotonic() - started < 1.0 and re.fullmatch(r"[0-9a-f]{12}",
                                                                                    headers["x-request-id"])
    finally:
        sock.close()


def test_daemon_closes_after_an_early_401(served):
    sock = connect(served.url)
    try:
        sock.sendall(b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 1000\r\n\r\n0123456789")
        status, headers, _ = read_response(sock)
        assert status == 401 and headers["connection"] == "close"
        assert closed_within(sock, 3.0)
    finally:
        sock.close()


def test_daemon_rate_limits_the_operator(root, monkeypatch):  # noqa: F811
    with running_daemon(root, monkeypatch, rate_burst=3, rate_per_minute=1) as s:
        assert [raw_initialize(s.url, s.token).status_code for _ in range(3)] == [200, 200, 200]
        refused = raw_initialize(s.url, s.token)
        assert refused.status_code == 429 and refused.json() == {"error": "rate limited"}


def _open_stream(s, session_id):
    sock = connect(s.url)
    host = s.url.split("://", 1)[1].split("/", 1)[0]
    sock.sendall((f"GET /mcp HTTP/1.1\r\nHost: {host}\r\nAuthorization: Bearer {s.token}\r\nAccept: {ACCEPT}\r\n"
                  f"Mcp-Session-Id: {session_id}\r\nMcp-Protocol-Version: 2025-06-18\r\n\r\n").encode())
    sock.settimeout(5)
    head = sock.recv(4096)
    return sock, int(head.split(b" ", 2)[1])


def test_daemon_inflight_cap(root, monkeypatch):  # noqa: F811
    with running_daemon(root, monkeypatch, max_inflight=2, max_connections=16) as s:
        sessions = [raw_initialize(s.url, s.token).headers["mcp-session-id"] for _ in range(2)]
        streams = []
        try:
            for sid in sessions:
                sock, status = _open_stream(s, sid)
                streams.append(sock)
                assert status == 200
            sock, status = _open_stream(s, sessions[0])
            streams.append(sock)
            assert status == 429
            streams[0].close()
            deadline = time.monotonic() + 2
            while raw_initialize(s.url, s.token).status_code == 429:
                assert time.monotonic() < deadline, "closing a stream did not free its slot"
                time.sleep(0.1)
        finally:
            for sock in streams:
                sock.close()


def _call(url, token, name, arguments=None):
    from fastmcp import Client as MCPClient

    async def go():
        async with MCPClient(url, auth=token) as c:
            return (await c.call_tool(name, arguments or {}, raise_on_error=False)).content[0].text
    import asyncio
    return asyncio.run(go())


def test_tool_exception_is_sanitized_over_the_real_server(served, monkeypatch):
    from taskmaster.native_routing import registry
    root = served.root
    real_route = registry.route

    def route(tool, *args, **kwargs):
        if tool == "backlog_status":
            raise sqlite3.OperationalError(f"no such table: x under {root}")
        return real_route(tool, *args, **kwargs)
    monkeypatch.setattr(registry, "route", route)
    text = _call(served.url, served.token, "backlog_status")
    assert text.startswith("Error: internal error in backlog_status (ref ") and str(root) not in text
    ref = text.split("(ref ", 1)[1].split(")", 1)[0]
    for handler in logging.getLogger("taskmaster.serve").handlers:
        handler.flush()
    log = serve_log(root)
    line = next(line for line in log.splitlines() if f"ref={ref}" in line)
    assert "principal=operator" in line and "OperationalError" in line


def test_sanitizer_is_removed_on_close(root, monkeypatch):  # noqa: F811
    with running_daemon(root, monkeypatch) as s:
        assert any(isinstance(m, SanitizeErrors) for m in bs.mcp._inner.middleware)
    assert not any(isinstance(m, SanitizeErrors) for m in bs.mcp._inner.middleware)
    from fastmcp import FastMCP
    from fastmcp import Client as MCPClient
    import asyncio
    toy = FastMCP("t")

    @toy.tool
    def boom() -> str:
        raise sqlite3.OperationalError("no such table: x")

    async def go():
        async with MCPClient(toy) as c:
            return (await c.call_tool("boom", {}, raise_on_error=False)).content[0].text
    assert "no such table" in asyncio.run(go())
    assert s.daemon._sanitizer is None


@pytest.mark.parametrize("before", [None, "0"])
def test_serve_env_is_set_and_restored(root, monkeypatch, before):  # noqa: F811
    if before is None:
        monkeypatch.delenv("TASKMASTER_SERVE", raising=False)
    else:
        monkeypatch.setenv("TASKMASTER_SERVE", before)
    with running_daemon(root, monkeypatch, viewer_port=0):
        assert os.environ["TASKMASTER_SERVE"] == "1"
    assert os.environ.get("TASKMASTER_SERVE") == before


def test_viewer_port_is_restored(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bs, "VIEWER_PORT", 54321)
    with running_daemon(root, monkeypatch, viewer_port=0) as s:
        assert bs.VIEWER_PORT == s.daemon.viewer_port != 54321
        assert bs.VIEWER_PUBLIC_URL is None
    assert bs.VIEWER_PORT == 54321

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(daemon_module, "build_app", boom)
    with pytest.raises(RuntimeError, match="boom"):
        ServeDaemon(ServeConfig(root=root, port=0, viewer_port=0, viewer_origins=("https://v.example",))).start()
    assert bs.VIEWER_PORT == 54321 and bs.VIEWER_PUBLIC_URL is None


def test_daemon_never_assigns_origin_provider(served):
    assert client_module.ORIGIN_PROVIDER is auth_module.request_key_id
    serve_dir = Path(daemon_module.__file__).parent
    for name in ("viewer.py", "guard.py", "limits.py", "sanitize.py", "protocol.py"):
        text = (serve_dir / name).read_text(encoding="utf-8")
        assert "ORIGIN_PROVIDER" not in text and "VIEWER_KEY_ID.set" not in text, name


def test_daemon_installs_and_restores_the_session_hooks(root, monkeypatch):  # noqa: F811
    """team-server-003: the daemon is the only assigner of SESSION_PROVIDER and claims.CONNECTED, and
    restores both on close and after a start that fails once they are installed."""
    from taskmaster.native import claims
    from taskmaster.serve import app as app_module
    assert bs.SESSION_PROVIDER is None and claims.CONNECTED is None
    prunes = []
    real_sweep = app_module._sweep

    async def spy_sweep(manager, registry, seconds, sessions=None):
        if sessions is not None:
            real_prune = sessions.prune
            sessions.prune = lambda: prunes.append(1) or real_prune()
        await real_sweep(manager, registry, 0.1, sessions)

    monkeypatch.setattr(app_module, "_sweep", spy_sweep)
    with running_daemon(root, monkeypatch) as s:
        assert bs.SESSION_PROVIDER == s.daemon.sessions.current
        assert claims.CONNECTED == s.daemon.sessions.connected
        deadline = time.monotonic() + 10
        while not prunes and time.monotonic() < deadline:
            time.sleep(0.05)
        assert prunes, "the sweep never pruned sessions"
    assert bs.SESSION_PROVIDER is None and claims.CONNECTED is None

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(daemon_module, "build_app", boom)
    with pytest.raises(RuntimeError, match="boom"):
        ServeDaemon(ServeConfig(root=root, port=0)).start()
    assert bs.SESSION_PROVIDER is None and claims.CONNECTED is None


def _viewer(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
        resp = conn.getresponse()
        return resp.status, resp.headers, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


def test_daemon_viewer_requires_login_and_sends_no_cors(served):
    port = served.daemon.viewer_port
    assert ready_line(served)["viewer_url"] == f"http://127.0.0.1:{port}/"
    responses = []
    status, headers, _ = _viewer(port, "GET", "/")
    responses.append(headers)
    assert status == 303 and headers["Location"] == "/login"
    origin = f"http://127.0.0.1:{port}"
    status, headers, _ = _viewer(port, "POST", "/login", {"Origin": origin}, f"token={served.token}")
    responses.append(headers)
    assert status == 303
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    status, headers, page = _viewer(port, "GET", "/", {"Cookie": cookie})
    responses.append(headers)
    csrf = re.search(r'name="taskmaster-csrf" content="([^"]+)"', page).group(1)
    status, headers, body = _viewer(port, "PUT", "/api/viewer/prefs",
                                    {"Cookie": cookie, "Origin": origin, "X-Taskmaster-CSRF": csrf,
                                     "Content-Type": "application/json"}, json.dumps({"theme": "light"}))
    responses.append(headers)
    assert status == 200, body
    status, headers, _ = _viewer(port, "POST", "/login", {"Origin": "https://v.example", "Host": "v.example",
                                                          "X-Forwarded-For": "203.0.113.7"}, f"token={served.token}")
    responses.append(headers)
    assert status == 403 and "Set-Cookie" not in headers
    for headers in responses:
        assert not [n for n in headers if n.lower().startswith("access-control-")]
    solo = served.daemon._saved_viewer_port
    if solo:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{solo}/api/identity", timeout=2) as resp:
                assert resp.headers["Access-Control-Allow-Origin"] != "*"      # local-server-006: no wildcard CORS
        except OSError:
            pass                              # the test process's solo viewer may not be running


def test_viewer_port_taken_is_exit_69(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    monkeypatch.delenv("TASKMASTER_SERVE", raising=False)
    ensure_operator_token(root)
    with socket.create_server(("127.0.0.1", 0)) as taken:
        with pytest.raises(ServeUnavailable, match="cannot bind the viewer") as exc:
            ServeDaemon(ServeConfig(root=root, port=0, viewer_port=taken.getsockname()[1])).start()
    assert exc.value.exit_code == 69
    assert not ownership_held(root)
    assert "TASKMASTER_SERVE" not in os.environ


def test_viewer_stops_on_close(root, monkeypatch):  # noqa: F811
    with running_daemon(root, monkeypatch, viewer_port=0) as s:
        port = s.daemon.viewer_port
        assert _viewer(port, "GET", "/login")[0] == 200
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()


def test_no_viewer(root, monkeypatch):  # noqa: F811
    with running_daemon(root, monkeypatch) as s:
        assert ready_line(s)["viewer_url"] is None
        assert not [t for t in threading.enumerate() if t.name == "taskmaster-serve-viewer"]
