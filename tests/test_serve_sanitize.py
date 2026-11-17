# User intent: tool and coordinator errors reach a remote caller without paths, SQL or tracebacks, with a ref the server log shares; solo callers see what they always saw.
import asyncio
import json
import logging
import sqlite3
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from serve_helpers import TwoKeyAuthenticator, running_app
from taskmaster.coordinator.service import Coordinator
from taskmaster.serve.app import build_app
from taskmaster.serve.auth import OPERATOR
from taskmaster.serve.guard import RequestGuard
from taskmaster.serve.limits import failure_budget
from taskmaster.serve.sanitize import SanitizeErrors, coordinator_public_error, internal_message, scrub

TOKEN = "op-token"
SUFFIX = "; the operator can look it up in the server log"


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "proj"
    path.mkdir()
    return path


class RecordIds:
    """An outer ASGI wrapper recording each response's x-request-id."""

    def __init__(self, app):
        self.app = app
        self.ids = []

    async def __call__(self, scope, receive, send):
        async def recording(message):
            if message["type"] == "http.response.start":
                for name, value in message.get("headers") or []:
                    if name == b"x-request-id":
                        self.ids.append(value.decode())
            await send(message)
        await self.app(scope, receive, recording)


def http_stack(server):
    auth = TwoKeyAuthenticator({TOKEN: OPERATOR})
    return RecordIds(RequestGuard(build_app(server, auth), max_body_bytes=2097152, body_timeout=30.0,
                                  failures=failure_budget(), authenticator=auth))


def call(url, name, arguments=None):
    async def go():
        async with Client(url, auth=TOKEN) as client:
            result = await client.call_tool(name, arguments or {}, raise_on_error=False)
            return result.content[0].text
    return asyncio.run(go())


def ref_of(text):
    return text.split("(ref ", 1)[1].split(")", 1)[0]


def toy(root):
    mcp = FastMCP("toy")

    @mcp.tool
    def boom() -> str:
        raise sqlite3.OperationalError(f"no such table: x at {root}")

    @mcp.tool
    def nope() -> str:
        return f"Error: {root}/.taskmaster/a.yaml is locked"

    @mcp.tool
    def nope2() -> str:
        return "Error: UNIQUE constraint failed: tasks.id"

    @mcp.tool
    def backlog_query(kind: str = "text") -> str:
        if kind == "json":
            return json.dumps({"error": "no such column: foo"})
        return f"Error: no such column: foo under {root}/db"

    @mcp.tool
    def deliberate() -> str:
        raise ToolError(f"bad id under {root}")

    @mcp.tool
    def json_path() -> str:
        return json.dumps({"error": f"cannot read {root}/.taskmaster/a.yaml"})

    @mcp.tool
    def json_sql() -> str:
        return json.dumps({"error": "no such table: secret", "seq": 3})

    @mcp.tool
    def json_payload() -> str:
        return json.dumps({"error": f"conflict at {root}/x", "type": "Conflict", "current": {"etag": "e1"}})

    @mcp.tool
    def json_success() -> str:
        return json.dumps({"tasks": [], "note": f"{root}"})

    @mcp.tool
    def a_list() -> str:
        return "[1, 2]"

    @mcp.tool
    def not_json() -> str:
        return "{not json"

    mcp.add_middleware(SanitizeErrors(root))
    return mcp


@pytest.fixture
def served(root):
    stack = http_stack(toy(root))
    with running_app(stack) as url:
        yield url + "/mcp", stack


def test_scrub_replaces_root_and_other_paths(root):
    win = scrub(f"Error: {root}\\.taskmaster\\x.yaml missing", root)
    assert win.text == "Error: <root>\\.taskmaster\\x.yaml missing" and win.changed
    fwd = scrub(f"Error: {str(root).replace(chr(92), '/')}/.taskmaster/x.yaml", root)
    assert fwd.text == "Error: <root>/.taskmaster/x.yaml"
    assert scrub("read /srv/other/secret.db now", root).text == "read <path> now"
    assert scrub("at C:\\Users\\bob\\repo", root).text == "at <path>"
    for kept in ("see https://tm.example.com/mcp/x", "/mcp", "route /mcp alone"):
        result = scrub(kept, root)
        assert result.text == kept and not result.changed
    for text in (f"{root}/x", "/srv/other/secret.db", "C:\\Users\\bob\\repo", "/mcp"):
        assert scrub(text, root).internal is False


@pytest.mark.parametrize("text", ["UNIQUE constraint failed: tasks.id", "sqlite3.OperationalError: no such table: x",
                                  "Traceback (most recent call last):", "database is locked"])
def test_scrub_flags_sql_and_tracebacks(root, text):
    assert scrub(text, root).internal is True


def test_tool_exception_over_http_is_sanitized_and_logged(served, root, caplog):
    url, stack = served
    with caplog.at_level(logging.INFO, logger="taskmaster.serve"):
        text = call(url, "boom")
    ref = ref_of(text)
    assert ref in stack.ids                 # the x-request-id of the HTTP request that carried the call
    assert text == "Error: " + internal_message("boom", ref)
    assert "no such table" not in text and str(root) not in text
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and "tool error" in r.getMessage()]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert f"ref={ref}" in message and "tool=boom" in message and "principal=operator" in message
    assert "type=OperationalError" in message and errors[0].exc_info is not None


def test_error_text_result_is_scrubbed(served, caplog):
    url, _ = served
    with caplog.at_level(logging.INFO, logger="taskmaster.serve"):
        assert call(url, "nope") == "Error: <root>/.taskmaster/a.yaml is locked"
    assert any("scrubbed tool error" in r.getMessage() and "a.yaml is locked" in r.getMessage()
               for r in caplog.records)


