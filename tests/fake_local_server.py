# User intent: one fake local taskmaster server for every relay test, usable in-process (FakeServer) and as a standalone launcher target
# (python fake_local_server.py ...: claims the lease, publishes discovery and the launch result, behaves as a control file says), so the
# Python and Rust relays are driven by the same server and the same switches.
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster import localserve as ls  # noqa: E402

TOKEN = "tok-123"
BUILD = {"version": "7.5.0", "digest": "a" * 32}
LIVE_INIT = {"protocolVersion": "2025-06-18", "capabilities": {}}
LIVE_TOOLS = [{"name": "write"}]
BIG_BODY = 64 * 1024 * 1024 + 1


class FakeServer:
    """The section 0 contract, plus switches a test flips: delays, forced statuses, an oversized body, a GET stream status.
    A control file (standalone use) overlays the attributes below on every request."""

    def __init__(self, build=BUILD, sse=False, token=TOKEN, control=None, init_result=None, tools=None):
        self.build, self.sse, self.token, self.control = build, sse, token, control
        self.init_result, self.tools = init_result or LIVE_INIT, tools or LIVE_TOOLS
        self.writes = 0
        self.inits = 0
        self.sessions = set()
        self.seen_headers = []
        self.calls = []                    # (method, tool name or None) of every POST that reached dispatch
        self.drop_tools = set()            # tools/call names whose connection is cut after the call ran
        self.drop_once_methods = set()     # other methods cut once after receipt
        self.stream_events = queue.Queue()
        self.stream_connects = 0
        self.stream_times = []
        self.deleted = []
        self.bridges = 1
        self.sse_hold = 0.0
        self.draining_initializes = 0
        self.retire_answer = (202, {})
        self.retired = threading.Event()
        self.on_retire = None
        self.delays = {}                   # "tools/call:<name>" or a method -> seconds before answering, every time
        self.delay_once = {}               # the same keys, consumed on first use
        self.status_for_tool = {}          # tool name -> HTTP status (answered instead of running it)
        self.get_status = None             # an HTTP status for the GET stream instead of the stream
        self.instructions = None           # an "instructions" string added to the initialize result
        self.server_name = None
        self.chunked = False               # SSE bodies as HTTP/1.1 Transfer-Encoding: chunked, each event split across two chunks (as uvicorn streams)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def setup(self):
                super().setup()
                if owner.chunked:
                    self.protocol_version = "HTTP/1.1"

            def _stream_head(self, headers=()):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                for key, val in headers:
                    self.send_header(key, val)
                if owner.chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.flush()

            def _stream_write(self, data):
                if not owner.chunked:
                    self.wfile.write(data)
                    self.wfile.flush()
                    return
                cut = max(1, len(data) // 2)
                for part in (data[:cut], data[cut:]):
                    self.wfile.write(b"%x\r\n" % len(part) + part + b"\r\n")
                    self.wfile.flush()
                    time.sleep(0.02)           # the chunk boundary falls inside the event: the relay must reassemble it

            def _stream_end(self):
                if owner.chunked:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
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
                if self.headers.get("Authorization") != "Bearer " + owner.token:
                    self._json(401, {"error": "unauthorized"})
                    return False
                return True

            def _delay(self, key):
                seconds = owner.delay_once.pop(key, None)
                if seconds is None:
                    seconds = owner.delays.get(key)
                if seconds:
                    time.sleep(seconds)

            def do_GET(self):
                owner._sync()
                if not self._auth():
                    return
                if self.path == ls.PATH_STATUS:
                    return self._json(200, {"format": 1, "state": "serving", "build": owner.build, "plugin_dir": "x", "root": "x",
                                            "bridges": owner.bridges, "inflight": 0, "idle_seconds": 0})
                owner.stream_times.append(time.monotonic())
                if owner.get_status:
                    return self._json(owner.get_status, {"error": "stream refused"})
                sid = self.headers.get("Mcp-Session-Id")
                if sid not in owner.sessions:
                    return self._json(404, {"error": "Session not found"})
                owner.stream_connects += 1
                self._stream_head()
                while True:
                    event = owner.stream_events.get()
                    if event is None:
                        self._stream_end()
                        return
                    self._stream_write(b"data: " + json.dumps(event).encode() + b"\n\n")

            def do_DELETE(self):
                owner.deleted.append(self.headers.get("Mcp-Session-Id"))
                owner.sessions.discard(self.headers.get("Mcp-Session-Id"))
                self._json(200, {})

            def do_POST(self):
                owner._sync()
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
                name = (message.get("params") or {}).get("name") if method == "tools/call" else None
                owner.calls.append((method, name))
                if method == "initialize" and owner.draining_initializes > 0:
                    owner.draining_initializes -= 1
                    return self._json(503, {"error": "this local server is draining; it exits once its calls finish"},
                                      [("Retry-After", "1")])
                if method == "initialize":
                    owner.inits += 1
                    sid = f"sess-{owner.inits}"
                    owner.sessions.add(sid)
                    result = dict(owner.init_result)
                    if owner.instructions:
                        result["instructions"] = owner.instructions
                    if owner.server_name:
                        result["serverInfo"] = {"name": owner.server_name, "version": "1"}
                    return self._reply(mid, result, [("Mcp-Session-Id", sid)])
                if sid not in owner.sessions:
                    return self._json(404, {"error": "Session not found"})
                if mid is None:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._delay(f"tools/call:{name}" if name else method)
                if method == "tools/call":
                    if name == "write":
                        owner.writes += 1
                    if name in owner.drop_tools:
                        self.close_connection = True
                        return
                    if name in owner.status_for_tool:
                        return self._json(owner.status_for_tool[name], {"error": "proxy says no"})
                    if name == "big":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(BIG_BODY))
                        self.end_headers()
                        chunk = b" " * 1048576
                        sent = 0
                        try:
                            while sent < BIG_BODY:
                                piece = chunk[:min(len(chunk), BIG_BODY - sent)]
                                self.wfile.write(piece)
                                sent += len(piece)
                        except OSError:
                            pass
                        self.close_connection = True
                        return
                    if name == "progress":
                        events = [{"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}},
                                  {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "done"}]}}]
                        return self._sse(events)
                    if name == "internal":
                        owner.stream_events.put({"jsonrpc": "2.0", "id": "tm-bridge-secret", "result": {}})
                        owner.stream_events.put({"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "visible"}})
                    return self._reply(mid, {"content": [{"type": "text", "text": f"ok {name}"}]})
                if method in owner.drop_once_methods:
                    owner.drop_once_methods.discard(method)
                    self.close_connection = True
                    return
                return self._reply(mid, {"tools": owner.tools})

            def _reply(self, mid, result, headers=()):
                payload = {"jsonrpc": "2.0", "id": mid, "result": result}
                if owner.sse:
                    return self._sse([payload], headers)
                self._json(200, payload, headers)

            def _sse(self, events, headers=()):
                self._stream_head(headers)
                for event in events:
                    self._stream_write(b"event: message\r\ndata: " + json.dumps(event).encode() + b"\r\n\r\n")
                time.sleep(owner.sse_hold)
                self._stream_end()
                self.close_connection = True

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def _sync(self):
        """Overlay the control file's switches (standalone use): a JSON object of attribute names to values."""
        if not self.control:
            return
        try:
            data = json.loads(Path(self.control).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for key, value in data.items():
            if key in ("drop_tools", "drop_once_methods"):
                value = set(value)
            if key == "retire_answer":
                value = (value[0], value[1])
            if hasattr(self, key) and key not in ("control",):
                setattr(self, key, value)

    def record(self, token_file="unused"):
        return {"format": 1, "profile": "local", "state": "serving", "url": f"http://127.0.0.1:{self.port}/mcp", "pid": os.getpid(),
                "root": "x", "plugin_dir": "x", "token_file": token_file, "build": self.build, "started": "t", "nonce": "n"}

    def endpoint(self):
        import tm_bridge as tb
        return tb.Endpoint(self.record(), self.token)

    def stop(self):
        self.stream_events.put(None)
        self.httpd.shutdown()
        self.httpd.server_close()


def standalone(argv=None) -> int:
    """Launch target for TASKMASTER_BRIDGE_TEST_SERVER_ARGV: claim the lease, publish discovery and the result, serve until
    retired or killed. Control-file keys (read once at start): startup_delay, result_status, result_reason, no_start, build."""
    from taskmaster import server_fence
    from taskmaster.serve.auth import ensure_operator_token
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--control", default=None)
    args = parser.parse_args(argv)
    root = Path(args.root)
    control = {}
    if args.control:
        try:
            control = json.loads(Path(args.control).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            control = {}
    sdir = root / ".taskmaster" / "local" / ls.SERVE_DIR
    with open(sdir / "fake-server.pids", "a", encoding="ascii") as pids:      # every launch, so a test can stop them all
        pids.write(f"{os.getpid()}\n")
    time.sleep(float(control.get("startup_delay", 0)))
    if control.get("no_start"):
        return 1
    status = control.get("result_status", ls.STATUS_READY)
    if status != ls.STATUS_READY:
        ls.write_result(Path(args.result_file), status, control.get("result_reason", "fake refusal"), pid=os.getpid())
        return 0
    build = control.get("build") or BUILD
    try:
        ensure_operator_token(root)
    except Exception:
        pass
    token_file = sdir / "operator.token"
    token_file.write_text(TOKEN + "\n", encoding="utf-8")
    extra = {}
    if control.get("serve_shipped"):          # the live registration equals the shipped handshake: no list_changed is due
        shipped = json.loads((PLUGIN_ROOT / "tm_bridge_handshake.json").read_text(encoding="utf-8"))
        extra = {"init_result": shipped["initialize"], "tools": shipped["tools"]}
    fake = FakeServer(build=build, control=args.control, **extra)
    try:
        lease = server_fence.claim_host(root)
    except server_fence.HostLeaseHeld:
        ls.write_result(Path(args.result_file), ls.STATUS_LEASE_HELD, "another host holds the lease", pid=os.getpid())
        return 0
    record = fake.record(str(token_file))
    ls.write_discovery(root, state=ls.STATE_SERVING, url=record["url"], pid=os.getpid(), plugin_dir=PLUGIN_ROOT, token_file=token_file,
                       build=build, started="t", nonce="n")
    ls.write_result(Path(args.result_file), ls.STATUS_READY, "", pid=os.getpid(), url=record["url"])
    fake.on_retire = lambda: threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)), daemon=True).start()
    try:
        while True:
            time.sleep(1)
    finally:
        lease.release()


if __name__ == "__main__":
    sys.exit(standalone())
