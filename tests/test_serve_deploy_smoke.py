# User intent: prove the shipped Caddyfile, the real daemon and a non-loopback client behave as the team-server designs
# assumed (host-local guard, failure budget, SSE, body caps, keep-alive, path allow-list) and that the shipped systemd
# unit verifies and scores within the exposure threshold; skipped unless TASKMASTER_CADDY names a Caddy (team-server-016 §3.5).
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import serve_helpers
from taskmaster.serve.auth import ensure_operator_token
from taskmaster.serve.principals import add_user, revoke_user

REPO = Path(__file__).resolve().parents[1]
CADDYFILE = REPO / "deploy" / "caddy" / "Caddyfile"
UNIT = REPO / "deploy" / "systemd" / "taskmaster.service"
CADDY = os.environ.get("TASKMASTER_CADDY")
MUTATION = os.environ.get("TASKMASTER_SMOKE_MUTATION", "")
HOST = "tm.localhost"
VIEWER_HOST = "viewer.tm.localhost"
SNI = {"sni_hostname": HOST}
EXPOSURE_THRESHOLD = 2.0          # design Q6; 015 may tighten it

needs_caddy = pytest.mark.skipif(not CADDY, reason="TASKMASTER_CADDY does not name a Caddy binary")
pytestmark = [pytest.mark.slow, pytest.mark.xdist_group("heavy_processes")]


# --- rendering the shipped Caddyfile ------------------------------------------------------------------------------

