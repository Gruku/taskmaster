# User intent: pin the shared vectors file (constants, cwd encoding, SSE, chunked bodies) to the Python bridge so the Rust relay's cargo tests
# read the same truth the Python bridge obeys; either side drifting fails here.
import io
import json
import socket
import threading
import http.client
from pathlib import Path

import pytest

import tm_bridge
from taskmaster import localserve

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "relay_vectors.json").read_text(encoding="utf-8"))


def test_format_and_init_wait():
    assert VECTORS["format"] == 1
    assert VECTORS["init_wait"] == 2.0


@pytest.mark.parametrize("name", sorted(VECTORS["constants"]))
def test_constants_equal_the_bridge(name):
    actual = getattr(tm_bridge, name)
    assert VECTORS["constants"][name] == (list(actual) if isinstance(actual, tuple) else actual)


@pytest.mark.parametrize("path,encoded", VECTORS["cwd"])
def test_cwd_encoding(path, encoded):
    assert localserve.encode_cwd(path) == encoded


class _Resp:
    def __init__(self, data):
        self.f = io.BytesIO(data)

    def getheader(self, name):
        return "text/event-stream" if name == "Content-Type" else None

    def readline(self, n=-1):
        return self.f.readline(n)


@pytest.mark.parametrize("case", VECTORS["sse"], ids=lambda c: repr(c["chunks"])[:40])
def test_sse_events(case):
    got = list(tm_bridge.Bridge.events(_Resp("".join(case["chunks"]).encode())))
    assert got == case["events"]


@pytest.mark.parametrize("case", VECTORS["chunked"], ids=lambda c: repr(c["raw"].split("\r\n\r\n", 1)[1])[:40])
def test_chunked_against_http_client(case):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.recv(4096)
        conn.sendall(case["raw"].encode("latin-1"))
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    conn = http.client.HTTPConnection("127.0.0.1", srv.getsockname()[1], timeout=5)
    try:
        conn.request("GET", "/")
        if "body" in case:
            assert conn.getresponse().read().decode() == case["body"]
        else:
            with pytest.raises(http.client.HTTPException) as exc:
                conn.getresponse().read()
            assert type(exc.value).__name__ == case["error"]
    finally:
        conn.close()
        srv.close()
