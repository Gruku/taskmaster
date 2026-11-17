# User intent: over real HTTP, two teammates' keys write as two people, a revoked key stops working on an open session, and no key ever appears in logs, errors or files besides the client's.
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastmcp import Client as McpClient
from fastmcp.client.transports import StreamableHttpTransport
from mcp.types import Implementation

from serve_helpers import IssuedKey, call_tool
from taskmaster.coordinator import client as client_module
from taskmaster.coordinator import identity_gate
from taskmaster.coordinator.actor_resolver import Origin
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.ownership import ownership_held
from taskmaster.serve import auth, principals
from taskmaster.serve.config import ServeConfig, ServeRefused
from taskmaster.serve.daemon import ServeDaemon
from taskmaster.serve.principals import KEY_RE, add_user, principals_path, revoke_user
from taskmaster.team_identity import IdentityRefused
from test_native_service_build import root  # noqa: F401
from test_native_service_identity import _value

ADD = {"title": "x", "epic": "test-epic", "phase": "dev"}


@pytest.fixture
def launch(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    monkeypatch.setenv("TASKMASTER_SERVICE_IDLE_SECONDS", "0.5")
    started = []

    def start(team=True, users=("ann", "bob"), **settings):
        """`settings` are extra `ServeConfig` fields for this test's daemon (e.g. a sweep's rate limits)."""
        if team:
            monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root_, *, project_doc=None: True)
        keys = {}
        for handle in users:
            key, record, _ = add_user(root, handle, name=f"{handle.title()} Person", emails=[f"{handle}@example.com"])
            keys[handle] = IssuedKey(key=key, id=record.key_id, secret=KEY_RE.fullmatch(key).group(2))
        out = io.StringIO()
        daemon = ServeDaemon(ServeConfig(root=root, port=0, **settings), stdout=out).start()
        codes = []
        thread = threading.Thread(target=lambda: codes.append(daemon.serve()), daemon=True)
        thread.start()
        assert daemon.ready.wait(30)
        world = SimpleNamespace(daemon=daemon, url=daemon.url, out=out, root=root, keys=keys, thread=thread,
                                operator=(root / ".taskmaster/local/serve/operator.token").read_text().strip())
        started.append(world)
        return world
    yield start
    for world in started:
        world.daemon.request_stop("test teardown")
        world.thread.join(30)
        assert not world.thread.is_alive()


def serve_log(root_):
    return (root_ / ".taskmaster/local/serve/serve.log").read_text(encoding="utf-8")


def events(root_, where="1=1"):
    rows = _value(root_, f"SELECT kind, id, op, actor FROM domain_events WHERE {where} ORDER BY seq")
    return [(kind, ident, op, json.loads(actor) if actor else None) for kind, ident, op, actor in rows]


def test_two_keys_write_as_two_people(launch):
    w = launch()
    a1 = call_tool(w.url, w.keys["ann"].key, "backlog_add_task", dict(ADD, title="A1"))
    b1 = call_tool(w.url, w.keys["bob"].key, "backlog_add_task", dict(ADD, title="B1"))
    assert "[seq " in a1 and "[seq " in b1, (a1, b1)
    created = [e for e in events(w.root) if e[0] == "task" and e[2] == "create" and e[3] is not None]
    assert [(e[3]["handle"], e[3]["clone_id"]) for e in created] == [
        ("ann", w.keys["ann"].id), ("bob", w.keys["bob"].id)]
    assert {e[1] for e in events(w.root) if e[0] == "profile"} >= {"ann", "bob"}


def test_a_client_sent_handle_is_ignored_over_http(launch):
    w = launch()

    async def go():
        transport = StreamableHttpTransport(w.url, headers={"X-Taskmaster-Handle": "bob", "X-Forwarded-User": "bob"},
                                            auth=w.keys["ann"].key)
        async with McpClient(transport, client_info=Implementation(name="bob", version="1")) as client:
            return (await client.call_tool("backlog_add_task", dict(ADD, title="Sneaky"))).content[0].text
    assert "[seq " in asyncio.run(go())
    [created] = [e for e in events(w.root) if e[0] == "task" and e[2] == "create" and e[3] is not None]
    assert created[3]["handle"] == "ann" and created[3]["clone_id"] == w.keys["ann"].id


def test_operator_token_cannot_write_as_a_person_in_team_mode(launch):
    w = launch()
    before = call_tool(w.url, w.operator, "backlog_list_tasks")
    answer = call_tool(w.url, w.operator, "backlog_add_task", dict(ADD, title="Nope"))
    assert "add-user" in answer and "[seq " not in answer
    assert call_tool(w.url, w.operator, "backlog_list_tasks") == before


def test_a_revoked_key_is_refused_on_an_open_session_over_http(launch):
    w = launch()
    ann = w.keys["ann"]

    async def go():
        outcome = {}
        try:
            async with McpClient(w.url, auth=ann.key) as client:
                assert "[seq " in (await client.call_tool("backlog_add_task", dict(ADD, title="Before"))).content[0].text
                revoke_user(w.root, "ann")
                try:
                    await client.call_tool("backlog_add_task", dict(ADD, title="After"))
                    outcome["after"] = "written"
                except Exception:  # noqa: BLE001
                    outcome["after"] = "refused"
        except Exception:  # noqa: BLE001 - closing the session with the revoked key is refused too
            pass
        return outcome
    assert asyncio.run(go()) == {"after": "refused"}
    created = [e for e in events(w.root) if e[0] == "task" and e[2] == "create" and e[3] is not None]
    assert len(created) == 1
    assert f"refused key {ann.id}: revoked" in serve_log(w.root)


