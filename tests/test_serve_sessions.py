# User intent: each MCP connection gets its own server-minted session that survives the tool thread pool, stays bound to one key, and is forgotten when the connection ends.
from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from serve_helpers import open_connection, running_app
from taskmaster.native import claims
from taskmaster.serve import auth
from taskmaster.serve import sessions as sessions_module
from taskmaster.serve.app import build_app
from taskmaster.serve.auth import KeyAuthenticator, OperatorTokenAuthenticator, Principal, ensure_operator_token, token_path
from taskmaster.serve.principals import PrincipalRegistry, RegistryUnavailable, add_user, principals_path, revoke_user
from taskmaster.serve.sessions import ChannelSession, SessionRegistry

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}


def poll(predicate, deadline=10.0):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def toy(sessions):
    from fastmcp import FastMCP
    mcp = FastMCP("toy-sessions")

    def describe():
        bound = sessions.current()
        return json.dumps({"session": bound.session, "agent": bound.agent} if bound else None)

    @mcp.tool
    def whosession() -> str:
        return describe()

    @mcp.tool
    async def whosession_async() -> str:
        return describe()

    @mcp.tool
    def spawned() -> str:
        seen = {}

        def look(key):
            bound = sessions.current()
            seen[key] = bound.session if bound else None

        plain = threading.Thread(target=contextvars.Context().run, args=(look, "plain"))
        plain.start()
        plain.join()
        copied = threading.Thread(target=contextvars.copy_context().run, args=(look, "copied"))
        copied.start()
        copied.join()
        return json.dumps(seen)

    return mcp


