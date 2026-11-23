# User intent: prove the bridge answers Claude Code's handshake at once and waits for a slow (cold uv environment) server in the
# background, so the first session after an install or upgrade still has tm; and that a launch that is merely slow is waited out.
from __future__ import annotations

import io
import json
import sys
import threading
import time
from pathlib import Path

import pytest

import tm_bridge as tb
from taskmaster import localserve as ls
from test_tm_bridge import BUILD, FAKE_STDIO, PLUGIN_ROOT, FakeServer, Out, call, msg, project, server  # noqa: F401

LIVE_INIT = {"protocolVersion": "2025-06-18", "capabilities": {}}      # what FakeServer answers initialize with
LIVE_TOOLS = [{"name": "write"}]                                        # and tools/list


class Slow:
    """An ensure() that returns only when released, like a launch waiting on a cold uv environment."""

    def __init__(self, fake=None, error=None):
        self.fake, self.error, self.gate = fake, error, threading.Event()

    def __call__(self):
        assert self.gate.wait(30), "the test never released the server"
        if self.error:
            raise self.error
        return self.fake.endpoint()


def lazy_bridge(project, ensure, **kw):
    out = Out()
    hold = kw.pop("hold_bound", None)
    bridge = tb.Bridge(project, BUILD, "disc-1", stdout=out, ensure=ensure, cwd=str(project), log=tb.Log(stream=io.StringIO()),
                       budget=10, **kw)
    if hold is not None:
        bridge.hold_bound = hold
    bridge.start_lazy()
    return bridge, out


def serve_dir(project):
    from taskmaster.coordinator.ownership import private_directory
    return private_directory(project, ls.SERVE_DIR)


def seed_cache(bridge, init, tools):
    serve_dir(bridge.root)
    bridge.save_cache(init, tools)


def test_initialize_is_answered_before_the_server_is_up_with_no_cache_and_the_list_waits(project, server, tmp_path):
    fake = server()
    slow = Slow(fake)
    bridge, out = lazy_bridge(project, slow, plugin_dir=tmp_path)
    bridge.list_hold = 5.0
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    reply = out.wait_for(lambda m: m.get("id") == 1, timeout=2)               # no server yet, and it still answers
    assert reply["result"]["capabilities"]["tools"]["listChanged"] is True and reply["result"]["protocolVersion"] == "2025-06-18"
    bridge.handle(msg("notifications/initialized"))
    threading.Thread(target=bridge.handle, args=(msg("tools/list", 2),), daemon=True).start()
    time.sleep(0.4)
    assert not [m for m in out.lines if m.get("id") == 2]                      # no cache: held, not guessed
    slow.gate.set()
    assert out.wait_for(lambda m: m.get("id") == 2)["result"]["tools"] == LIVE_TOOLS
    assert fake.inits == 1
    deadline = time.monotonic() + 10
    while bridge.load_cache() == (None, None) and time.monotonic() < deadline:
        time.sleep(0.05)
    cached_init, cached_tools = bridge.load_cache()
    assert cached_init["protocolVersion"] == "2025-06-18" and cached_tools == LIVE_TOOLS    # the next session starts warm
    bridge.close()


def test_with_no_cache_a_list_that_waits_too_long_is_answered_empty_and_list_changed_brings_the_real_one(project, server, tmp_path):
    fake = server()
    slow = Slow(fake)
    bridge, out = lazy_bridge(project, slow, plugin_dir=tmp_path)
    bridge.list_hold = 0.3
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    bridge.handle(msg("notifications/initialized"))
    bridge.handle(msg("tools/list", 2))                                        # blocks 0.3 s, then answers
    assert out.wait_for(lambda m: m.get("id") == 2)["result"]["tools"] == []
    slow.gate.set()
    out.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed")
    bridge.handle(msg("tools/list", 3))                                        # the client's refresh now reaches the server
    assert out.wait_for(lambda m: m.get("id") == 3)["result"]["tools"] == LIVE_TOOLS
    bridge.close()


