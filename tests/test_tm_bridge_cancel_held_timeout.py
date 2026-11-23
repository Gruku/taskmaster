# User intent: a tool call the client cancelled while the bridge held it gets no answer on any branch, including the hold
# bound expiring (a late answer to an abandoned id makes Claude Code drop the whole connection).
import threading, time
from test_tm_bridge import FakeServer, Out, call, msg, project, server  # noqa: F401
from test_tm_bridge_coldstart import Slow, lazy_bridge


def test_cancelled_held_call_not_answered_on_hold_timeout(project, server):
    fake = server()
    slow = Slow(fake)
    bridge, out = lazy_bridge(project, slow, hold_bound=1.0)
    bridge.list_hold = 0.5
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    bridge.handle(msg("notifications/initialized"))
    threading.Thread(target=bridge.handle, args=(call("write", 7),), daemon=True).start()
    time.sleep(0.2)
    bridge.handle({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 7, "reason": "user"}})
    time.sleep(2.0)
    with out.lock:
        answered = [m for m in out.lines if m.get("id") == 7]
    slow.gate.set()
    bridge.close()
    assert answered == [], f"cancelled call 7 was answered: {answered}"