def _active(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def _once(text: str, old: str, new: str, *, regex: bool = False, count: int = 1) -> str:
    """Replace a shipped fragment exactly `count` times, in non-comment text; drift in the shipped file fails here."""
    found = len(re.findall(old, text, re.M)) if regex else text.count(old)
    assert found == count, f"expected exactly {count} of {old!r} in the shipped Caddyfile, found {found}"
    return re.sub(old, new, text, flags=re.M) if regex else text.replace(old, new)


def render_caddyfile(*, log_path: Path, https_port: int, http_port: int, upstream_port: int, viewer_port: int = 0,
                     mutation: str = "") -> str:
    text = _active(CADDYFILE.read_text(encoding="utf-8"))
    text = _once(text, r"^tm\.example\.com \{", f"{HOST} {{", regex=True)
    text = _once(text, r"^viewer\.tm\.example\.com \{", f"{VIEWER_HOST} {{", regex=True)
    text = _once(text, r"^\ttls \{\n\t\tissuer acme \{\n\t\t\tdisable_http_challenge\n\t\t\}\n\t\}$", "\ttls internal",
                 regex=True, count=2)
    text = _once(text, "\tadmin off", f"\tadmin off\n\thttps_port {https_port}\n\thttp_port {http_port}\n\tskip_install_trust")
    text = _once(text, "/var/log/caddy/taskmaster-access.log", str(log_path))
    text = _once(text, "/var/log/caddy/taskmaster-viewer-access.log", str(log_path.with_name("viewer-access.log")))
    text = _once(text, "reverse_proxy 127.0.0.1:7471", f"reverse_proxy 127.0.0.1:{viewer_port}")
    text = _once(text, "root * /opt/taskmaster/current/viewer", f"root * {(REPO / 'viewer').as_posix()}")
    if mutation == "viewer-no-conns":    # V9 control: the viewer queue removed
        text = _once(text, "\t\t\t\tmax_conns_per_host 16\n", "")
    elif mutation == "viewer-no-buffers":  # V10 control: the buffers removed
        text = _once(text, "\t\t\trequest_buffers 64KiB\n\t\t\tresponse_buffers 512KiB\n", "")
    text = _once(text, "\t\t\tidle 2m", "\t\t\tidle 3s")
    text = _once(text, "reverse_proxy 127.0.0.1:7470", f"reverse_proxy 127.0.0.1:{upstream_port}")
    if mutation == "forward-strip":      # case 2
        text = _once(text, f"reverse_proxy 127.0.0.1:{upstream_port} {{",
                     f"reverse_proxy 127.0.0.1:{upstream_port} {{\n\t\t\theader_up -X-Forwarded-For\n\t\t\theader_up Host 127.0.0.1")
    elif mutation == "xff-passthrough":  # case 5: the client's own X-Forwarded-For replaces Caddy's
        text = _once(text, f"reverse_proxy 127.0.0.1:{upstream_port} {{",
                     f"reverse_proxy 127.0.0.1:{upstream_port} {{\n\t\t\theader_up X-Forwarded-For {{http.request.header.X-Forwarded-For}}")
    elif mutation == "encode":           # case 6: compression can buffer an event stream
        text = _once(text, f"\theader -Server\n\tlog {{\n\t\toutput file {log_path}",
                     f"\theader -Server\n\tencode gzip\n\tlog {{\n\t\toutput file {log_path}")
    elif mutation == "trusted-proxies":  # benign here: uvicorn takes the right-most untrusted address
        text = _once(text, "\t\tprotocols h1 h2", "\t\tprotocols h1 h2\n\t\ttrusted_proxies static private_ranges")
    elif mutation == "read-body":        # case 6
        text = _once(text, "\t\t\tread_header 10s", "\t\t\tread_header 10s\n\t\t\tread_body 3s")
    elif mutation == "no-read-header":   # case 8
        text = _once(text, "\t\t\tread_header 10s\n", "")
    elif mutation == "wide-matcher":     # case 10
        text = _once(text, "path /mcp /mcp/* /read/v1 /read/v1/* /write/v1 /write/v1/*", "path /*")
    elif mutation == "long-keepalive":   # case 11
        text = _once(text, "keepalive 4s", "keepalive 2m")
    return text + "\n"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def own_address() -> str | None:
    """This machine's non-loopback address, so the forwarded peer is not loopback as in production."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            address = sock.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") else address


# --- the running world ---------------------------------------------------------------------------------------------

def unit_linger_flags() -> list[str]:
    import shlex
    from taskmaster.serve.cli import build_parser
    text = UNIT.read_text(encoding="utf-8").replace(chr(92) + chr(10), " ")
    line = next(l for l in text.splitlines() if l.startswith("ExecStart="))
    args = build_parser().parse_args(shlex.split(line[len("ExecStart="):])[1:])
    return ["--linger-bytes", str(args.linger_bytes), "--linger-seconds", str(args.linger_seconds)]


def serve_command() -> list[str]:
    script = Path(sys.executable).parent / "taskmaster-serve"
    return [str(script)] if script.exists() else [sys.executable, "-m", "taskmaster.serve"]


class _SniClient(httpx.Client):
    """An httpx client that sends SNI viewer.tm.localhost on every request (httpx takes it per request, not per client)."""

    def request(self, method, url, **kwargs):
        if kwargs.get("extensions") is None:
            kwargs["extensions"] = {"sni_hostname": VIEWER_HOST}
        return super().request(method, url, **kwargs)


class World:
    def __init__(self, tmp: Path, root: Path, address: str):
        self.tmp, self.root, self.address = tmp, root, address
        self.https_port, self.http_port, self.viewer_port = free_port(), free_port(), free_port()
        self.member_key = add_user(root, "ann", name="Ann", emails=["ann@example.com"])[0]
        self.operator = ensure_operator_token(root)[0]
        self.daemon = None
        self.daemon_port = 0
        self.extra: list[str] = unit_linger_flags()    # the draining the shipped unit configures
        self.caddy = None
        self.ca = tmp / "data" / "caddy" / "pki" / "authorities" / "local" / "root.crt"
        self.serve_log = root / ".taskmaster" / "local" / "serve" / "serve.log"
        if MUTATION == "daemon-cap":
            self.extra = ["--max-body-bytes", str(8 * 1024 * 1024)]
        elif MUTATION == "linger0":
            self.extra = ["--linger-seconds", "0"]
        elif MUTATION == "linger-default":    # the daemon's own 64 KiB drain: a declared oversize body becomes a 502
            self.extra = []

    def start_daemon(self, *, allowed=True):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir(exist_ok=True)
        stderr_path = self.tmp / f"serve-{time.monotonic_ns()}.stderr"
        env = {k: v for k, v in os.environ.items() if k not in ("TASKMASTER_ROOT", "ANTHROPIC_API_KEY")}
        command = [*serve_command(), "--root", str(self.root), "--host", "127.0.0.1",
                   "--port", str(self.daemon_port), "--viewer-port", str(self.viewer_port),
                   "--viewer-origin", f"https://{VIEWER_HOST}:{self.https_port}", *self.extra]
        if allowed:
            command += ["--allowed-host", f"{HOST}:{self.https_port}"]
        with open(stderr_path, "wb") as stderr:
            self.daemon = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr, env=env, cwd=str(elsewhere),
                                           start_new_session=True)
        ready = serve_helpers.read_ready_line(self.daemon, timeout=120, stderr_path=stderr_path)
        self.daemon_port = int(ready["url"].split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[1])
        return ready

    def stop_daemon(self):
        child, self.daemon = self.daemon, None
        if child is None or child.poll() is not None:
            return
        child.send_signal(signal.SIGTERM)
        try:
            child.wait(30)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(10)
        with contextlib.suppress(Exception):
            child.stdout.close()

    def restart_daemon(self, *, allowed=True):
        # No retry: the viewer's fixed port must rebind at once over TIME_WAIT, as `systemctl restart` needs (028 gate I1).
        self.stop_daemon()
        return self.start_daemon(allowed=allowed)

    def start_caddy(self):
        (self.tmp / "data").mkdir(exist_ok=True)
        (self.tmp / "config").mkdir(exist_ok=True)
        (self.tmp / "home").mkdir(exist_ok=True)
        caddyfile = self.tmp / "Caddyfile"
        caddyfile.write_text(render_caddyfile(log_path=self.tmp / "access.log", https_port=self.https_port,
                                              http_port=self.http_port, upstream_port=self.daemon_port,
                                              viewer_port=self.viewer_port, mutation=MUTATION), encoding="utf-8")
        env = {**os.environ, "HOME": str(self.tmp / "home"), "XDG_DATA_HOME": str(self.tmp / "data"),
               "XDG_CONFIG_HOME": str(self.tmp / "config")}
        self.caddy_log = open(self.tmp / "caddy.log", "wb")
        self.caddy = subprocess.Popen([CADDY, "run", "--config", str(caddyfile), "--adapter", "caddyfile"],
                                      stdout=self.caddy_log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            assert self.caddy.poll() is None, (self.tmp / "caddy.log").read_text(errors="replace")[-3000:]
            if self.ca.exists():
                with contextlib.suppress(OSError, ssl.SSLError):
                    with self.tls() as sock:
                        sock.getpeername()
                        return
            time.sleep(0.5)
        raise AssertionError("Caddy did not serve tm.localhost in 60 s:\n" + (self.tmp / "caddy.log").read_text(errors="replace")[-3000:])

    def stop_caddy(self):
        if self.caddy and self.caddy.poll() is None:
            self.caddy.send_signal(signal.SIGTERM)
            try:
                self.caddy.wait(20)
            except subprocess.TimeoutExpired:
                self.caddy.kill()
        if getattr(self, "caddy_log", None):
            self.caddy_log.close()

    # a raw TLS socket to Caddy with SNI tm.localhost, from this machine's non-loopback address
    def tls(self, timeout=15.0) -> ssl.SSLSocket:
        context = ssl.create_default_context(cafile=str(self.ca))
        raw = socket.create_connection((self.address, self.https_port), timeout=timeout)
        return context.wrap_socket(raw, server_hostname=HOST)

    def client(self, **kwargs) -> httpx.Client:
        return httpx.Client(base_url=f"https://{self.address}:{self.https_port}", verify=str(self.ca),
                            headers={"Host": f"{HOST}:{self.https_port}"}, timeout=kwargs.pop("timeout", 30), **kwargs)

    def viewer_origin(self) -> str:
        return f"https://{VIEWER_HOST}:{self.https_port}"

    def vtls(self, timeout=15.0, alpn=None) -> ssl.SSLSocket:
        context = ssl.create_default_context(cafile=str(self.ca))
        if alpn:
            context.set_alpn_protocols(alpn)
        raw = socket.create_connection((self.address, self.https_port), timeout=timeout)
        return context.wrap_socket(raw, server_hostname=VIEWER_HOST)

    def vclient(self, **kwargs) -> httpx.Client:
        return _SniClient(base_url=f"https://{self.address}:{self.https_port}", verify=str(self.ca),
                          headers={"Host": f"{VIEWER_HOST}:{self.https_port}"}, timeout=kwargs.pop("timeout", 30), **kwargs)

    def vlogin(self, client, key, **extra) -> httpx.Response:
        return client.post("/login", content=f"token={key}",
                           headers={"Origin": self.viewer_origin(), "Content-Type": "application/x-www-form-urlencoded",
                                    **extra})

    def vsession(self, client, key):
        """Sign in through Caddy; (cookie header value, csrf token)."""
        done = self.vlogin(client, key)
        assert done.status_code == 303, (done.status_code, done.text)
        cookie = done.headers["set-cookie"].split(";", 1)[0]
        page = client.get("/", headers={"Cookie": cookie})
        assert page.status_code == 200, page.text
        csrf = re.search(r'<meta name="taskmaster-csrf" content="([^"]+)">', page.text).group(1)
        return cookie, csrf

    def direct(self) -> httpx.Client:
        return httpx.Client(base_url=f"http://127.0.0.1:{self.daemon_port}", timeout=30)

    def log_text(self) -> str:
        return self.serve_log.read_text(errors="replace") if self.serve_log.exists() else ""


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    if not CADDY:
        pytest.skip("TASKMASTER_CADDY does not name a Caddy binary")
    address = own_address()
    if address is None:
        pytest.skip("this machine has no non-loopback address, so the forwarded peer would be loopback")
    from native_twins import make_twins
    from taskmaster import backlog_server as bs
    tmp = tmp_path_factory.mktemp("smoke")
    patch = pytest.MonkeyPatch()
    twins = make_twins(tmp / "twins", patch, lambda: bs.backlog_add_task(title="Build task", epic="test-epic", phase="dev"),
                       visibility=None)
    with twins.at(twins.native):
        the = World(tmp, twins.native, address)
        try:
            the.start_daemon()
            the.start_caddy()
            yield the
        finally:
            the.stop_caddy()
            the.stop_daemon()
            patch.undo()


# --- MCP over the proxy --------------------------------------------------------------------------------------------

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def decode(response: httpx.Response) -> dict:
    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:])
        raise AssertionError(f"no data line in {response.text[:300]!r}")
    return response.json()


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}}}


def mcp_initialize(client: httpx.Client, key: str, *, extra=None) -> httpx.Response:
    return client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": f"Bearer {key}", **(extra or {})},
                       extensions=SNI)


def mcp_session(client: httpx.Client, key: str) -> str:
    opened = mcp_initialize(client, key)
    assert opened.status_code == 200, opened.text
    session = opened.headers["mcp-session-id"]
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {key}", "mcp-session-id": session}
    client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers, extensions=SNI)
    return session


def raw_http(sock, request: bytes, *, read_for=5.0) -> bytes:
    sock.sendall(request)
    sock.settimeout(read_for)
    data = b""
    deadline = time.monotonic() + read_for
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(65536)
        except (socket.timeout, ssl.SSLError, ConnectionError):
            break
        if not chunk:
            break
        data += chunk
        if b"\r\n\r\n" in data:
            break
    return data


def status_of(raw: bytes) -> int | None:
    match = re.match(rb"HTTP/1\.[01] (\d{3})", raw)
    return int(match.group(1)) if match else None


# --- the cases (design §3.5) --------------------------------------------------------------------------------------

@needs_caddy
def test_1_member_key_completes_a_tool_call_through_caddy(world):
    with world.client() as client:
        session = mcp_session(client, world.member_key)
        call = client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {world.member_key}",
                                            "mcp-session-id": session},
                           json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": "backlog_list_tasks", "arguments": {}}}, extensions=SNI)
        assert call.status_code == 200, call.text
        text = decode(call)["result"]["content"][0]["text"]
        assert not text.startswith("Error"), text
        assert "strict-transport-security" in call.headers and "server" not in call.headers


@needs_caddy
def test_2_the_bootstrap_token_is_refused_through_caddy(world):
    with world.client() as client:
        refused = mcp_initialize(client, world.operator)
    assert refused.status_code == 403 and "works only on the host" in refused.text, (refused.status_code, refused.text)


@needs_caddy
def test_3_hop_by_hop_names_cannot_strip_the_forwarding_signals(world):
    with world.client() as client:
        for connection in ("X-Forwarded-For", "Host", "X-Forwarded-For, Host"):
            refused = mcp_initialize(client, world.operator, extra={"Connection": connection})
            assert refused.status_code == 403, (connection, refused.status_code, refused.text)
    assert f"refused operator token from {world.address}" in world.log_text()


@needs_caddy
def test_4_the_bootstrap_token_still_works_on_the_host_path(world):
    with world.direct() as client:
        ok = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": f"Bearer {world.operator}"})
    assert ok.status_code == 200, ok.text


@needs_caddy
def test_6_an_idle_timeout_does_not_cut_an_open_sse_stream(world):
    with world.client() as client:
        session = mcp_session(client, world.member_key)
    with world.tls() as sock:
        request = (f"GET /mcp HTTP/1.1\r\nHost: {HOST}:{world.https_port}\r\nAuthorization: Bearer {world.member_key}\r\n"
                   f"Accept: text/event-stream\r\nmcp-session-id: {session}\r\n\r\n").encode()
        head = raw_http(sock, request, read_for=5.0)
        assert status_of(head) == 200 and b"text/event-stream" in head.lower(), head[:300]
        sock.settimeout(1.0)
        deadline = time.monotonic() + 8.0          # well past Caddy's idle 3s
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            assert chunk != b"", "Caddy closed the open event stream before 8 s"


@needs_caddy
def test_7a_a_declared_oversize_body_gets_the_daemons_413(world):
    with world.client() as client:
        refused = client.post("/mcp", content=b"x" * (3 * 1024 * 1024),
                              headers={**MCP_HEADERS, "Authorization": f"Bearer {world.member_key}"}, extensions=SNI)
    # The daemon refuses with 413 before reading the body and drains up to the unit's --linger-bytes (Caddy's cap), so
    # Caddy finishes sending and the client reads the 413 (with the 64 KiB default Caddy hits a broken pipe: 502).
    assert refused.status_code == 413, refused.status_code
    assert re.search(r"refused 413 POST /mcp", world.log_text())


@needs_caddy
def test_7b_a_chunked_oversize_body_gets_a_413_not_a_reset(world):
    def chunks():
        for _ in range(3 * 16):
            yield b"x" * (64 * 1024)
    with world.client() as client:
        refused = client.post("/mcp", content=chunks(),
                              headers={**MCP_HEADERS, "Authorization": f"Bearer {world.member_key}"}, extensions=SNI)
    assert refused.status_code == 413, refused.status_code


@needs_caddy
def test_8_trickled_headers_are_closed_at_the_header_deadline(world):
    with world.tls(timeout=30) as sock:
        sock.sendall(b"GET /mcp HTTP/1.1\r\nHost: " + f"{HOST}:{world.https_port}".encode() + b"\r\nX-Slow: ")
        sock.settimeout(1.0)
        started, closed = time.monotonic(), False
        while time.monotonic() - started < 16:
            try:
                sock.sendall(b"a")
                if sock.recv(4096) == b"":
                    closed = True
                    break
            except socket.timeout:
                continue
            except (ssl.SSLError, ConnectionError, BrokenPipeError):
                closed = True
                break
        assert closed, "Caddy kept a half-sent header open for 16 s"
        assert time.monotonic() - started < 14


@needs_caddy
@pytest.mark.parametrize("path", ["/rpc", "/", "/login", "/mcp/../x", "/MCP/../x", "/mcp/%2e%2e/x", "/read", "/read/v2/meta",
                                  "/write", "/reads/v1/meta", "/read/v1/../x", "/write/v1/%2e%2e/x"])
def test_10_only_the_public_prefixes_pass_caddy(world, path):
    with world.tls() as sock:
        raw = raw_http(sock, (f"GET {path} HTTP/1.1\r\nHost: {HOST}:{world.https_port}\r\nConnection: close\r\n\r\n").encode())
    assert status_of(raw) == 404, (path, raw[:200])


@needs_caddy
def test_11_a_pooled_upstream_connection_is_not_reused_after_uvicorn_closes_it(world):
    with world.client() as client:
        first = mcp_initialize(client, world.member_key)
        time.sleep(6)
        second = mcp_initialize(client, world.member_key)
    assert first.status_code == 200 and second.status_code == 200, (first.status_code, second.status_code)


@needs_caddy
def test_5_forged_forwarding_headers_do_not_buy_a_fresh_failure_budget(world):
    world.restart_daemon()                      # a fresh failure budget for this peer
    statuses = []
    with world.client() as client:
        for n in range(25):
            answer = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": "Bearer not-a-key",
                                                            "X-Forwarded-For": f"203.0.113.{n + 1}"}, extensions=SNI)
            statuses.append(answer.status_code)
    assert statuses[:20] == [401] * 20 and statuses[20] == 429, statuses
    log = world.log_text()
    assert world.address in log and "203.0.113." not in log


@needs_caddy
def test_9_a_revoked_keys_body_is_drained_so_the_client_reads_401(world):
    key = add_user(world.root, "bob", name="Bob", emails=["bob@example.com"])[0]
    revoke_user(world.root, "bob")
    world.restart_daemon()
    with world.client() as client:
        answer = client.post("/mcp", content=b"x" * (1900 * 1024),
                             headers={**MCP_HEADERS, "Authorization": f"Bearer {key}"}, extensions=SNI)
    assert answer.status_code == 401, answer.status_code


@needs_caddy
def test_12_a_daemon_without_the_matching_allowed_host_answers_421(world):
    world.restart_daemon(allowed=False)
    with world.client() as client:
        answer = mcp_initialize(client, world.member_key)
    assert answer.status_code == 421, (answer.status_code, answer.text)



# --- the viewer site (team-server-028 step 13, cases V1-V10) -----------------------------------------------------

def viewer_urls() -> list[str]:
    from taskmaster.serve import viewer as viewer_guard
    root = REPO / "viewer"
    return ["/static/v3/" + path.relative_to(root).as_posix() for path in sorted(root.rglob("*"))
            if path.is_file() and viewer_guard.static_asset_allowed(path.relative_to(root).as_posix())]


@needs_caddy
def test_v1_a_member_key_signs_in_through_the_viewer_site(world):
    world.restart_daemon()
    with world.vclient() as client:
        done = world.vlogin(client, world.member_key)
        assert done.status_code == 303, (done.status_code, done.text)
        cookie_line = done.headers["set-cookie"]
        assert cookie_line.startswith("__Host-tm_session=")
        assert all(flag in cookie_line for flag in ("Secure", "HttpOnly", "SameSite=Strict")), cookie_line
        cookie = cookie_line.split(";", 1)[0]
        page = client.get("/", headers={"Cookie": cookie})
        assert page.status_code == 200 and 'name="taskmaster-csrf"' in page.text
        assert client.get("/api/board", headers={"Cookie": cookie}).status_code == 200


@needs_caddy
def test_v2_the_operator_token_and_a_loopback_cookie_get_no_session_through_caddy(world):
    with world.vclient() as client:
        refused = world.vlogin(client, world.operator)
        assert refused.status_code == 403 and "works only on the host" in refused.text, (refused.status_code, refused.text)
    with httpx.Client(base_url=f"http://127.0.0.1:{world.viewer_port}", timeout=30) as direct:
        minted = direct.post("/login", content=f"token={world.operator}",
                             headers={"Origin": f"http://127.0.0.1:{world.viewer_port}",
                                      "Content-Type": "application/x-www-form-urlencoded"})
        assert minted.status_code == 303, (minted.status_code, minted.text)
        name_value = minted.headers["set-cookie"].split(";", 1)[0]
    value = name_value.split("=", 1)[1]
    with world.vclient() as client:
        for cookie in (name_value, f"__Host-tm_session={value}"):
            answer = client.get("/api/board", headers={"Cookie": cookie})
            assert answer.status_code in (401, 403), (cookie.split("=")[0], answer.status_code)


@needs_caddy
@pytest.mark.parametrize("path", ["/backlog.yaml", "/v3/dev/edit-demo", "/static/v3/dev/edit-demo.html",
                                  "/static/v3/package.json", "/api/session", "/mcp", "/read/v1/meta",
                                  "/v3/../backlog.yaml", "/API/../backlog.yaml"])
def test_v4_the_viewer_host_refuses_every_removed_path(world, path):
    with world.vtls() as sock:
        raw = raw_http(sock, (f"GET {path} HTTP/1.1\r\nHost: {VIEWER_HOST}:{world.https_port}\r\nConnection: close\r\n\r\n").encode())
    assert status_of(raw) == 404, (path, raw[:300])


@needs_caddy
@pytest.mark.parametrize("path", ["/login", "/api/board", "/static/v3/js/main.js"])
def test_v4_the_mcp_host_serves_no_viewer_path(world, path):
    with world.tls() as sock:
        raw = raw_http(sock, (f"GET {path} HTTP/1.1\r\nHost: {HOST}:{world.https_port}\r\nConnection: close\r\n\r\n").encode())
    assert status_of(raw) == 404, (path, raw[:300])


SECURITY_HEADERS = ("content-security-policy", "x-content-type-options", "x-frame-options", "referrer-policy",
                    "cross-origin-opener-policy", "cross-origin-resource-policy", "permissions-policy", "cache-control")


@needs_caddy
def test_v5_headers_arrive_through_caddy_as_the_app_sent_them(world):
    with world.vclient() as client:
        cookie, _ = world.vsession(client, world.member_key)
        client.cookies.clear()                       # the jar would sign /login in and redirect
        for path, headers in (("/login", {}), ("/", {"Cookie": cookie}), ("/static/v3/js/main.js", {})):
            through = client.get(path, headers=headers)
            assert through.status_code == 200, (path, through.status_code)
            assert "strict-transport-security" in through.headers and "server" not in through.headers, path
            with httpx.Client(base_url=f"http://127.0.0.1:{world.viewer_port}", timeout=30) as direct:
                app = direct.get(path, headers={k: v for k, v in headers.items()})
            if app.status_code == 200:      # the cookie is origin-bound, so '/' may be a login redirect on loopback
                for name in SECURITY_HEADERS:   # static assets come from Caddy's file_server: same headers as the app's
                    expected = app.headers.get(name)
                    if expected is not None:
                        expected = expected.replace(f"http://127.0.0.1:{world.viewer_port}", world.viewer_origin())
                    assert through.headers.get(name) == expected, (path, name)   # the origin is the one named in the request
            assert through.headers.get("content-security-policy"), path


@needs_caddy
def test_v6_an_oversize_viewer_body_is_refused_before_a_handler_runs(world):
    with world.vclient() as client:
        cookie, csrf = world.vsession(client, world.member_key)
        refused = client.post("/api/tasks", content=b"x" * (2 * 1024 * 1024),
                              headers={"Cookie": cookie, "Origin": world.viewer_origin(), "X-Taskmaster-CSRF": csrf,
                                       "Content-Type": "application/json"})
    assert refused.status_code in (413, 502), refused.status_code
    assert "200 POST /api/tasks" not in world.log_text()


@needs_caddy
def test_v7_a_second_viewer_request_after_six_seconds_is_not_a_stale_connection(world):
    with world.vclient() as client:
        first = client.get("/login")
        time.sleep(6)
        second = client.get("/login")
    assert first.status_code == 200 and second.status_code == 200, (first.status_code, second.status_code)


@needs_caddy
def test_v8_an_unknown_host_reaches_no_app_bytes(world):
    with world.vtls() as sock:
        raw = raw_http(sock, b"GET /login HTTP/1.1\r\nHost: evil.example\r\nConnection: close\r\n\r\n")
    print("V8 evidence: Caddy answered", status_of(raw), "for an unknown Host on the viewer SNI")
    body = raw.lower()
    assert b"<form" not in body and b"taskmaster" not in body and b"token" not in body, raw[:300]
    assert "evil.example" not in world.log_text()


def _curl_h2() -> str | None:
    curl = shutil.which("curl")
    if curl is None:
        return None
    return curl if "HTTP2" in subprocess.run([curl, "-V"], capture_output=True, text=True).stdout else None


def _curl_all(world, cookie: str, urls: list[str], tmp: Path) -> list[str]:
    config = tmp / "curl.cfg"
    config.write_text("".join(f'url = "https://{VIEWER_HOST}:{world.https_port}{u}"\noutput = "/dev/null"\n' for u in urls),
                      encoding="utf-8")
    done = subprocess.run([_curl_h2(), "--http2", "--parallel", "--parallel-max", "200", "--cacert", str(world.ca),
                           "--resolve", f"{VIEWER_HOST}:{world.https_port}:{world.address}", "-H", f"Cookie: {cookie}",
                           "-s", "-w", "%{http_code}\n", "--config", str(config)],
                          capture_output=True, text=True, timeout=180)
    return done.stdout.split()


@needs_caddy
def test_v9_a_page_load_over_h2_does_not_over_dial(world, tmp_path):
    if _curl_h2() is None:
        pytest.skip("curl is missing or lists no HTTP2 support")
    with world.vclient() as client:
        cookie, _ = world.vsession(client, world.member_key)
    urls = ["/", "/api/board", *viewer_urls()]
    assert len(urls) > 100, len(urls)
    for _ in range(3):
        codes = _curl_all(world, cookie, urls, tmp_path)
        bad = [c for c in codes if c != "200"]
        print(f"V9 evidence ({MUTATION or 'shipped'}): {len(codes)} answers, {len(bad)} not 200: {sorted(set(bad))}")
        if MUTATION == "viewer-no-conns":
            continue                       # the recorded control: counted, not asserted
        assert len(codes) == len(urls) and not bad, (len(codes), sorted(set(bad)))


def _stalled_h2_reader(world, streams: int, hold: float, out: dict, path: str, cookie: str | None = None) -> None:
    """One HTTP/2 connection that opens `streams` GETs of `path` with a zero stream window and never sends
    WINDOW_UPDATE: nothing it asked for can be written to it (design 028 case V10)."""
    import struct

    def frame(kind: int, flags: int, stream: int, payload: bytes = b"") -> bytes:
        return struct.pack(">I", len(payload))[1:] + bytes([kind, flags]) + struct.pack(">I", stream) + payload

    def literal(index: int, value: str) -> bytes:
        raw = value.encode()
        return bytes([index]) + bytes([len(raw)]) + raw

    sock = world.vtls(timeout=10, alpn=["h2"])
    if sock.selected_alpn_protocol() != "h2":
        out["error"] = "Caddy did not negotiate h2"
        return
    block = b"\x82\x87" + literal(0x04, path) + literal(0x01, f"{VIEWER_HOST}:{world.https_port}")
    if cookie:                                            # static-table name 32 (cookie): 15 in the prefix, then 17
        assert len(cookie) < 127, cookie
        block += b"\x0f\x11" + bytes([len(cookie)]) + cookie.encode()
    # The server's SETTINGS is acked once, when it arrives (below): a second, unsolicited ACK is a PROTOCOL_ERROR GOAWAY.
    sock.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
                 + frame(4, 0, 0, struct.pack(">HI", 2, 0) + struct.pack(">HI", 4, 0)))      # no push, stream window 0
    for n in range(streams):
        sock.sendall(frame(1, 0x05, 2 * n + 1, block))
    out["opened"] = streams
    sock.settimeout(1.0)
    started = time.monotonic()
    deadline = started + hold
    pending, frames = b"", out.setdefault("frames", [])
    try:
        while time.monotonic() < deadline:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as error:
                out["closed"] = type(error).__name__
                break
            if not data:
                out["closed"] = "eof"
                break
            pending += data
            while len(pending) >= 9 and len(pending) >= 9 + int.from_bytes(pending[:3], "big"):
                size, kind, flags = int.from_bytes(pending[:3], "big"), pending[3], pending[4]
                payload, pending = pending[9:9 + size], pending[9 + size:]
                frames.append((kind, flags, payload[:16].hex()) if kind in (3, 7) else (kind, flags))
                if kind == 4 and not flags & 1:                                              # ack the server's SETTINGS
                    sock.sendall(frame(4, 1, 0))
    finally:
        out["held"] = round(time.monotonic() - started, 1)
        sock.close()


BIG_TRACKED = "docs/v10-big.txt"


def _track_a_big_file(world) -> None:
    """A 256 KiB git-tracked file in the served root, so /file/ has an answer far larger than Go's per-stream buffer:
    only an answer that size can pin an upstream slot behind a stalled reader (a ~1 KB one is drained at once)."""
    root = world.root
    target = root / BIG_TRACKED
    if target.exists():
        return
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    if not (root / ".git").exists():
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, env=env, timeout=60)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(("v10 " * 64 + "\n") * 1024, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", BIG_TRACKED], check=True, env=env, timeout=60)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "v10", "--", BIG_TRACKED], check=True, env=env, timeout=60)


# The /file/ answer goes through the buffered proxy: the N1 proof that a stalled reader frees its upstream slot (the
# viewer-no-buffers control pins all 16). The font is what an unauthenticated client would stall on to pin Caddy's
# buffers (028 gate, Codex-I); it now comes from disk and touches no upstream at all.
@needs_caddy
@pytest.mark.parametrize("stalled", ["/file/" + BIG_TRACKED, "/static/v3/vendor/fonts/DMSans-Variable.woff2"])
def test_v10_a_stalled_h2_reader_holds_no_upstream_slot(world, stalled):
    import threading
    _track_a_big_file(world)
    world.restart_daemon()                                # a fresh tracked-file listing and failure budget
    with world.vclient() as client:
        cookie, _ = world.vsession(client, world.member_key)
        if stalled.startswith("/file/"):
            reader_key = add_user(world.root, "v10reader", name="V10", emails=["v10@example.com"])[0]
            reader_cookie, _ = world.vsession(client, reader_key)       # its own key: no shared per-key limits
            client.cookies.clear()
            assert len(client.get(stalled, headers={"Cookie": reader_cookie}).content) > 256 * 1024
        else:
            reader_cookie = None
    result: dict = {}
    reader = threading.Thread(target=_stalled_h2_reader, args=(world, 20, 18.0, result, stalled, reader_cookie),
                              daemon=True)
    stop = threading.Event()
    uploads = {}

    def slow_upload():
        with world.vtls(timeout=30) as sock:
            sock.sendall((f"POST /login HTTP/1.1\r\nHost: {VIEWER_HOST}:{world.https_port}\r\nOrigin: {world.viewer_origin()}\r\n"
                          "Content-Type: application/x-www-form-urlencoded\r\nContent-Length: 4096\r\n\r\n").encode())
            sent = 0
            while sent < 4096 and not stop.is_set():
                sock.sendall(b"a" * 100)
                sent += 100
                time.sleep(1.0)
            uploads["sent"] = sent

    uploader = threading.Thread(target=slow_upload, daemon=True)
    reader.start()
    time.sleep(2.0)                                       # let the 20 streams open
    uploader.start()
    latencies = []
    try:
        with world.vclient(timeout=5.5) as client:
            for _ in range(3):
                for path, headers in (("/login", {}), ("/api/board", {"Cookie": cookie})):
                    started = time.monotonic()
                    try:
                        status = client.get(path, headers=headers).status_code
                    except httpx.HTTPError as error:
                        status = type(error).__name__
                    latencies.append((path, status, round(time.monotonic() - started, 2)))
    finally:
        stop.set()
        reader.join(timeout=25)
        uploader.join(timeout=10)
    print(f"V10 evidence ({MUTATION or 'shipped'}, {stalled}): reader {result}, B latencies {latencies}")
    assert result.get("opened") == 20 and "closed" not in result and result.get("held", 0) >= 17.0, result
    if MUTATION == "viewer-no-buffers":
        return                                            # the recorded control: counted, not asserted
    assert all(status == 200 and took < 5.0 for _, status, took in latencies), latencies


STATIC_PROBES = ["/static/v3/js/../../README.md", "/static/v3/js/%2e%2e/%2e%2e/README.md", "/static/v3/js/_dormant/auto-mode.js",
                 "/static/v3/vendor/fonts/OFL-DMSans.txt", "/static/v3/js/components/edit/fields/chip-input-demo.js",
                 "/static/v3/js/components/edit/fields/chip-input-DEMO.js", "/static/v3//dev/edit-demo.html",
                 "/static/v3/vendor/../../viewer/index.html", "/static/v3/js/main.js%00", "/static/v3/js/",
                 "/static/v3/vendor/fonts/DMSans-Variable.woff2/..%2f..%2f..%2fREADME.md", "/static/v3/package.json",
                 "/static/v3/%6a%73/_dormant/auto-mode.js", "/static/v3/js/%5fdormant/auto-mode.js"]


@needs_caddy
def test_v11_public_static_assets_come_from_disk_and_nothing_else_does(world):
    """028 gate, Codex-I: every asset static_asset_allowed admits is Caddy's file_server answer (Last-Modified, which
    the app never sends), byte for byte; every file it refuses is still a 404, from the app."""
    from taskmaster.serve import viewer as viewer_guard
    root = REPO / "viewer"
    files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*")
                   if path.is_file() and "node_modules" not in path.parts)
    served = 0
    world.restart_daemon()                                # a fresh failure budget for this peer
    with world.vclient() as client:
        cookie, _ = world.vsession(client, world.member_key)   # refusals with a session are not charged
        client.cookies.clear()
        for rel in files:
            answer = client.get("/static/v3/" + rel, headers={"Cookie": cookie})
            if viewer_guard.static_asset_allowed(rel):
                assert answer.status_code == 200 and "last-modified" in answer.headers, (rel, answer.status_code)
                assert answer.content == (root / rel).read_bytes(), rel
                served += 1
            else:
                assert answer.status_code == 404 and "last-modified" not in answer.headers, (rel, answer.status_code)
        favicon = client.get("/favicon.ico")
        assert favicon.status_code == 200 and "last-modified" in favicon.headers
        assert favicon.content == (root / "vendor" / "favicon.ico").read_bytes()
        assert client.head("/static/v3/js/main.js").status_code == 200
    assert served > 100, served
    for path in STATIC_PROBES:
        with world.vtls() as sock:
            raw = raw_http(sock, (f"GET {path} HTTP/1.1\r\nHost: {VIEWER_HOST}:{world.https_port}\r\nCookie: {cookie}\r\n"
                                  "Connection: close\r\n\r\n").encode())
        assert status_of(raw) in (400, 404) and b"last-modified" not in raw.lower(), (path, raw[:300])

@needs_caddy
def test_v3_forged_forwarding_headers_do_not_buy_a_fresh_viewer_failure_budget(world):
    world.restart_daemon()                                # a fresh failure budget for this peer
    statuses = []
    with world.vclient() as client:
        for n in range(25):
            answer = world.vlogin(client, "not-a-key", **{"X-Forwarded-For": f"203.0.113.{n + 1}"})
            statuses.append(answer.status_code)
    assert statuses[:20] == [401] * 20 and statuses[20] == 429, statuses
    log = world.log_text()
    assert world.address in log and "203.0.113." not in log
    world.restart_daemon()                                # leave a clean budget for whatever runs next

# --- the shipped files against the real tools ---------------------------------------------------------------------

@needs_caddy
def test_the_shipped_caddyfile_validates_with_only_the_log_path_changed(tmp_path):
    shipped = CADDYFILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    rendered = shipped.replace("/var/log/caddy/taskmaster-access.log", str(tmp_path / "access.log"))
    rendered = rendered.replace("/var/log/caddy/taskmaster-viewer-access.log", str(tmp_path / "viewer-access.log"))
    assert rendered.count(str(tmp_path / "access.log")) == 1 and rendered.count(str(tmp_path / "viewer-access.log")) == 1
    assert rendered != shipped
    target = tmp_path / "Caddyfile"
    target.write_text(rendered, encoding="utf-8")
    env = {**os.environ, "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "data"),
           "XDG_CONFIG_HOME": str(tmp_path / "config")}
    done = subprocess.run([CADDY, "validate", "--config", str(target), "--adapter", "caddyfile"],
                          capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]



@needs_caddy
def test_the_rehearsals_caddyfile_validates(tmp_path):
    """028 gate I2: the file scripts/linux_rehearsal.sh installs (every site on Caddy's own CA) is one Caddy accepts."""
    from test_deploy_files import rehearsal_edited_caddyfile
    target = rehearsal_edited_caddyfile(tmp_path)
    text = target.read_text(encoding="utf-8")
    for name in ("taskmaster-access.log", "taskmaster-viewer-access.log"):
        assert text.count(f"/var/log/caddy/{name}") == 1, name
        text = text.replace(f"/var/log/caddy/{name}", str(tmp_path / name))
    target.write_text(text, encoding="utf-8")
    env = {**os.environ, "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "data"),
           "XDG_CONFIG_HOME": str(tmp_path / "config")}
    done = subprocess.run([CADDY, "validate", "--config", str(target), "--adapter", "caddyfile"],
                          capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]

needs_systemd = pytest.mark.skipif(sys.platform != "linux" or shutil.which("systemd-analyze") is None,
                                   reason="systemd-analyze is not available")


def render_unit(tmp_path: Path) -> Path:
    text = UNIT.read_text(encoding="utf-8").replace("\r\n", "\n")
    served = tmp_path / "project"
    served.mkdir(exist_ok=True)
    binary = tmp_path / "bin" / "taskmaster-serve"
    binary.parent.mkdir(exist_ok=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    text = text.replace("/opt/taskmaster/current/.venv/bin/taskmaster-serve", str(binary))
    text = text.replace("/srv/taskmaster/project", str(served))
    target = tmp_path / "taskmaster.service"
    target.write_text(text, encoding="utf-8")
    return target


@needs_systemd
def test_the_shipped_unit_passes_systemd_analyze_verify(tmp_path):
    done = subprocess.run(["systemd-analyze", "verify", str(render_unit(tmp_path))], capture_output=True, text=True, timeout=60)
    errors = [line for line in (done.stdout + done.stderr).splitlines()
              if line.strip() and "does not exist" not in line and "not found" not in line.lower()
              and "Failed to create" not in line]
    assert not errors, "\n".join(errors)


@needs_systemd
def test_the_shipped_unit_meets_the_exposure_threshold(tmp_path):
    done = subprocess.run(["systemd-analyze", "security", "--offline=yes", "--no-pager", str(render_unit(tmp_path))],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-1500:]
    match = re.search(r"Overall exposure level for \S+:\s*([0-9.]+)", done.stdout)
    assert match, done.stdout[-1500:]
    print("systemd-analyze security exposure:", match.group(1))
    assert float(match.group(1)) <= EXPOSURE_THRESHOLD, done.stdout