def test_cached_initialize_and_tools_are_served_at_once_and_a_difference_emits_list_changed(project, server):
    fake = server()
    slow = Slow(fake)
    bridge, out = lazy_bridge(project, slow)
    seed_cache(bridge, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "instructions": "old text",
                        "serverInfo": {"name": "tm", "version": "1"}}, [{"name": "stale"}])
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    assert out.wait_for(lambda m: m.get("id") == 1, timeout=2)["result"]["instructions"] == "old text"
    bridge.handle(msg("notifications/initialized"))
    bridge.handle(msg("tools/list", 2))
    assert out.wait_for(lambda m: m.get("id") == 2, timeout=2)["result"]["tools"] == [{"name": "stale"}]
    assert not [m for m in out.lines if m.get("method") == "notifications/tools/list_changed"]
    slow.gate.set()
    out.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed")
    assert bridge.load_cache()[1] == LIVE_TOOLS                                # the cache now holds what is live
    bridge.close()


def test_no_list_changed_when_the_live_server_matches_what_was_served(project, server):
    fake = server()
    slow = Slow(fake)
    bridge, out = lazy_bridge(project, slow)
    seed_cache(bridge, LIVE_INIT, LIVE_TOOLS)
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    bridge.handle(msg("notifications/initialized"))
    bridge.handle(msg("tools/list", 2))
    slow.gate.set()
    assert bridge.upstream_ready.wait(10)
    time.sleep(0.5)
    assert not [m for m in out.lines if m.get("method") == "notifications/tools/list_changed"]
    bridge.close()


def test_cache_of_another_plugin_build_is_ignored(project, server, tmp_path):
    bridge, _ = lazy_bridge(project, Slow(server()), plugin_dir=tmp_path)           # a plugin dir with no shipped handshake
    seed_cache(bridge, LIVE_INIT, LIVE_TOOLS)
    bridge.build = {"version": "9.9.9", "digest": "f" * 32}
    assert bridge.load_cache() == (None, None)


def test_first_ever_run_serves_the_shipped_handshake_at_once_and_calls_wait_for_the_server(project, server):
    shipped = json.loads((PLUGIN_ROOT / "tm_bridge_handshake.json").read_text(encoding="utf-8"))
    fake = server()
    slow = Slow(fake)
    bridge, out = lazy_bridge(project, slow)                                   # no cache file: the shipped one applies
    assert not bridge.cache_path().exists()
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    reply = out.wait_for(lambda m: m.get("id") == 1, timeout=2)["result"]
    assert reply["instructions"] == shipped["initialize"]["instructions"] and reply["serverInfo"]["name"] == "taskmaster"
    bridge.handle(msg("notifications/initialized"))
    bridge.handle(msg("tools/list", 2))
    assert out.wait_for(lambda m: m.get("id") == 2, timeout=2)["result"]["tools"] == shipped["tools"]      # no wait, not empty
    threading.Thread(target=bridge.handle, args=(call("write", 3),), daemon=True).start()
    time.sleep(0.4)
    assert fake.writes == 0                                                     # the call waits for the server
    slow.gate.set()
    assert out.wait_for(lambda m: m.get("id") == 3)["result"]["content"][0]["text"] == "ok write"
    out.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed")      # the fake's list differs from the shipped one
    bridge.close()


def test_the_shipped_handshake_equals_the_servers_live_registration():
    import surface_inventory as inv
    live = inv.handshake_snapshot()
    shipped = json.loads(inv.HANDSHAKE.read_text(encoding="utf-8"))
    assert shipped == live, "tools or instructions changed: run `python tests/surface_inventory.py --write-handshake` and commit tm_bridge_handshake.json"


def test_calls_that_arrive_before_the_server_is_ready_wait_and_are_then_served(project, server):
    fake = server()
    slow = Slow(fake)
    bridge, out = lazy_bridge(project, slow)
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    bridge.handle(msg("notifications/initialized"))
    threading.Thread(target=bridge.handle, args=(call("write", 7),), daemon=True).start()
    time.sleep(0.4)
    assert not [m for m in out.lines if m.get("id") == 7] and fake.writes == 0
    slow.gate.set()
    assert out.wait_for(lambda m: m.get("id") == 7)["result"]["content"][0]["text"] == "ok write"
    assert fake.writes == 1                                                    # ran once, after the session existed
    bridge.close()


