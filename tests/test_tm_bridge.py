# User intent: prove the stdio bridge relays faithfully and safely: framing, session headers, the retry rule (never run a write twice),
# eager reconnect, the skew matrix, opt-outs, hidden double-spawn launch, the real stdio fallback, and a stdlib-only import set.
from __future__ import annotations

import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import tm_bridge as tb
from taskmaster import localserve as ls
from taskmaster import server_fence

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BUILD = {"version": "7.5.0", "digest": "a" * 32}
OLDER = {"version": "7.4.0", "digest": "b" * 32}
NEWER = {"version": "7.6.0", "digest": "c" * 32}
OTHER_CODE = {"version": "7.5.0", "digest": "d" * 32}
TOKEN = "tok-123"


# -- a fake local server: just enough of the section 0 contract ------------------------------------------------------

class FakeServer:
    def __init__(self, build=BUILD, sse=False):
        self.build, self.sse = build, sse
        self.writes = 0
        self.inits = 0
        self.sessions = set()
        self.seen_headers = []
        self.drop_tools = set()            # tools/call names whose connection is cut after the call ran
        self.drop_once_methods = set()     # other methods cut once after receipt
        self.stream_events = queue.Queue()
        self.stream_connects = 0
        self.deleted = []
        self.bridges = 1
        self.sse_hold = 0.0                # seconds an SSE response stays open after its last event
        self.draining_initializes = 0      # initializes answered 503 "draining" before dispatch (serve/local.py's refusal)
        self.retire_answer = (202, {})
        self.retired = threading.Event()
        self.on_retire = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def _json(self, status, value, headers=()):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for key, val in headers:
                    self.send_header(key, val)
                self.end_headers()
                self.wfile.write(data)

            def _auth(self):
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    self._json(401, {"error": "unauthorized"})
                    return False
                return True

            def do_GET(self):
                if not self._auth():
                    return
                if self.path == ls.PATH_STATUS:
                    return self._json(200, {"format": 1, "state": "serving", "build": owner.build, "plugin_dir": "x", "root": "x",
                                            "bridges": owner.bridges, "inflight": 0, "idle_seconds": 0})
                sid = self.headers.get("Mcp-Session-Id")
                if sid not in owner.sessions:
                    return self._json(404, {"error": "Session not found"})
                owner.stream_connects += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.flush()
                while True:
                    event = owner.stream_events.get()
                    if event is None:
                        return
                    self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                    self.wfile.flush()

            def do_DELETE(self):
                owner.deleted.append(self.headers.get("Mcp-Session-Id"))
                owner.sessions.discard(self.headers.get("Mcp-Session-Id"))
                self._json(200, {})

            def do_POST(self):
                if not self._auth():
                    return
                body = self._body()
                if self.path == ls.PATH_RETIRE:
                    status, answer = owner.retire_answer
                    if status == 202:
                        owner.retired.set()
                        if owner.on_retire:
                            owner.on_retire()
                    return self._json(status, answer)
                message = json.loads(body)
                owner.seen_headers.append(dict(self.headers))
                method, mid = message.get("method"), message.get("id")
                sid = self.headers.get("Mcp-Session-Id")
                if method == "initialize" and owner.draining_initializes > 0:
                    owner.draining_initializes -= 1
                    return self._json(503, {"error": "this local server is draining; it exits once its calls finish"},
                                      [("Retry-After", "1")])
                if method == "initialize":
                    owner.inits += 1
                    sid = f"sess-{owner.inits}"
                    owner.sessions.add(sid)
                    return self._reply(mid, {"protocolVersion": "2025-06-18", "capabilities": {}}, [("Mcp-Session-Id", sid)])
                if sid not in owner.sessions:
                    return self._json(404, {"error": "Session not found"})
                if mid is None:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if method == "tools/call":
                    name = message["params"]["name"]
                    if name == "write":
                        owner.writes += 1
                    if name in owner.drop_tools:
                        self.close_connection = True
                        return
                    if name == "progress":
                        events = [{"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}},
                                  {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "done"}]}}]
                        return self._sse(events)
                    return self._reply(mid, {"content": [{"type": "text", "text": f"ok {name}"}]})
                if method in owner.drop_once_methods:
                    owner.drop_once_methods.discard(method)
                    self.close_connection = True
                    return
                return self._reply(mid, {"tools": [{"name": "write"}]})

            def _reply(self, mid, result, headers=()):
                payload = {"jsonrpc": "2.0", "id": mid, "result": result}
                if owner.sse:
                    return self._sse([payload], headers)
                self._json(200, payload, headers)

            def _sse(self, events, headers=()):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                for key, val in headers:
                    self.send_header(key, val)
                self.end_headers()
                for event in events:
                    self.wfile.write(b"event: message\r\ndata: " + json.dumps(event).encode() + b"\r\n\r\n")
                    self.wfile.flush()
                time.sleep(owner.sse_hold)
                self.close_connection = True

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def record(self, token_file="unused"):
        return {"format": 1, "profile": "local", "state": "serving", "url": f"http://127.0.0.1:{self.port}/mcp", "pid": os.getpid(),
                "root": "x", "plugin_dir": "x", "token_file": token_file, "build": self.build, "started": "t", "nonce": "n"}

    def endpoint(self):
        return tb.Endpoint(self.record(), TOKEN)

    def stop(self):
        self.stream_events.put(None)
        self.httpd.shutdown()
        self.httpd.server_close()


