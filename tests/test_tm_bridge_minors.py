# User intent: pin the bridge's end-of-branch minor fixes (replaced sessions are ended, one bad message never ends a session,
# the operator token goes to loopback only, a cache is only written by its own build, an unknown protocol is not echoed).
from __future__ import annotations

import io
import json
import time

import pytest

import tm_bridge as tb
from test_tm_bridge import BUILD, OLDER, FakeServer, Out, msg, make_bridge, handshake, server, project  # noqa: F401
from test_tm_bridge_coldstart import lazy_bridge, serve_dir, LIVE_INIT, LIVE_TOOLS  # noqa: F401


def test_a_replaced_upstream_session_is_deleted(server, tmp_path):
    fake = server()
    bridge, out = make_bridge(fake.endpoint, tmp_path)
    handshake(bridge)
    fake.drop_once_methods = {"tools/list"}
    bridge.handle(msg("tools/list", 9))
    out.wait_for(lambda m: m.get("id") == 9)
    assert "sess-1" in fake.deleted and fake.inits == 2
    bridge.closed.set()


def test_the_endpoint_refuses_a_non_loopback_host():
    with pytest.raises(ValueError):
        tb.Endpoint({"url": "http://192.0.2.5:9/mcp", "build": BUILD}, "tok")
    assert tb.Endpoint({"url": "http://127.0.0.1:9/mcp", "build": BUILD}, "tok").port == 9


def test_an_unparseable_line_gets_a_parse_error_and_a_batch_is_handled_message_by_message(server, tmp_path):
    fake = server()
    bridge, out = make_bridge(fake.endpoint, tmp_path)
    batch = json.dumps([msg("initialize", 1, protocolVersion="2025-06-18"), msg("ping", 2)])
    bridge.run(stdin=io.BytesIO(b"{not json\n" + batch.encode() + b"\n"))
    assert out.wait_for(lambda m: m.get("id") is None and "error" in m)["error"]["code"] == -32700
    assert "result" in out.wait_for(lambda m: m.get("id") == 1)
    assert out.wait_for(lambda m: m.get("id") == 2)


def test_reconnect_does_nothing_once_the_session_is_on_the_fallback(tmp_path):
    def never():
        raise AssertionError("must not launch")
    bridge = tb.Bridge(tmp_path, BUILD, "d", stdout=Out(), ensure=never, log=tb.Log(stream=io.StringIO()))
    bridge.fallback = object()
    assert bridge.reconnect(bridge.epoch) is True


def test_a_cache_is_not_written_from_a_server_of_another_build(project, server):
    fake = server(build=OLDER)
    bridge, _ = lazy_bridge(project, fake.endpoint)
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    bridge.handle(msg("notifications/initialized"))
    bridge.upstream_ready.wait(10)
    time.sleep(1.0)
    assert not bridge.cache_path().exists()        # (load_cache would fall back to the shipped file)
    bridge.close()


def test_an_unknown_protocol_version_is_not_echoed_back(project, server):
    fake = server()
    bridge, out = lazy_bridge(project, fake.endpoint)
    bridge.handle(msg("initialize", 1, protocolVersion="1999-01-01"))
    assert out.wait_for(lambda m: m.get("id") == 1)["result"]["protocolVersion"] in tb.KNOWN_PROTOCOLS
    bridge.close()


def test_stale_launch_files_are_swept_and_fresh_ones_kept(tmp_path):
    import os
    old, fresh = tmp_path / "launch-aa.json", tmp_path / "launch-bb.pid"
    old.write_text("{}")
    fresh.write_text("1")
    past = time.time() - 7200
    os.utime(old, (past, past))
    tb._sweep_stale_launch_files(tmp_path)
    assert not old.exists() and fresh.exists()


# -- team bridge (018 M1 M2 M3 M4 M5) -----------------------------------------------------------------------------------

def test_no_key_tools_cover_every_tool_name_the_mods_call():
    import re
    from pathlib import Path
    mods = Path(__file__).resolve().parents[1] / "mods" / "taskmaster-mods"
    called = set()
    for folder in ("hooks", "scanner"):
        for path in (mods / folder).rglob("*.ts"):
            called |= set(re.findall(r"\bbacklog_[a-z_]+", path.read_text(encoding="utf-8")))
    assert called, "the mods call no tm tools?"
    assert called <= set(tb.NO_KEY_TOOLS), sorted(called - set(tb.NO_KEY_TOOLS))


def test_a_refused_key_is_reloaded_on_the_next_call_without_a_restart(server, tmp_path):
    from types import SimpleNamespace
    from test_tm_bridge import TOKEN
    from test_tm_bridge_team import team_bridge
    from test_tm_bridge import call
    fake = server()
    holder = {"value": "wrong-key"}
    bridge, out = team_bridge(tmp_path, fake.port, load_key=lambda ref: SimpleNamespace(value=holder["value"]))
    bridge.handle(call("write", 5))
    text = out.wait_for(lambda m: m.get("id") == 5)["error"]["message"]
    assert "set-key" in text and "repeat the call" in text
    holder["value"] = TOKEN                                  # the member ran `taskmaster remote set-key`
    handshake(bridge)
    bridge.handle(call("write", 6))
    assert out.wait_for(lambda m: m.get("id") == 6)["result"]["content"][0]["text"] == "ok write"
    bridge.closed.set()


def test_a_proxy_502_on_a_call_says_it_may_have_committed(tmp_path):
    from test_tm_bridge_team import StatusServer, team_bridge
    from test_tm_bridge import call
    fake = StatusServer(502, "bad gateway")
    try:
        bridge, out = team_bridge(tmp_path, fake.port)
        bridge.handle(call("write", 3))
        text = out.wait_for(lambda m: m.get("id") == 3)["error"]["message"]
        assert "may have committed" in text
        bridge.closed.set()
    finally:
        fake.stop()


def test_an_oversized_response_is_refused(monkeypatch):
    class Resp:
        def getheader(self, name):
            return "application/json"

        def read(self, n=None):
            return b"x" * (n or 10)
    monkeypatch.setattr(tb, "MAX_BODY_BYTES", 5)
    with pytest.raises(OSError):
        list(tb.Bridge.events(Resp()))
