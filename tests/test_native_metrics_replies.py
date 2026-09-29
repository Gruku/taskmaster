# User intent: N16 track C - tool and viewer replies report their output bytes only in the
# opt-in instrumented pass, and the disabled path leaves every reply byte-identical.
"""Opt-in `tool` and `http` metrics records (reply output bytes)."""
import json

from taskmaster.native import metrics
from test_native_metrics import kinds, recording
from test_native_service import root  # noqa: F401


def test_tool_reply_bytes_are_the_outermost_replys_utf8_size(root):
    from taskmaster import backlog_server as bs
    with recording():
        reply = bs.backlog_get_task(task_id="test-epic-001")
    tools = kinds("tool")
    assert [record["op"] for record in tools] == ["backlog_get_task"]
    expected = reply if isinstance(reply, str) else json.dumps(reply, default=str)
    assert tools[0]["output_bytes"] == len(expected.encode("utf-8")) and tools[0]["ms"] > 0


def test_viewer_json_reply_records_bytes_and_server_timing():
    import io
    from taskmaster import backlog_server as bs

    class Handler(bs.ViewerHandler):
        def __init__(self):
            self.command, self.path, self.wfile, self.headers_sent = "GET", "/api/board?x=1", io.BytesIO(), []

        def send_response(self, status):
            self.headers_sent.append(status)

        def send_header(self, name, value):
            self.headers_sent.append((name, value))

        def end_headers(self):
            pass
    quiet, measured = Handler(), Handler()
    metrics.clear()
    quiet._send_json(200, {"ok": True, "rows": [1, 2]}, timings={"read": 1.5})
    assert metrics.records() == []
    with recording():
        measured._send_json(200, {"ok": True, "rows": [1, 2]}, timings={"read": 1.5})
    [record] = kinds("http")
    assert measured.wfile.getvalue() == quiet.wfile.getvalue()
    assert record["output_bytes"] == len(measured.wfile.getvalue())
    assert (record["method"], record["path"], record["status"], record["read_ms"]) == ("GET", "/api/board", 200, 1.5)
    assert record["encode_ms"] >= 0