class Out:
    def __init__(self):
        self.lines, self.lock = [], threading.Lock()

    def write(self, data):
        with self.lock:
            self.lines.extend(json.loads(part) for part in data.decode().splitlines() if part.strip())

    def flush(self):
        pass

    def wait_for(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                for item in self.lines:
                    if predicate(item):
                        return item
            time.sleep(0.02)
        raise AssertionError(f"no matching message in {self.lines}")


def msg(method, mid=None, **params):
    out = {"jsonrpc": "2.0", "method": method}
    if mid is not None:
        out["id"] = mid
    if params:
        out["params"] = params
    return out


def call(name, mid):
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": name, "arguments": {}}}


@pytest.fixture
def server():
    made = []

    def make(**kw):
        made.append(FakeServer(**kw))
        return made[-1]
    yield make
    for item in made:
        item.stop()


def make_bridge(endpoint_source, tmp_path, build=BUILD, **kw):
    out = Out()
    bridge = tb.Bridge(tmp_path, build, "disc-1", stdout=out, ensure=endpoint_source, cwd="C:\\My Projects\\x y",
                       log=tb.Log(stream=io.StringIO()), budget=kw.pop("budget", 10), **kw)
    bridge.start()
    return bridge, out


def handshake(bridge):
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    bridge.handle(msg("notifications/initialized"))


# -- framing, headers, the stream ------------------------------------------------------------------------------------

@pytest.mark.parametrize("sse", [False, True])
def test_relay_json_and_sse_responses_and_202(server, tmp_path, sse):
    fake = server(sse=sse)
    bridge, out = make_bridge(fake.endpoint, tmp_path)
    handshake(bridge)
    bridge.handle(msg("tools/list", 2))
    bridge.handle(call("progress", 3))
    bridge.handle(call("read", 4))
    assert out.wait_for(lambda m: m.get("id") == 1)["result"]["protocolVersion"] == "2025-06-18"
    assert out.wait_for(lambda m: m.get("id") == 2)["result"]["tools"]
    assert out.wait_for(lambda m: m.get("method") == "notifications/progress")
    assert out.wait_for(lambda m: m.get("id") == 3)["result"]["content"][0]["text"] == "done"
    assert out.wait_for(lambda m: m.get("id") == 4)["result"]["content"][0]["text"] == "ok read"
    assert not [m for m in out.lines if m.get("method") == "notifications/initialized"]      # a 202 emits nothing
    bridge.closed.set()


def test_requests_carry_session_headers_token_and_protocol(server, tmp_path):
    fake = server()
    bridge, out = make_bridge(fake.endpoint, tmp_path)
    handshake(bridge)
    bridge.handle(call("read", 2))
    headers = fake.seen_headers[-1]
    assert headers["Mcp-Session-Id"] == "sess-1"
    assert headers[ls.HEADER_SESSION] == "disc-1" and headers[ls.HEADER_SESSION_NONCE] == bridge.nonce
    assert ls.decode_cwd(headers[ls.HEADER_CWD]) == "C:\\My Projects\\x y"
    assert headers["MCP-Protocol-Version"] == "2025-06-18"
    assert "text/event-stream" in headers["Accept"]
    bridge.closed.set()


def test_get_stream_held_open_forwarded_and_closed_with_delete(server, tmp_path):
    fake = server()
    bridge, out = make_bridge(fake.endpoint, tmp_path)
    handshake(bridge)
    deadline = time.monotonic() + 5
    while fake.stream_connects == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert fake.stream_connects == 1
    fake.stream_events.put({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    out.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed")
    bridge.close()
    assert fake.deleted == ["sess-1"]


# -- the retry rule and eager reconnect ------------------------------------------------------------------------------

def test_unknown_session_404_is_reinitialized_and_the_write_runs_once(server, tmp_path):
    fake = server()
    bridge, out = make_bridge(fake.endpoint, tmp_path)
    handshake(bridge)
    fake.sessions.clear()                                  # the server restarted: our session id is unknown
    bridge.handle(call("write", 5))
    assert out.wait_for(lambda m: m.get("id") == 5)["result"]["content"][0]["text"] == "ok write"
    assert fake.writes == 1 and fake.inits == 2
    assert not [m for m in out.lines if m.get("method") == "notifications/tools/list_changed"]
    bridge.closed.set()


def test_connection_refused_before_send_is_retried_on_the_new_server_and_list_changed_follows_a_build_change(server, tmp_path):
    first, second = server(), server(build=NEWER)
    current = {"fake": first}
    bridge, out = make_bridge(lambda: current["fake"].endpoint(), tmp_path)
    handshake(bridge)
    first.stop()
    current["fake"] = second
    bridge.handle(call("write", 6))
    assert out.wait_for(lambda m: m.get("id") == 6)["result"]["content"][0]["text"] == "ok write"
    assert (first.writes, second.writes, second.inits) == (0, 1, 1)
    assert out.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed")
    bridge.closed.set()


def test_an_initialize_refused_by_a_draining_server_reconnects_instead_of_failing(server, tmp_path):
    fake = server()
    fake.draining_initializes = 1
    bridge, out = make_bridge(fake.endpoint, tmp_path)
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    answer = out.wait_for(lambda m: m.get("id") == 1)
    assert "result" in answer and "error" not in answer, answer
    assert fake.inits == 1 and fake.draining_initializes == 0
    bridge.closed.set()


def test_the_launcher_keeps_the_servers_stderr_out_of_serve_log(project, tmp_path):
    """serve.log belongs to the server's own rotating handler; a second writer duplicated its lines (002 M1)."""
    serve_dir = project / ".taskmaster" / "local" / "serve"
    serve_dir.mkdir(parents=True, exist_ok=True)
    marker = "printed-before-logging-is-up"
    code = f"import sys; sys.stderr.write('{marker}')"
    assert tb.launcher_main([sys.executable, "-c", code, "--root", str(project)]) == 0
    start_log = serve_dir / "serve-start.log"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not (start_log.exists() and marker in start_log.read_text(errors="replace")):
        time.sleep(0.1)
    assert marker in start_log.read_text(errors="replace")
    assert not (serve_dir / "serve.log").exists()


def test_unreachable_server_fails_the_call_as_not_sent(server, tmp_path):
    fake = server()
    bridge, out = make_bridge(fake.endpoint, tmp_path, budget=0.5)
    handshake(bridge)
    fake.stop()
    bridge.handle(call("write", 7))
    text = out.wait_for(lambda m: m.get("id") == 7)["error"]["message"]
    assert "was not sent" in text and TRANSIENT.search(text)
    bridge.closed.set()


def _transient_matcher():
    source = (PLUGIN_ROOT / "mods" / "taskmaster-mods" / "hooks" / "tm.ts").read_text(encoding="utf-8")
    return re.compile(re.search(r"const NOT_CONNECTED = /(.+)/\n", source).group(1))


TRANSIENT = _transient_matcher()


def test_the_mods_bridge_wording_fixture_matches_the_bridges_own_text():
    # mods/taskmaster-mods/tests/fixtures/bridge-text.ts holds the two phrasings the mod tests use; one source of truth is the bridge.
    source = (PLUGIN_ROOT / "mods" / "taskmaster-mods" / "tests" / "fixtures" / "bridge-text.ts").read_text(encoding="utf-8")
    templates = dict(re.findall(r"export const (\w+) = \(tool: string\): string => `(.+)`", source))
    seen = {name: text.replace("${tool}", "backlog_x") for name, text in templates.items()}
    assert seen["bridgeNotSent"] == tb.may_have_committed_text("backlog_x", sent_known=True)
    assert seen["bridgeMayHaveCommitted"] == tb.may_have_committed_text("backlog_x")
    for text in seen.values():
        assert TRANSIENT.search(text)


def test_may_have_committed_after_a_cut_response_is_not_retried_and_matches_the_mods_matcher(server, tmp_path):
    fake = server()
    fake.drop_tools = {"write"}                           # the write runs, then the connection is cut
    bridge, out = make_bridge(fake.endpoint, tmp_path)
    handshake(bridge)
    bridge.handle(call("write", 8))
    error = out.wait_for(lambda m: m.get("id") == 8)["error"]["message"]
    assert "may have committed" in error and TRANSIENT.search(error.split("\n")[0])
    time.sleep(0.5)                                        # a background reconnect must not replay the call
    assert fake.writes == 1
    bridge.closed.set()


def test_non_tool_request_cut_after_send_is_retried_after_reconnect(server, tmp_path):
    fake = server()
    bridge, out = make_bridge(fake.endpoint, tmp_path)
    handshake(bridge)
    fake.drop_once_methods = {"tools/list"}
    bridge.handle(msg("tools/list", 9))
    assert out.wait_for(lambda m: m.get("id") == 9)["result"]["tools"]
    assert fake.inits == 2
    bridge.closed.set()


def test_server_http_error_is_returned_not_retried(server, tmp_path):
    fake = server()
    bridge, out = make_bridge(fake.endpoint, tmp_path)
    handshake(bridge)
    bridge.endpoint.token = "wrong"
    bridge.handle(call("write", 10))
    assert "HTTP 401" in out.wait_for(lambda m: m.get("id") == 10)["error"]["message"]
    assert fake.writes == 0
    bridge.closed.set()


# -- discovery, skew matrix, retire, launch outcomes -----------------------------------------------------------------

@pytest.mark.parametrize("server_build,expected", [
    (BUILD, "use"), (NEWER, "use"), (OLDER, "retire"), (OTHER_CODE, "retire_if_alone"),
    ({"version": "weird", "digest": "e" * 32}, "retire_if_alone"), (None, "retire_if_alone")])
def test_skew_matrix(server_build, expected):
    assert tb.skew_action(server_build, BUILD) == expected


@pytest.fixture
def project(tmp_path):
    from taskmaster.serve.auth import ensure_operator_token
    server_fence.reset_for_tests()
    root = tmp_path / "proj"
    (root / ".taskmaster" / "local").mkdir(parents=True)
    ensure_operator_token(root)
    yield root
    server_fence.reset_for_tests()


class Host:
    """Plays the server's lease + discovery for a fake HTTP server (what serve --local does)."""

    def __init__(self, root, fake):
        self.root, self.fake, self.lease = root, fake, None
        self.token_file = root / ".taskmaster" / "local" / "serve" / "operator.token"

    def up(self, state=ls.STATE_SERVING):
        self.lease = server_fence.claim_host(self.root)
        record = self.fake.record(str(self.token_file))
        ls.write_discovery(self.root, state=state, url=record["url"], pid=os.getpid(), plugin_dir=self.root,
                           token_file=self.token_file, build=self.fake.build, started="t", nonce="n")

    def down(self):
        if self.lease:
            self.lease.release()
            self.lease = None
        ls.remove_discovery(self.root)


def token_matches(project):
    # the fake server checks TOKEN; the real file holds a random one: make the file hold TOKEN
    path = project / ".taskmaster" / "local" / "serve" / "operator.token"
    path.write_text(TOKEN + "\n", encoding="utf-8")


def test_ensure_uses_a_server_of_the_same_build(server, project):
    token_matches(project)
    fake = server()
    host = Host(project, fake)
    host.up()
    launched = []
    endpoint = tb.ensure_server(project, BUILD, "disc", launch=lambda *a, **k: launched.append(1))
    assert endpoint.port == fake.port and not launched and not fake.retired.is_set()


def test_ensure_retires_an_older_server_then_launches(server, project):
    token_matches(project)
    old, new = server(build=OLDER), server(build=BUILD)
    host = Host(project, old)
    host.up()
    old.on_retire = host.down

    def launch(root, plugin_dir, log=None):
        host.fake = new
        host.up()
        return {"status": "ready", "reason": "", "pid": 1}
    endpoint = tb.ensure_server(project, BUILD, "disc", launch=launch)
    assert old.retired.is_set() and endpoint.port == new.port


def test_ensure_same_version_other_code_with_other_bridges_uses_it_and_says_so_once(server, project):
    token_matches(project)
    fake = server(build=OTHER_CODE)
    fake.retire_answer = (409, {"reason": "other bridges connected"})
    Host(project, fake).up()
    stream = io.StringIO()
    log = tb.Log(stream=stream)
    for _ in range(2):
        endpoint = tb.ensure_server(project, BUILD, "disc", log=log)
        assert endpoint.port == fake.port
    assert stream.getvalue().count("could not be replaced now") == 1


def test_ensure_waits_out_a_draining_server_then_launches(server, project):
    token_matches(project)
    fake = server()
    host = Host(project, fake)
    host.up(state=ls.STATE_DRAINING)
    threading.Timer(0.5, host.down).start()
    started = time.monotonic()

    def launch(root, plugin_dir, log=None):
        assert time.monotonic() - started >= 0.4
        host.up()
        return {"status": "ready", "reason": ""}
    assert tb.ensure_server(project, BUILD, "disc", launch=launch).port == fake.port


def test_ensure_stale_discovery_launches(server, project):
    token_matches(project)
    fake = server()
    host = Host(project, fake)
    host.up()
    host.lease.release()                                    # the lease is free: the record is stale
    host.lease = None
    seen = []

    def launch(root, plugin_dir, log=None):
        seen.append(1)
        host.up()
        return {"status": "ready", "reason": ""}
    assert tb.ensure_server(project, BUILD, "disc", launch=launch).port == fake.port and seen


def test_ensure_lease_held_is_bounded_to_three_tries(project):
    calls = []

    def launch(root, plugin_dir, log=None):
        calls.append(1)
        return {"status": "lease_held", "reason": "x"}
    with pytest.raises(tb.ServerUnavailable):
        tb.ensure_server(project, BUILD, "disc", launch=launch)
    assert len(calls) == ls.__dict__.get("LEASE_HELD_TRIES", tb.LEASE_HELD_TRIES) == 3


@pytest.mark.parametrize("status", ["store_owned_busy", "refused", "error"])
def test_ensure_other_outcomes_fall_back_with_the_reason(project, status):
    with pytest.raises(tb.ServerUnavailable) as info:
        tb.ensure_server(project, BUILD, "disc", launch=lambda *a, **k: {"status": status, "reason": "because"})
    assert status in str(info.value) and "because" in str(info.value)


def test_launch_result_timeout_is_an_error(project):
    result = tb.launch_server(project, PLUGIN_ROOT, server_argv=[sys.executable, "-c", "pass"], wait=1.0)
    assert result["status"] == "error" and "no launch result" in result["reason"]


# -- the hidden double spawn -----------------------------------------------------------------------------------------

FAKE_SERVE = '''
import json, os, sys, time
result, info, stop = sys.argv[1], sys.argv[2], sys.argv[3]
open(info, "w").write(json.dumps({"pid": os.getpid(), "ppid": os.getppid(), "env": dict(os.environ)}))
open(result, "w").write(json.dumps({"status": "ready", "reason": "", "pid": os.getpid()}))
deadline = time.time() + 90
while time.time() < deadline and not os.path.exists(stop):
    time.sleep(0.1)
'''


def test_double_spawn_scrubs_claude_env_and_the_server_outlives_its_launcher(project, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
    monkeypatch.setenv("CLAUDECODE", "1")
    script, info, stop = tmp_path / "fake_serve.py", tmp_path / "info.json", tmp_path / "stop"
    script.write_text(FAKE_SERVE, encoding="utf-8")
    result = tb.launch_server(project, PLUGIN_ROOT, wait=30,
                              server_argv=[getattr(sys, "_base_executable", sys.executable), str(script), "{result_file}", str(info), str(stop)])
    try:
        assert result["status"] == "ready"
        data = json.loads(info.read_text(encoding="utf-8"))
        assert not [k for k in data["env"] if k.upper().startswith("CLAUDE")]
        assert data["env"]["LEFTOVERS_KEEP"] == "1" and data["env"]["LEFTOVERS_NAME"] == "tm-local:proj"
        assert data["ppid"] not in (os.getpid(),)                     # not a child of this (the bridge's) process
        if os.name == "nt":                                           # the launcher (its parent) has already exited
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, data["ppid"])
            alive = False
            if handle:
                code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                ctypes.windll.kernel32.CloseHandle(handle)
                alive = code.value == 259
            assert not alive
        assert not list((project / ".taskmaster" / "local" / "serve").glob("launch-*.json"))   # result file removed
    finally:
        stop.write_text("x")
        deadline = time.monotonic() + 10
        while info.exists() and time.monotonic() < deadline and _pid_alive(json.loads(info.read_text())["pid"]):
            time.sleep(0.1)


def _pid_alive(pid):
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_launch_flags_hide_the_window_and_detach():
    if os.name == "nt":
        flags = tb.hidden_flags(True)["creationflags"]
        assert flags & subprocess.CREATE_NO_WINDOW and flags & subprocess.CREATE_NEW_PROCESS_GROUP and flags & 0x01000000
        assert not tb.hidden_flags(False)["creationflags"] & 0x01000000
    else:
        assert tb.hidden_flags()["start_new_session"] is True


def test_launch_breakaway_refused_retries_without_it(monkeypatch):
    calls = []
    real = subprocess.Popen

    def fake_popen(argv, **kw):
        calls.append(kw.get("creationflags", 0))
        if os.name == "nt" and kw.get("creationflags", 0) & 0x01000000:
            raise PermissionError("job does not allow breakaway")
        return real([sys.executable, "-c", "pass"], **{k: v for k, v in kw.items()})
    monkeypatch.setattr(tb.subprocess, "Popen", fake_popen)
    if os.name == "nt":
        tb.spawn_detached(["x"], env=dict(os.environ), cwd=str(PLUGIN_ROOT)).wait()
        assert len(calls) == 2 and not calls[1] & 0x01000000


def test_server_python_is_cached_and_re_resolved_when_pruned(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKMASTER_BRIDGE_CACHE", str(tmp_path / "cache.json"))
    fake_py = tmp_path / "py.exe"
    fake_py.write_text("x")
    runs = []

    def fake_run(argv, **kw):
        runs.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=str(fake_py) + "\n", stderr="")
    monkeypatch.setattr(tb.subprocess, "run", fake_run)
    monkeypatch.setattr(tb.shutil, "which", lambda name: "uv")
    assert tb.server_python(PLUGIN_ROOT) == str(fake_py)
    assert tb.server_python(PLUGIN_ROOT) == str(fake_py) and len(runs) == 1      # cache hit
    fake_py.unlink()                                                             # the uv env was pruned
    fake_py.write_text("y")
    runs.clear()
    fake_py.unlink()
    assert tb.server_python(PLUGIN_ROOT) is None and len(runs) == 1              # re-resolved, uv names a missing file


# -- opt-outs --------------------------------------------------------------------------------------------------------

def test_opt_outs(project, monkeypatch):
    monkeypatch.delenv("TASKMASTER_LOCAL_SERVER", raising=False)
    assert tb.fallback_reason(project)                                    # no native store here: legacy / missing
    monkeypatch.setenv("TASKMASTER_LOCAL_SERVER", "0")
    assert tb.fallback_reason(project) == "TASKMASTER_LOCAL_SERVER=0"
    monkeypatch.delenv("TASKMASTER_LOCAL_SERVER")
    (project / ".taskmaster" / "local" / "settings.json").write_text('{"local_server": "off"}', encoding="utf-8")
    assert "local_server: off" in tb.fallback_reason(project)
    empty = project.parent / "nobacklog"
    empty.mkdir()
    assert tb.fallback_reason(empty) == "no backlog at the project root"


def test_marker_and_legacy_store_fall_back(project, monkeypatch):
    monkeypatch.delenv("TASKMASTER_LOCAL_SERVER", raising=False)
    monkeypatch.setattr(ls, "legacy_store", lambda root: False)
    assert tb.fallback_reason(project) is None
    monkeypatch.setattr(ls, "server_mode_reason", lambda root: "project is in server mode (https://x)")
    assert "server mode" in tb.fallback_reason(project)
    monkeypatch.setattr(ls, "legacy_store", lambda root: True)
    assert "not a ready native store" in tb.fallback_reason(project)


# -- the real fallback relay and the import set ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def native_root(tmp_path_factory):
    from native_twins import activate_native, point_server_at, scaffold
    from taskmaster import backlog_server as bs
    from taskmaster import store
    root = scaffold(tmp_path_factory.mktemp("fallback") / "native")
    with pytest.MonkeyPatch.context() as patch:
        point_server_at(patch, root)
        bs.backlog_add_epic(epic_id="e", name="E", done_when="done")
        store.reset_for_tests()
        activate_native(root)
        store.reset_for_tests()
    return root


def _rpc(proc, message):
    proc.stdin.write((json.dumps(message) + "\n").encode())
    proc.stdin.flush()


def _read_until(proc, mid, timeout=180):
    if not hasattr(proc, "box"):
        proc.box = queue.Queue()

        def reader():
            for line in proc.stdout:
                proc.box.put(json.loads(line))
        threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = proc.box.get(timeout=max(0.1, deadline - time.monotonic()))
        if item.get("id") == mid:
            return item
    raise AssertionError("no answer")


@pytest.mark.skipif(shutil.which("uv") is None, reason="the fallback runs uv run backlog_server.py")
def test_real_fallback_relay_runs_the_stdio_server_and_answers_a_tool_call(native_root):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(TASKMASTER_LOCAL_SERVER="0", TASKMASTER_ROOT=str(native_root))
    proc = subprocess.Popen([sys.executable, str(PLUGIN_ROOT / "tm_bridge.py")], cwd=str(native_root), env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        _rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
        assert "serverInfo" in _read_until(proc, 1)["result"]
        _rpc(proc, msg("notifications/initialized"))
        _rpc(proc, call("backlog_store_status", 2))
        answer = _read_until(proc, 2)
        assert "result" in answer and not answer["result"].get("isError")
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    assert "TASKMASTER_LOCAL_SERVER=0" in proc.stderr.read().decode()


def _floor_python():
    found = subprocess.run(["uv", "python", "find", "3.11"], capture_output=True, text=True) if shutil.which("uv") else None
    return found.stdout.strip() if found and found.returncode == 0 and found.stdout.strip() else None


IMPORT_PIN = """
import sys, tempfile, pathlib
sys.path.insert(0, %r)
import tm_bridge
tm_bridge._ls()
tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / ".taskmaster" / "local").mkdir(parents=True)
(tmp / ".taskmaster" / "local" / "store.db").write_bytes(b"garbage")
reason = tm_bridge.fallback_reason(tmp)
from taskmaster.root import resolve_root
resolve_root(tmp)
bad = [m for m in ("fastmcp", "pydantic", "starlette", "uvicorn", "yaml", "anyio", "httpx", "mcp") if m in sys.modules]
print(sys.version_info[:2], reason, bad)
sys.exit(1 if bad else 0)
""" % str(PLUGIN_ROOT)


@pytest.mark.parametrize("floor", [False, True])
def test_bridge_import_set_has_no_third_party_module_even_on_the_python_floor(floor):
    python = _floor_python() if floor else sys.executable
    if python is None:
        pytest.skip("no Python 3.11 available")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
    done = subprocess.run([python, "-I", "-c", IMPORT_PIN], capture_output=True, text=True, timeout=120, env=env)
    assert done.returncode == 0, done.stdout + done.stderr


def test_mcp_json_launches_the_bridge_through_uv_without_a_project():
    entry = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["tm"]
    assert entry["command"] == "uv" and entry["args"][-1] == "${CLAUDE_PLUGIN_ROOT}/tm_bridge.py"
    assert "--no-project" in entry["args"] and ">=3.11" in entry["args"]


def test_project_config_can_turn_local_mode_off(project, monkeypatch):
    monkeypatch.delenv("TASKMASTER_LOCAL_SERVER", raising=False)
    (project / ".taskmaster" / "taskmaster.json").write_text('{"local_server": "off"}', encoding="utf-8")
    assert "local_server: off" in tb.fallback_reason(project)


FAKE_STDIO = '''
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if m.get("method") == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {}}}), flush=True)
    elif m.get("method") == "tools/call":
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"content": [{"type": "text", "text": "stdio " + m["params"]["name"]}]}}), flush=True)
'''


def test_mid_session_loss_switches_to_the_stdio_child_after_three_failed_relaunches(server, tmp_path):
    fake = server()
    state = {"down": False}

    def ensure():
        if state["down"]:
            raise tb.ServerUnavailable("launch error: boom")
        return fake.endpoint()
    script = tmp_path / "fake_stdio.py"
    script.write_text(FAKE_STDIO, encoding="utf-8")
    out = Out()
    bridge = tb.Bridge(tmp_path, BUILD, "disc-1", stdout=out, ensure=ensure, log=tb.Log(stream=io.StringIO()), budget=30,
                       fallback_command=[sys.executable, str(script)])
    bridge.start()
    handshake(bridge)
    state["down"] = True
    fake.stop()
    bridge.handle(call("read", 20))                    # three failed relaunches -> switch, then served by the child
    assert out.wait_for(lambda m: m.get("id") == 20)["result"]["content"][0]["text"] == "stdio read"
    assert out.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed")
    bridge.handle(call("read", 21))
    assert out.wait_for(lambda m: m.get("id") == 21)["result"]["content"][0]["text"] == "stdio read"
    assert not [m for m in out.lines if m.get("id") == "tm-bridge-fb-init"]
    bridge.close()


def test_bridge_process_end_to_end_against_a_fake_server_then_exits_on_stdin_eof(native_root, server):
    from taskmaster.coordinator.protocol import build_identity
    from taskmaster.serve.auth import ensure_operator_token
    token_file = native_root / ".taskmaster" / "local" / "serve" / "operator.token"
    server_fence.reset_for_tests()
    try:
        ensure_operator_token(native_root)
    except Exception:
        pass
    token_file.write_text(TOKEN + "\n", encoding="utf-8")
    fake = server(build=build_identity())
    lease = server_fence.claim_host(native_root)
    try:
        record = fake.record(str(token_file))
        ls.write_discovery(native_root, state=ls.STATE_SERVING, url=record["url"], pid=os.getpid(), plugin_dir=PLUGIN_ROOT,
                           token_file=token_file, build=fake.build, started="t", nonce="n")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "TASKMASTER_LOCAL_SERVER")}
        env.update(CLAUDE_CODE_SESSION_ID="sess-e2e", TASKMASTER_ROOT=str(native_root))
        proc = subprocess.Popen([sys.executable, str(PLUGIN_ROOT / "tm_bridge.py")], cwd=str(native_root), env=env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _rpc(proc, msg("initialize", 1, protocolVersion="2025-06-18"))
        assert _read_until(proc, 1, timeout=60)["result"]["protocolVersion"] == "2025-06-18"
        _rpc(proc, msg("notifications/initialized"))
        _rpc(proc, call("read", 2))
        assert _read_until(proc, 2, timeout=60)["result"]["content"][0]["text"] == "ok read"
        assert fake.seen_headers[-1][ls.HEADER_SESSION] == "sess-e2e"
        proc.stdin.close()
        assert proc.wait(timeout=30) == 0
        assert fake.deleted == ["sess-1"] and not fake.retired.is_set()
    finally:
        lease.release()
        ls.remove_discovery(native_root)
