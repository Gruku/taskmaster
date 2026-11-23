# User intent: boot replay - a 503 "draining" answer to the boot's initialize replay: reconnect (old eager path) or permanent stdio fallback?
import sys, threading, time
import tm_bridge as tb
from test_tm_bridge import FAKE_STDIO, FakeServer, Out, call, msg, project, server  # noqa: F401
from test_tm_bridge_coldstart import Slow, lazy_bridge


def test_draining_on_boot_replay(project, server):
    fake = server()
    fake.draining_initializes = 1
    slow = Slow(fake)
    bridge, out = lazy_bridge(project, slow, fallback_command=[sys.executable, "-c", FAKE_STDIO])
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    bridge.handle(msg("notifications/initialized"))
    slow.gate.set()
    assert bridge.upstream_ready.wait(10)
    bridge.handle(call("write", 9))
    text = out.wait_for(lambda m: m.get("id") == 9)["result"]["content"][0]["text"]
    boot_error = repr(bridge.boot_error)
    bridge.close()
    assert text == "ok write", f"went to {text!r}; boot_error={boot_error}"
