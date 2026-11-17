# User intent: the agent in a command's actor comes from the MCP clientInfo captured once per server process
# (viewer and CLIs send nothing, merge-stamp replay acts as taskmaster), so receipts say which kind of agent wrote.
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing

import mcp.types
import pytest
from fastmcp import Client

from taskmaster import backlog_server as bs
from taskmaster.coordinator import adapter
from taskmaster.coordinator.protocol import ServiceUnavailable
from taskmaster.native_routing import merge_stamps, runtime, viewer
from native_twins import make_twins, native_database
import native_projection_oracle


def _initialize(name):
    async def run():
        async with Client(bs.mcp._inner, client_info=mcp.types.Implementation(name=name, version="1")):
            pass
    asyncio.run(run())


def test_initialize_captures_the_client_agent_once(monkeypatch):
    monkeypatch.setattr(bs, "_CLIENT_AGENT", None)
    _initialize("claude-code")
    assert bs.client_agent() == "claude"
    _initialize("codex-mcp-client")
    assert bs.client_agent() == "claude"


def test_unknown_client_maps_to_empty(monkeypatch):
    monkeypatch.setattr(bs, "_CLIENT_AGENT", None)
    _initialize("cursor")
    assert bs.client_agent() == ""


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, lambda: bs.backlog_add_task(title="Seed", epic="test-epic", phase="dev"))


def test_tool_writes_send_the_captured_agent(twins, monkeypatch):
    monkeypatch.setattr(bs, "_CLIENT_AGENT", "claude")
    with twins.at(twins.native):
        assert not bs.backlog_add_task(title="Agent task", epic="test-epic", phase="dev").startswith("Error")
    with closing(sqlite3.connect(native_database(twins.native))) as connection:
        outcome = connection.execute("SELECT outcome_json FROM command_receipts ORDER BY commit_seq DESC LIMIT 1").fetchone()[0]
    assert json.loads(outcome)["actor"]["agent"] == "claude"


def test_native_call_sends_its_agent(tmp_path, monkeypatch):
    sent = []

    class Recorder:
        def __init__(self, *args, **kwargs):
            pass

        def execute(self, envelope):
            sent.append(envelope)
            return {"receipt": {"affected": [], "commit_seq": 0},
                    "projection": {"state": "unchanged", "through": 0, "notices": []}}
    monkeypatch.setattr(adapter, "Client", Recorder)
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE native_manifest(key TEXT, value TEXT)")
    monkeypatch.setattr(adapter.NativeCall, "read", _fake_read)
    call = adapter.NativeCall(connection, tmp_path, "s", agent="codex")
    call.execute("task.patch", {})
    assert sent[0]["actor"] == {"agent": "codex"}
    adapter.NativeCall(connection, tmp_path, "s").execute("task.patch", {})
    assert sent[1]["actor"] == {"agent": ""}


from contextlib import contextmanager  # noqa: E402


@contextmanager
def _fake_read(self):
    class Snapshot:
        identity = {"store_id": "s"}
    yield Snapshot()


def test_native_call_rejects_a_non_text_agent(tmp_path):
    with pytest.raises(ValueError):
        adapter.NativeCall(sqlite3.connect(":memory:"), tmp_path, "s", agent=None)


def test_viewer_writes_send_no_agent(twins, monkeypatch):
    monkeypatch.setattr(bs, "_CLIENT_AGENT", "claude")
    with twins.at(twins.native):
        with viewer._open(native_database(twins.native)) as call:
            assert call.agent == ""


def test_merge_stamp_replay_acts_as_taskmaster(tmp_path, monkeypatch):
    seen = []

    def recorder(*args, **kwargs):
        seen.append(kwargs.get("agent"))
        raise ServiceUnavailable("down")
    monkeypatch.setattr(runtime, "open_call", recorder)
    apply = merge_stamps.native_apply(tmp_path / "store.db", tmp_path / ".taskmaster", "s", autostart=False)
    with pytest.raises(merge_stamps.Unavailable):
        apply({"task_id": "x", "rung": "r", "sha": "a" * 40})
    assert seen == ["taskmaster"]


def test_engine_oracle_double_accepts_an_agent(twins):
    database = native_database(twins.native)
    with native_projection_oracle.open_call(database, twins.native / ".taskmaster", "s", agent="claude") as call:
        assert call.agent == "claude"
