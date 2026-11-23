# User intent: a team clone's plugin tm is the same stdio bridge pointed at the team server with the member's key: a write
# lands on the team server and never in a local store, a bad key or an unreachable server says so in words the mods map, and
# nothing here ever falls back to the local stdio store (split brain) or skips TLS verification.
from __future__ import annotations

import io
import json
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import tm_bridge as tb
from taskmaster.remote import keys, locate
from test_tm_bridge import TOKEN, TRANSIENT, FakeServer, Out, call, handshake, msg, server  # noqa: F401  (the fixture)

KEY = SimpleNamespace(value=TOKEN)


def team_bridge(tmp_path, port, load_key=lambda ref: KEY, scheme="http", budget=2):
    ref = locate.server_ref(f"{scheme}://127.0.0.1:{port}/mcp")
    out = Out()
    bridge = tb.TeamBridge(tmp_path, ref, load_key, stdout=out, cwd=str(tmp_path), log=tb.Log(stream=io.StringIO()), budget=budget)
    bridge.start()
    return bridge, out


def test_a_write_lands_on_the_team_server_with_the_member_key_and_never_in_a_local_store(server, tmp_path):
    fake = server()
    bridge, out = team_bridge(tmp_path, fake.port)
    handshake(bridge)
    bridge.handle(call("write", 5))
    assert out.wait_for(lambda m: m.get("id") == 5)["result"]["content"][0]["text"] == "ok write"
    assert fake.writes == 1
    assert not (tmp_path / ".taskmaster").exists()
    headers = {k.lower(): v for k, v in fake.seen_headers[-1].items()}
    assert headers["authorization"] == "Bearer " + TOKEN
    assert not [k for k in headers if k.startswith("x-taskmaster")]
    assert bridge.fallback is None
    bridge.closed.set()


class StatusServer:
    def __init__(self, status, body="no"):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_an_expired_or_refused_key_names_the_fix_and_never_prints_the_key(server, tmp_path):
    fake = server()                                         # accepts only TOKEN
    secret = "tm_u_0123456789abcdef_" + "A" * 43
    bridge, out = team_bridge(tmp_path, fake.port, load_key=lambda ref: SimpleNamespace(value=secret))
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    text = out.wait_for(lambda m: m.get("id") == 1)["error"]["message"]
    assert "taskmaster remote set-key --url http://127.0.0.1:%d/mcp" % fake.port in text
    assert secret not in text and secret not in json.dumps(out.lines)
    bridge.closed.set()


def test_403_says_the_role_or_scopes_denied_it(tmp_path):
    fake = StatusServer(403, "scope missing: tasks:write")
    try:
        bridge, out = team_bridge(tmp_path, fake.port)
        bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
        text = out.wait_for(lambda m: m.get("id") == 1)["error"]["message"]
        assert "denied this call for your role or scopes" in text and "scope missing" in text
        bridge.closed.set()
    finally:
        fake.stop()


def test_an_unreachable_team_server_is_the_transient_wording_the_mods_map_and_never_falls_back(server, tmp_path):
    fake = server()
    bridge, out = team_bridge(tmp_path, fake.port, budget=0.5)
    handshake(bridge)
    fake.stop()
    bridge.handle(call("write", 7))
    text = out.wait_for(lambda m: m.get("id") == 7)["error"]["message"]
    assert "team taskmaster server could not be reached; this call was not sent" in text and TRANSIENT.search(text)
    assert bridge.fallback is None and bridge._count_failure() is False
    with pytest.raises(tb.ServerUnavailable):
        bridge._switch_to_fallback("x")
    assert not (tmp_path / ".taskmaster").exists()
    bridge.closed.set()


def test_no_key_answers_every_tool_call_with_set_key_and_sends_nothing(server, tmp_path):
    fake = server()
    env = {"TASKMASTER_CONFIG_DIR": str(tmp_path / "cfg")}
    bridge, out = team_bridge(tmp_path, fake.port, load_key=lambda ref: keys.load_key(ref, env))
    handshake(bridge)
    assert out.wait_for(lambda m: m.get("id") == 1)["result"]["capabilities"] == {"tools": {}}
    bridge.handle(msg("tools/list", 2))
    assert out.wait_for(lambda m: m.get("id") == 2)["result"]["tools"]
    names = [t["name"] for t in out.wait_for(lambda m: m.get("id") == 2)["result"]["tools"]]
    assert names and "backlog_server_setup" in names and "backlog_update_task" in names
    bridge.handle(call("write", 3))
    result = out.wait_for(lambda m: m.get("id") == 3)["result"]
    assert result["isError"] and "taskmaster remote set-key" in result["content"][0]["text"]
    assert fake.inits == 0 and fake.writes == 0 and bridge.fallback is None


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl to make a throwaway certificate")
def test_https_verifies_the_certificate_no_insecure_override(tmp_path):
    cert, pem = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(pem), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=localhost"], check=True, capture_output=True)
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(self.path)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(pem))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        bridge, out = team_bridge(tmp_path, httpd.server_address[1], scheme="https", budget=0.5)
        bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
        text = out.wait_for(lambda m: m.get("id") == 1)["error"]["message"]
        assert "was not sent" in text and seen == []
        bridge.closed.set()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_entry_picks_the_team_bridge_only_for_a_marked_clone(tmp_path, monkeypatch):
    (tmp_path / ".taskmaster").mkdir()
    monkeypatch.chdir(tmp_path)
    log = tb.Log(stream=io.StringIO())
    assert tb._start_team_bridge(log) is None                         # solo: nothing imported, nothing started
    (tmp_path / ".taskmaster-server.json").write_text(json.dumps(
        {"format": 1, "host_id": "0" * 32, "mode": "server", "url": "http://127.0.0.1:9/mcp"}), encoding="utf-8")
    monkeypatch.setenv("TASKMASTER_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("TASKMASTER_SERVER_KEY", raising=False)
    monkeypatch.delenv("TASKMASTER_SERVER_URL", raising=False)
    bridge = tb._start_team_bridge(log)
    assert isinstance(bridge, tb.TeamBridge) and bridge.refusal and "set-key" in bridge.refusal
