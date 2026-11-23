# User intent: a cancel of a held call - does a notifications/cancelled sent while a tools/call is held stop the call (no ghost write)?
import threading, time
import tm_bridge as tb
from test_tm_bridge import FakeServer, Out, call, msg, project, server  # noqa: F401
from test_tm_bridge_coldstart import Slow, lazy_bridge


def _run(project, server, release_after):
    fake = server()
    slow = Slow(fake)
    bridge, out = lazy_bridge(project, slow)
    bridge.list_hold = 0.5
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    bridge.handle(msg("notifications/initialized"))
    threading.Thread(target=bridge.handle, args=(call("write", 7),), daemon=True).start()
    time.sleep(0.2)
    threading.Thread(target=bridge.handle, args=({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 7, "reason": "user"}},), daemon=True).start()
    time.sleep(release_after)
    slow.gate.set()
    deadline = time.monotonic() + 10
    while fake.writes == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    writes = fake.writes
    bridge.close()
    return writes


def test_cancel_then_server_up_late(project, server):
    assert _run(project, server, 1.5) == 0, "held call ran after the client cancelled it"


def test_cancel_then_server_up_soon(project, server):
    assert _run(project, server, 0.1) == 0, "held call ran after the client cancelled it"