@pytest.fixture
def world(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    ensure_operator_token(tmp_path)
    ann_key, ann, _ = add_user(tmp_path, "ann")
    bob_key, bob, _ = add_user(tmp_path, "bob")
    registry = PrincipalRegistry(principals_path(tmp_path))
    sessions = SessionRegistry(registry)
    authn = KeyAuthenticator(OperatorTokenAuthenticator(token_path(tmp_path)), registry)
    return SimpleNamespace(root=tmp_path, ann_key=ann_key, ann=ann.key_id, bob_key=bob_key, bob=bob.key_id,
                           registry=registry, sessions=sessions, authn=authn)


def app_for(world, **kwargs):
    return build_app(toy(world.sessions), world.authn, registry=world.registry, sessions=world.sessions,
                     sweep_seconds=0.2, **kwargs)


def answer(connection, tool="whosession"):
    return json.loads(connection.call(tool))


def test_a_connection_has_one_session(world):
    with running_app(app_for(world)) as url, open_connection(url + "/mcp", world.ann_key) as ann:
        seen = {answer(ann)["session"] for _ in range(10)}
    assert len(seen) == 1
    [session] = seen
    assert claims.REMOTE_SESSION_RE.fullmatch(session) and session.startswith("ann@")


def test_two_connections_have_two_sessions(world):
    with running_app(app_for(world)) as url, open_connection(url + "/mcp", world.ann_key) as a1, \
            open_connection(url + "/mcp", world.ann_key) as a2, open_connection(url + "/mcp", world.bob_key) as b:
        first, second, bob = answer(a1)["session"], answer(a2)["session"], answer(b)["session"]
    assert first != second and first.startswith("ann@") and second.startswith("ann@")
    assert bob.startswith("bob@")


def test_sessions_survive_the_thread_pool(world):
    with running_app(app_for(world)) as url, open_connection(url + "/mcp", world.ann_key) as ann, \
            open_connection(url + "/mcp", world.bob_key) as bob:
        own = {"ann": answer(ann)["session"], "bob": answer(bob)["session"]}
        barrier, wrong = threading.Barrier(2), []

        def hammer(name, connection):
            barrier.wait()
            for index in range(50):
                tool = "whosession_async" if index % 5 == 0 else "whosession"
                got = answer(connection, tool)["session"]
                if got != own[name]:
                    wrong.append((name, got))

        threads = [threading.Thread(target=hammer, args=("ann", ann)), threading.Thread(target=hammer, args=("bob", bob))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(300)
    assert wrong == []


def test_a_plain_thread_sees_no_session(world):
    with running_app(app_for(world)) as url, open_connection(url + "/mcp", world.ann_key) as ann:
        own = answer(ann)["session"]
        seen = json.loads(ann.call("spawned"))
    assert seen == {"plain": None, "copied": own}


def test_agent_is_per_connection(world):
    with running_app(app_for(world)) as url, open_connection(url + "/mcp", world.ann_key, "claude-code") as a, \
            open_connection(url + "/mcp", world.bob_key, "codex") as b:
        assert answer(a)["agent"] == "claude" and answer(b)["agent"] == "codex"


def test_session_nonce_is_not_the_transport_id(world, caplog):
    caplog.set_level(logging.INFO, logger="taskmaster.serve")
    with running_app(app_for(world)) as url:
        response = httpx.post(url + "/mcp", headers={"Authorization": f"Bearer {world.ann_key}", **ACCEPT}, json=INIT)
        sid = response.headers["mcp-session-id"]
        httpx.post(url + "/mcp", headers={"Authorization": f"Bearer {world.ann_key}", **ACCEPT, "mcp-session-id": sid},
                   json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        called = httpx.post(url + "/mcp", headers={"Authorization": f"Bearer {world.ann_key}", **ACCEPT,
                                                     "mcp-session-id": sid},
                            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": "whosession", "arguments": {}}})
        assert called.status_code == 200, called.text
    [entry] = list(world.sessions._by_session.values()) or [None]
    assert entry is not None and entry.transport_id == sid
    nonce = entry.session.split("@")[1]
    assert nonce not in sid and sid not in entry.session
    assert sid not in caplog.text and sid[:8] in caplog.text


class StubRequest:
    def __init__(self, headers):
        self.headers = headers


def test_missing_principal_or_session_id_fails_closed(world, monkeypatch):
    import fastmcp.server.dependencies as dependencies
    monkeypatch.setattr(dependencies, "get_http_request", lambda: StubRequest({"mcp-session-id": "abc"}))
    monkeypatch.setattr(auth, "current_key_id", lambda: "a" * 16)
    monkeypatch.setattr(auth, "current_principal", lambda: None)
    with pytest.raises(PermissionError, match="principal"):
        world.sessions.current()
    monkeypatch.setattr(auth, "current_principal", lambda: Principal("ann", "member", world.ann))
    monkeypatch.setattr(dependencies, "get_http_request", lambda: StubRequest({}))
    with pytest.raises(PermissionError, match="session id"):
        world.sessions.current()
    monkeypatch.setattr(dependencies, "get_http_request", lambda: StubRequest({"mcp-session-id": "bad id\n"}))
    with pytest.raises(PermissionError, match="session id"):
        world.sessions.current()


def test_provider_reads_the_key_through_current_key_id(world, monkeypatch):
    import fastmcp.server.dependencies as dependencies
    order = []
    monkeypatch.setattr(dependencies, "get_http_request", lambda: StubRequest({"mcp-session-id": "t-1"}))

    def key_id():
        order.append("key")
        raise PermissionError("operator token not host-local")

    monkeypatch.setattr(auth, "current_key_id", key_id)
    monkeypatch.setattr(auth, "current_principal", lambda: order.append("principal") or Principal("ann", "member", "x"))
    with pytest.raises(PermissionError):
        world.sessions.current()
    assert order == ["key"] and world.sessions._by_transport == {}


def test_binding_never_changes_key(world):
    world.sessions.bind("t-1", Principal("ann", "member", world.ann), "")
    with pytest.raises(PermissionError):
        world.sessions.bind("t-1", Principal("bob", "member", world.bob), "")


def test_connected_tracks_the_transport(world, caplog):
    caplog.set_level(logging.INFO, logger="taskmaster.serve")
    gone = []
    world.sessions.on_disconnect.append(gone.append)
    with running_app(app_for(world)) as url:
        ann = open_connection(url + "/mcp", world.ann_key)
        session = answer(ann)["session"]
        assert world.sessions.connected(session) is True
        assert [entry.session for entry in world.sessions.live()] == [session]
        ann.close()
        assert poll(lambda: not world.sessions.connected(session))
        assert poll(lambda: session not in world.sessions._by_session)
    assert [entry.session for entry in gone] == [session]
    assert f"session {session} closed" in caplog.text
    assert world.sessions.connected("ann@" + "f" * 16) is False
    assert world.sessions.connected("ann@viewer") is False
    assert SessionRegistry().connected(session) is False


def test_idle_transport_is_pruned(world):
    with running_app(app_for(world, session_idle_seconds=0.5)) as url:
        ann = open_connection(url + "/mcp", world.ann_key)
        session = answer(ann)["session"]
        assert poll(lambda: session not in world.sessions._by_session, deadline=15)
        assert world.sessions.connected(session) is False
        try:
            ann.close()
        except Exception:  # noqa: BLE001 - the server already ended the session
            pass


def test_prune_errors_never_stop_the_sweep(world, caplog):
    caplog.set_level(logging.INFO, logger="taskmaster.serve")
    second = []

    def boom(entry):
        raise ValueError("boom")

    world.sessions.on_disconnect.extend([boom, second.append])
    with running_app(app_for(world)) as url:
        ann = open_connection(url + "/mcp", world.ann_key)
        bob = open_connection(url + "/mcp", world.bob_key)
        session = answer(ann)["session"]
        answer(bob)
        ann.close()
        assert poll(lambda: [entry.session for entry in second] == [session])
        revoke_user(world.root, "bob")      # the revocation step still runs after a failed callback
        assert poll(lambda: not world.sessions.live())
        try:
            bob.close()
        except Exception:  # noqa: BLE001 - the server ended bob's session
            pass
    assert "on_disconnect callback failed: ValueError" in caplog.text


def test_viewer_channel_session(world, monkeypatch):
    assert world.sessions.current() is None
    with auth.viewer_principal(world.ann):
        bound = world.sessions.current()
    assert isinstance(bound, ChannelSession) and bound.session == "ann@viewer"
    bound.task = {"id": "x"}
    assert bound.task is None   # a viewer never keeps a current task
    with auth.viewer_principal("operator"):
        assert world.sessions.current().session == "operator@viewer"
    revoke_user(world.root, "bob")
    with auth.viewer_principal(world.bob), pytest.raises(PermissionError):
        world.sessions.current()

    def unavailable(key_id):
        raise RegistryUnavailable

    monkeypatch.setattr(world.registry, "lookup", unavailable)
    with auth.viewer_principal(world.ann), pytest.raises(PermissionError):
        world.sessions.current()