def test_a_key_added_while_running_works_without_a_restart(launch):
    w = launch()
    cy, _, _ = add_user(w.root, "cy")
    assert not call_tool(w.url, cy, "backlog_list_tasks").startswith("Error")


def test_a_key_never_appears_in_logs_or_errors(launch, caplog, capfd):
    w = launch()
    ann, bob = w.keys["ann"], w.keys["bob"]
    caplog.set_level(logging.DEBUG)
    texts = []

    def keep(response):
        texts.append(f"{response.status_code} {dict(response.headers)} {response.text}")
    headers = lambda token, **more: {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
                                     "Content-Type": "application/json", **more}
    texts.append(call_tool(w.url, ann.key, "backlog_add_task", dict(ADD, title="Quiet")))
    texts.append(call_tool(w.url, bob.key, "backlog_list_tasks"))
    wrong = f"tm_u_{ann.id}_" + ("A" if ann.secret[0] != "A" else "B") + ann.secret[1:]
    keep(httpx.post(w.url, headers=headers(wrong), json={}))
    revoke_user(w.root, "bob")
    keep(httpx.post(w.url, headers=headers(bob.key), json={}))
    keep(httpx.post(w.url, headers=headers("tm_u_zz"), json={}))
    keep(httpx.post(w.url.rsplit("/mcp", 1)[0] + "/other", headers=headers(ann.key), json={}))
    keep(httpx.post(w.url, headers=headers(ann.key, Host="evil.example"), json={}))
    source = w.daemon.coordinator.team_identity.source
    with pytest.raises(IdentityRefused) as refused:
        source.record(Origin(principal=bob.id))
    texts.append(str(refused.value))
    time.sleep(0.3)
    db = " ".join(str(r) for r in _value(w.root, "SELECT outcome_json FROM command_receipts"))
    db += " ".join(str(r) for r in _value(w.root, "SELECT actor, tool FROM domain_events"))
    sinks = {"caplog": caplog.text, "serve.log": serve_log(w.root), "capfd": "".join(capfd.readouterr()),
             "ready": w.out.getvalue(), "http": "\n".join(texts), "principals": principals_path(w.root).read_text(),
             "db": db}
    for who in (ann, bob):
        for name, text in sinks.items():
            assert who.key not in text, (who.id, name)
            assert who.secret not in text, (who.id, name)


def test_unsafe_principals_file_refuses_start(root, monkeypatch):  # noqa: F811
    add_user(root, "ann")
    real = principals.ownership.verify_private

    def verify(path):
        if getattr(path, "name", "") == principals.PRINCIPALS_FILE:
            raise PermissionError("too open")
        return real(path)
    monkeypatch.setattr(principals.ownership, "verify_private", verify)
    with pytest.raises(ServeRefused) as exc:
        ServeDaemon(ServeConfig(root=root, port=0), stdout=io.StringIO()).start()
    assert exc.value.exit_code == 2
    assert ownership_held(root) is False


def test_principals_file_breaking_at_runtime_fails_closed(launch):
    w = launch()
    assert not call_tool(w.url, w.keys["ann"].key, "backlog_list_tasks").startswith("Error")
    principals_path(w.root).write_bytes(b"{")
    with pytest.raises(httpx.HTTPStatusError) as exc:
        call_tool(w.url, w.keys["ann"].key, "backlog_list_tasks")
    assert exc.value.response.status_code == 401
    assert not call_tool(w.url, w.operator, "backlog_list_tasks").startswith("Error")
    assert "refusing every member key" in serve_log(w.root)


@pytest.mark.parametrize("team", [False, True])
def test_team_mode_off_is_warned(launch, team):
    w = launch(team=team)
    assert ("team mode is off" in serve_log(w.root)) is (not team)


def test_concurrent_mcp_and_viewer_writes_stamp_their_own_key(launch):
    w = launch()
    ann, bob = w.keys["ann"], w.keys["bob"]
    assert client_module.ORIGIN_PROVIDER is auth.request_key_id
    store_id = Client(w.root, autostart=False).identity["store_id"]
    for n in range(20):
        before = _value(w.root, "SELECT COALESCE(MAX(seq),0) FROM domain_events")[0][0]
        barrier, errors = threading.Barrier(2), []

        def mcp():
            try:
                barrier.wait(30)
                call_tool(w.url, ann.key, "backlog_add_task", dict(ADD, title=f"M{n}"))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def viewer():
            try:
                with auth.viewer_principal(bob.id):
                    barrier.wait(30)
                    Client(w.root, autostart=False).execute({
                        "protocol": 2, "store_id": store_id, "caller_scope": "bob@viewer", "request_id": f"v{n}",
                        "operation": "task.patch", "arguments": {"id": "test-epic-001", "set": {"title": f"V{n}"}},
                        "expected_revisions": []})
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=mcp), threading.Thread(target=viewer)]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
        assert not errors, errors
        for kind, ident, op, actor_ in events(w.root, f"seq>{before}"):
            assert actor_["handle"] in ("ann", "bob"), (kind, ident, op, actor_)
            if kind == "task" and op == "create":
                assert actor_["clone_id"] == ann.id
            elif kind == "task":
                assert actor_["clone_id"] == bob.id
    assert client_module.ORIGIN_PROVIDER is auth.request_key_id
    assert auth.VIEWER_KEY_ID.get() is None


def test_origin_provider_is_restored(launch):
    assert client_module.ORIGIN_PROVIDER is None
    w = launch()
    assert client_module.ORIGIN_PROVIDER is auth.request_key_id
    w.daemon.request_stop("test")
    w.thread.join(30)
    assert client_module.ORIGIN_PROVIDER is None


def test_ready_line_names_the_principals_file(launch):
    w = launch()
    assert json.loads(w.out.getvalue().splitlines()[0])["principals_file"].endswith("principals.json")
