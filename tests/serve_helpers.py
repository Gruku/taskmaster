# User intent: shared harness for serve tests: run an ASGI app on a real loopback socket, read the daemon's ready line, stop a child process tree.
import asyncio
from dataclasses import dataclass, field
import dataclasses
import json
import os
import queue
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import uvicorn


@contextmanager
def running_app(app, host="127.0.0.1", **uvicorn_options):
    sock = socket.create_server((host, 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, **{"lifespan": "on", "log_config": None, "access_log": False,
                                                   **uvicorn_options}))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.02)
    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(15)
        assert not thread.is_alive()
        sock.close()


def toy_server():
    from fastmcp import FastMCP
    from taskmaster.serve.auth import current_principal

    mcp = FastMCP("toy")

    @mcp.tool
    def whoami() -> str:
        principal = current_principal()
        return json.dumps(dataclasses.asdict(principal) if principal else None)

    return mcp


def call_tool(url, token, name, arguments=None, *, framed=False):
    """The first text block. Team-server-027 frames teammates' text, so by default the frame is removed (as the mods
    do) and tests keep reading the plain content; pass framed=True to see the frame."""
    from fastmcp import Client
    from taskmaster.untrusted import unframe

    async def go():
        async with Client(url, auth=token) as client:
            return (await client.call_tool(name, arguments or {})).content[0].text
    text = asyncio.run(go())
    return text if framed else unframe(text)


class Connection:
    """One long-lived MCP connection (one MCP session) on its own event-loop thread.
    `.call(name, args)` returns the first text block; `.close()` ends the session (the client sends DELETE)."""

    def __init__(self, url, token, client_name="claude-code", headers=None):
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport
        from mcp.types import Implementation
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="test-mcp-connection", daemon=True)
        self.thread.start()
        target = StreamableHttpTransport(url, headers=headers, auth=token) if headers else url
        self.client = Client(target, auth=None if headers else token,
                             client_info=Implementation(name=client_name, version="1"))
        self._run(self.client.__aenter__())

    def _run(self, coroutine, timeout=120):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout)

    def call(self, name, arguments=None, timeout=120, *, framed=False):
        from taskmaster.untrusted import unframe

        async def go():
            result = await self.client.call_tool(name, arguments or {}, raise_on_error=False)
            if result.content:
                return result.content[0].text
            return json.dumps(getattr(result, "structured_content", None))     # a tool that answered with no text block
        text = self._run(go(), timeout)
        return text if framed else unframe(text)

    def tools(self) -> list[dict]:
        """Every registered tool as {name, inputSchema}, as this connection's principal sees the list."""
        async def go():
            return [{"name": t.name, "inputSchema": t.inputSchema} for t in await self.client.list_tools()]
        return self._run(go())

    def call_all(self, name, arguments=None, timeout=120, *, framed=False):
        """Every text block of the result (team-server-010: the adoption notice is an extra block)."""
        from taskmaster.untrusted import unframe

        async def go():
            result = await self.client.call_tool(name, arguments or {}, raise_on_error=False)
            return [block.text for block in result.content if hasattr(block, "text")]
        blocks = self._run(go(), timeout)
        return blocks if framed else [unframe(block) for block in blocks]

    def close(self):
        if self.loop.is_running():
            try:
                self._run(self.client.__aexit__(None, None, None), 60)
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.thread.join(30)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def open_connection(url, token, client_name="claude-code", headers=None) -> Connection:
    return Connection(url, token, client_name, headers)


def list_tool_names(url, token):
    from fastmcp import Client

    async def go():
        async with Client(url, auth=token) as client:
            return [tool.name for tool in await client.list_tools()]
    return asyncio.run(go())


def raw_initialize(url, token):
    return httpx.post(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"}}})


def stop_tree(child):
    if child.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(child.pid)], capture_output=True, timeout=30)
    else:
        child.kill()
    try:
        child.wait(10)
    except subprocess.TimeoutExpired:
        child.kill()


def read_ready_line(child, timeout=90, stderr_path=None):
    """The daemon's first stdout line as JSON; fails with the stderr tail if it exits or times out first."""
    lines = queue.Queue()

    def pump():
        for line in iter(child.stdout.readline, b""):
            lines.put(line)
        lines.put(None)
    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        tail = ""
        if stderr_path is not None and Path(stderr_path).exists():
            tail = Path(stderr_path).read_text(errors="replace")[-3000:]
        assert left > 0, f"serve did not become ready in {timeout}s; stderr tail:\n{tail}"
        try:
            line = lines.get(timeout=min(left, 1))
        except queue.Empty:
            continue
        if line is None:
            code = child.wait(10)
            tail = Path(stderr_path).read_text(errors="replace")[-3000:] if stderr_path else ""
            raise AssertionError(f"serve exited before ready (code {code}); stderr tail:\n{tail}")
        try:
            return json.loads(line)
        except ValueError:
            continue


def raw_request(url, data: bytes, *, read_timeout=5.0):
    """Send raw bytes on a fresh socket and read until the server closes or read_timeout passes.
    Returns (bytes read, seconds from connect to close or timeout)."""
    host, port = url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)
    sock = socket.create_connection((host.strip("[]"), int(port)), timeout=read_timeout)
    started = time.monotonic()
    chunks = []
    try:
        if data:
            sock.sendall(data)
        while True:
            left = read_timeout - (time.monotonic() - started)
            if left <= 0:
                break
            sock.settimeout(left)
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            except (ConnectionResetError, ConnectionAbortedError):
                break
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    return b"".join(chunks), time.monotonic() - started


def connect(url, timeout=10.0):
    host, port = url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)
    return socket.create_connection((host.strip("[]"), int(port)), timeout=timeout)


CRLF = bytes([13, 10])
CRLF2 = CRLF * 2


def read_response(sock, timeout=5.0):
    """Read one HTTP/1.1 response (headers plus a Content-Length body) from sock.
    Returns (status, {lower-case header: value}, body)."""
    sock.settimeout(timeout)
    data = b""
    while CRLF2 not in data:
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError(f"closed before a full response: {data!r}")
        data += chunk
    head, _, body = data.partition(CRLF2)
    lines = head.decode("latin-1").split(CRLF.decode())
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    while len(body) < length:
        chunk = sock.recv(65536)
        if not chunk:
            break
        body += chunk
    return status, headers, body[:length]


def closed_within(sock, seconds) -> bool:
    """True if the peer closes (EOF or reset) within seconds; data read meanwhile is discarded."""
    deadline = time.monotonic() + seconds
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        sock.settimeout(left)
        try:
            if not sock.recv(65536):
                return True
        except socket.timeout:
            return False
        except (ConnectionResetError, ConnectionAbortedError, OSError):
            return True


class TwoKeyAuthenticator:
    """An Authenticator over a fixed (mutable) token -> Principal table, recording every call."""

    def __init__(self, keys):
        self.keys = dict(keys)
        self.calls = []

    def authenticate(self, token):
        self.calls.append(token)
        return self.keys.get(token)


@dataclass
class IssuedKey:
    """A test principal's key; the key and its secret stay out of the repr, so a failing assert never prints them
    (security review m-6)."""
    key: str = field(repr=False)
    id: str
    secret: str = field(repr=False)