def test_server_discover_is_answered_method_not_found_at_once_and_other_requests_give_up_before_the_client_does(project, server):
    slow = Slow(server())
    bridge, out = lazy_bridge(project, slow)
    bridge.list_hold = 0.3
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    bridge.handle(msg("server/discover", "probe"))
    assert out.wait_for(lambda m: m.get("id") == "probe", timeout=1)["error"]["code"] == -32601
    bridge.handle(msg("prompts/list", 4))
    assert out.wait_for(lambda m: m.get("id") == 4, timeout=1)["result"] == {"prompts": []}
    bridge.handle(msg("completion/complete", 6))                               # not answerable without the server: an error, not a late reply
    assert "error" in out.wait_for(lambda m: m.get("id") == 6)
    slow.gate.set()
    bridge.close()


def test_a_wait_past_the_bound_fails_the_call_as_not_sent(project, server):
    slow = Slow(server())
    bridge, out = lazy_bridge(project, slow, hold_bound=0.3)
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    bridge.handle(call("write", 5))
    err = out.wait_for(lambda m: m.get("id") == 5)
    assert "error" in err and "this call was not sent" in err["error"]["message"]
    slow.gate.set()
    bridge.close()


def test_when_the_server_cannot_be_had_held_calls_go_to_the_stdio_fallback(project):
    slow = Slow(error=tb.ServerUnavailable("launch failed"))
    bridge, out = lazy_bridge(project, slow, fallback_command=[sys.executable, "-c", FAKE_STDIO])
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    out.wait_for(lambda m: m.get("id") == 1, timeout=2)
    bridge.handle(msg("notifications/initialized"))
    threading.Thread(target=bridge.handle, args=(call("write", 9),), daemon=True).start()
    slow.gate.set()
    assert out.wait_for(lambda m: m.get("id") == 9)["result"]["content"][0]["text"] == "stdio write"
    out.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed")
    bridge.close()


def test_the_fallback_does_not_start_while_an_environment_build_is_running(project, monkeypatch):
    started = []
    monkeypatch.setattr(tb.subprocess, "Popen", lambda *a, **k: started.append(1) or (_ for _ in ()).throw(OSError("stop here")))
    bridge = tb.Bridge(project, BUILD, "d", stdout=Out(), ensure=lambda: None, log=tb.Log(stream=io.StringIO()),
                       fallback_command=["x"])
    tb._ENV_LOCK.acquire()                                      # a launch's uv build is in progress
    try:
        worker = threading.Thread(target=lambda: _swallow(bridge._switch_to_fallback, "why"), daemon=True)
        worker.start()
        time.sleep(0.4)
        assert not started                                      # it waits instead of racing the build
    finally:
        tb._ENV_LOCK.release()
    worker.join(5)
    assert started == [1]


def _swallow(fn, *a):
    try:
        fn(*a)
    except OSError:
        pass


# -- the launch wait ---------------------------------------------------------------------------------------------------

SLOW_SERVE = '''
import json, sys, time
time.sleep(float(sys.argv[2]))                                  # a cold uv environment build
open(sys.argv[1], "w").write(json.dumps({"status": "ready", "reason": "", "pid": 1}))
time.sleep(5)
'''


def test_a_slow_but_alive_server_start_is_waited_out_past_the_old_fifteen_second_cap(project, monkeypatch):
    monkeypatch.setattr(ls, "RESULT_WAIT_SECONDS", 0.5)         # the old cap would have given up at once
    started = time.monotonic()
    result = tb.launch_server(project, PLUGIN_ROOT, server_argv=[sys.executable, "-c", SLOW_SERVE, "{result_file}", "2.5"])
    assert result["status"] == "ready" and time.monotonic() - started >= 2.4


def test_a_server_that_died_without_a_result_ends_the_wait_early(project):
    started = time.monotonic()
    result = tb.launch_server(project, PLUGIN_ROOT, server_argv=[sys.executable, "-c", "pass"], wait=120)
    assert result["status"] == "error" and "no launch result" in result["reason"] and time.monotonic() - started < 30


def test_the_wait_is_bounded(project):
    started = time.monotonic()
    result = tb.launch_server(project, PLUGIN_ROOT, server_argv=[sys.executable, "-c", "import time; time.sleep(20)"], wait=1.5)
    assert result["status"] == "error" and "within 1.5s" in result["reason"] and time.monotonic() - started < 15
    assert not list(serve_dir(project).glob("launch-*"))                 # neither the result nor the pid file is left behind


def test_the_handoff_experiment_is_gone():
    assert not hasattr(tb, "handoff_to_child")
    assert "TASKMASTER_BRIDGE_HANDOFF" not in Path(tb.__file__).read_text(encoding="utf-8")
