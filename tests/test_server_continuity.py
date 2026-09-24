import importlib
import json
import threading
from socketserver import BaseServer

import pytest


def _viewer_servers():
    """Servers whose `serve_forever` a live thread is running."""
    servers = set()
    for thread in threading.enumerate():
        target = getattr(thread, "_target", None)
        server = getattr(target, "__self__", None)
        if isinstance(server, BaseServer) and getattr(target, "__name__", "") == "serve_forever":
            servers.add(server)
    return servers


@pytest.fixture
def in_backlog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.write_text(
        "meta:\n  schema_version: 3\nepics: []\n",
        encoding="utf-8",
    )
    import taskmaster.backlog_server as srv
    before = _viewer_servers()
    # The reload re-runs import-time startup, which binds a fresh viewer server;
    # close it afterwards so repeated reloads do not leak serving threads.
    importlib.reload(srv)
    try:
        yield srv, bp
    finally:
        closed = _viewer_servers() - before
        for server in closed:
            server.shutdown()
            server.server_close()
        if closed:
            srv._viewer_started = False  # a later caller starts a live one, not the closed port


def test_backlog_continuity_items_returns_json_with_items_array(in_backlog):
    srv, _ = in_backlog
    srv.backlog_decision_create(title="x", options=["a", "b"])
    out = srv.backlog_continuity_items()
    data = json.loads(out)
    assert "items" in data
    assert any(i["type"] == "decision" for i in data["items"])
    for it in data["items"]:
        assert {"id", "type", "title", "action_class", "timestamp"} <= set(it)


def test_backlog_continuity_items_filters_auto_stage_by_default(in_backlog):
    srv, _ = in_backlog
    srv.backlog_handover_create(
        tldr="auto stub", session_kind="auto-stage", body="", task_ids=[],
    )
    items = json.loads(srv.backlog_continuity_items())["items"]
    assert not any(i["type"] == "handover" and i["title"] == "auto stub" for i in items)
    items_all = json.loads(srv.backlog_continuity_items(include_auto_stage=True))["items"]
    assert any(i["title"] == "auto stub" for i in items_all)