def test_sql_error_text_becomes_internal(served):
    url, stack = served
    text = call(url, "nope2")
    assert ref_of(text) in stack.ids
    assert text == "Error: " + internal_message("nope2", ref_of(text)) and "UNIQUE" not in text


def test_backlog_query_keeps_its_sql_errors(served):
    url, _ = served
    assert call(url, "backlog_query") == "Error: no such column: foo under <root>/db"
    assert json.loads(call(url, "backlog_query", {"kind": "json"})) == {"error": "no such column: foo"}


def test_deliberate_tool_error_passes_scrubbed(served):
    url, _ = served
    assert call(url, "deliberate") == "bad id under <root>"


def test_json_error_results_are_scrubbed(served, caplog):
    url, stack = served
    assert json.loads(call(url, "json_path")) == {"error": "cannot read <root>/.taskmaster/a.yaml"}
    with caplog.at_level(logging.INFO, logger="taskmaster.serve"):
        sql = json.loads(call(url, "json_sql"))
    assert ref_of(sql["error"]) in stack.ids
    assert sql == {"error": internal_message("json_sql", ref_of(sql["error"])), "seq": 3}
    assert any("scrubbed tool error" in r.getMessage() and "secret" in r.getMessage() for r in caplog.records)
    payload = json.loads(call(url, "json_payload"))
    assert payload == {"error": "conflict at <root>/x", "type": "Conflict", "current": {"etag": "e1"}}


def test_success_results_are_untouched(served, root):
    url, _ = served
    assert json.loads(call(url, "json_success")) == {"tasks": [], "note": f"{root}"}
    assert call(url, "a_list") == "[1, 2]"
    assert call(url, "not_json") == "{not json"


def test_solo_in_memory_call_is_unchanged(root, caplog):
    async def go():
        async with Client(toy(root)) as client:
            return (await client.call_tool("boom", {}, raise_on_error=False)).content[0].text
    with caplog.at_level(logging.INFO, logger="taskmaster.serve"):
        text = asyncio.run(go())
    assert "no such table" in text
    assert not [r for r in caplog.records if r.name == "taskmaster.serve"]


def test_real_json_error_sites_are_scrubbed(monkeypatch, root):
    from taskmaster import backlog_server as bs
    from taskmaster.native_routing import gate
    monkeypatch.setattr(bs, "_backlog_path", lambda: root / ".taskmaster" / "backlog.yaml")

    def unavailable(path):
        raise gate.NativeUnavailable(f"bad {root}/x")
    monkeypatch.setattr(gate, "native_database", unavailable)
    sanitizer = SanitizeErrors(root)
    bs.mcp._inner.add_middleware(sanitizer)
    try:
        with running_app(http_stack(bs.mcp._inner)) as url:
            text = call(url + "/mcp", "backlog_changes_since", {"cursor": ""})
    finally:
        bs.mcp._inner.middleware.remove(sanitizer)
    assert json.loads(text)["error"] == "bad <root>/x"


def test_coordinator_public_error(root, caplog):
    log = logging.getLogger("taskmaster.serve.test")
    assert coordinator_public_error(ValueError(f"bad {root}/x"), root, log) == {"error": "bad <root>/x",
                                                                                "type": "ValueError"}
    with caplog.at_level(logging.ERROR, logger="taskmaster.serve.test"):
        answer = coordinator_public_error(sqlite3.OperationalError("database is locked"), root, log)
    assert answer["type"] == "ServiceUnavailable"
    assert answer["error"].startswith("internal error in the coordinator (ref c") and answer["error"].endswith(SUFFIX)
    ref = answer["error"].split("(ref ", 1)[1].split(")", 1)[0]
    record = next(r for r in caplog.records if "coordinator error" in r.getMessage())
    assert f"ref={ref}" in record.getMessage() and record.exc_info is not None
    assert coordinator_public_error(ValueError("UNIQUE constraint failed: t.id"), root, log)["type"] == \
        "ServiceUnavailable"


def test_base_coordinator_answer_is_unchanged():
    assert Coordinator.public_error(Coordinator.__new__(Coordinator), sqlite3.OperationalError("no such table: x")) == \
        {"error": "no such table: x", "type": "OperationalError"}


def test_caller_mistakes_keep_their_text(served):
    # Bad arguments and unknown tools echo only the caller's own input, so they are not made internal.
    url, _ = served
    assert "internal error" not in call(url, "backlog_query", {"kind": 5, "extra": 1})
    unknown = call(url, "no_such_tool")
    assert "no_such_tool" in unknown and "internal error" not in unknown



def test_dict_results_have_their_error_field_scrubbed(root):
    # 006 review M3: a dict-returning tool's structured_content is scrubbed too.
    from mcp.types import TextContent
    from fastmcp.tools.tool import ToolResult
    sanitizer = SanitizeErrors(root)
    result = ToolResult(content=[TextContent(type="text", text=json.dumps({"error": "no such table: t"}))],
                        structured_content={"error": "no such table: t", "n": 1})
    out = sanitizer._scrub_result(result, "project_get", "abc123abc123")
    assert out.structured_content == {"error": internal_message("project_get", "abc123abc123"), "n": 1}
    assert "no such table" not in out.content[0].text


def test_posix_paths_with_spaces_are_scrubbed(root):
    assert scrub("cannot read /home/u/My Project/x.yaml now", root).text == "cannot read <path> now"
